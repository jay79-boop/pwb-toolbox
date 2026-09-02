"""Command line over :mod:`pwb_toolbox.vision`.

    python tools/nvidia_vision.py ask chart.png --prompt "What is in this image?"
    python tools/nvidia_vision.py ask https://example.com/x.jpg --stream
    python tools/nvidia_vision.py models --filter kimi
    python tools/nvidia_vision.py chart NQ=F --keep out.png

The client itself lives in the shipped package so anything can import it. This
half stays on the desk because ``chart`` reaches for ``tools/desk_levels.py``
to render the picture before reading it, and the package does not depend on the
desk.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pwb_toolbox.vision import (
    DEFAULT_MODEL,
    MAX_INLINE_BYTES,
    Answer,
    NvidiaVisionError,
    VisionClient,
)

CHART_PROMPT = (
    "This is a candlestick chart with market structure levels drawn on it. "
    "Describe what you see: the trend, where price sits relative to the marked "
    "levels, and any structure the drawing does not label. Do not give trading "
    "advice and do not invent numbers that are not legible on the chart."
)


def _client_from(args) -> VisionClient:
    return VisionClient(model=args.model, max_bytes=args.max_bytes)


def _ask_options(payload_args) -> dict[str, Any]:
    options: dict[str, Any] = {
        "max_tokens": payload_args.max_tokens,
        "temperature": payload_args.temperature,
        "reasoning_effort": payload_args.reasoning_effort,
    }
    options["seed"] = None if payload_args.seed < 0 else payload_args.seed
    return options


def _emit(answer: Answer, args, streamed: bool) -> None:
    if args.json:
        print(
            json.dumps(
                {
                    "text": answer.text,
                    "reasoning": answer.reasoning,
                    "model": answer.model,
                    "finish_reason": answer.finish_reason,
                    "usage": answer.usage,
                },
                indent=2,
            )
        )
        return
    if args.show_reasoning and answer.reasoning:
        print("--- reasoning ---", file=sys.stderr)
        print(answer.reasoning, file=sys.stderr)
        print("--- answer ---", file=sys.stderr)
    if not streamed:
        print(answer.text)
    elif not answer.text.endswith("\n"):
        print()  # the stream left the cursor mid-line


def _printer(args):
    """Stream fragments to stdout, unless --json wants the body uninterrupted."""
    if not args.stream or args.json:
        return None

    def on_chunk(piece: str) -> None:
        sys.stdout.write(piece)
        sys.stdout.flush()

    return on_chunk


def cmd_ask(args) -> int:
    client = _client_from(args)
    streamed = args.stream
    on_chunk = _printer(args)

    answer = client.ask(
        args.prompt,
        args.images,
        stream=streamed,
        on_chunk=on_chunk,
        resize=not args.no_resize,
        **_ask_options(args),
    )
    _emit(answer, args, streamed)
    return 0


def cmd_chart(args) -> int:
    """Render the desk's own chart for a symbol, then have the model read it."""
    from tools import desk_levels

    bars = desk_levels.fetch_bars(
        args.symbol, interval=args.interval, period=args.period
    )
    struct = desk_levels.structure(
        bars, args.symbol, tz=args.tz or desk_levels.DEFAULT_TZ
    )
    out = Path(args.keep) if args.keep else Path(f"{args.symbol}-chart.png")
    desk_levels.render(bars, struct, str(out), title=args.symbol, bars_shown=args.bars)

    client = _client_from(args)
    streamed = args.stream
    on_chunk = _printer(args)

    answer = client.ask(
        args.prompt,
        [out],
        stream=streamed,
        on_chunk=on_chunk,
        resize=True,
        **_ask_options(args),
    )
    _emit(answer, args, streamed)
    if not args.keep:
        out.unlink(missing_ok=True)
    elif not args.json:
        print(f"chart kept at {out}", file=sys.stderr)
    return 0


def cmd_models(args) -> int:
    client = VisionClient(model=DEFAULT_MODEL, max_bytes=MAX_INLINE_BYTES)
    names = client.list_models(args.filter)
    if args.json:
        print(json.dumps(names, indent=2))
        return 0
    for name in names:
        print(name)
    if not names:
        where = f" matching {args.filter!r}" if args.filter else ""
        print(f"no models{where} in the catalog", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--model", default=DEFAULT_MODEL, help="NVIDIA catalog model id")
        p.add_argument("--stream", action="store_true", help="print as it arrives")
        p.add_argument("--max-tokens", type=int, default=16384)
        p.add_argument("--temperature", type=float, default=1.0)
        p.add_argument(
            "--seed", type=int, default=0, help="negative to omit the seed entirely"
        )
        p.add_argument(
            "--reasoning-effort",
            default=None,
            help="passed through; models that do not accept it will 400",
        )
        p.add_argument("--max-bytes", type=int, default=MAX_INLINE_BYTES)
        p.add_argument("--json", action="store_true", help="emit the full result")
        p.add_argument("--show-reasoning", action="store_true")
        return p

    ask = common(sub.add_parser("ask", help="ask about one or more images"))
    ask.add_argument("images", nargs="*", help="local paths, http(s) URLs or data URIs")
    ask.add_argument("--prompt", default="What is in this image?")
    ask.add_argument(
        "--no-resize",
        action="store_true",
        help="fail rather than downscale an over-cap image",
    )
    ask.set_defaults(func=cmd_ask)

    chart = common(sub.add_parser("chart", help="render a desk chart, then read it"))
    chart.add_argument("symbol")
    chart.add_argument("--interval", default="15m")
    chart.add_argument("--period", default="1mo")
    chart.add_argument("--tz", default=None, help="defaults to desk_levels.DEFAULT_TZ")
    chart.add_argument("--bars", type=int, default=120)
    chart.add_argument("--prompt", default=CHART_PROMPT)
    chart.add_argument("--keep", default=None, help="path to keep the PNG at")
    chart.set_defaults(func=cmd_chart)

    models = sub.add_parser("models", help="list the catalog: does that id exist?")
    models.add_argument("--filter", default=None, help="substring to match, e.g. kimi")
    models.add_argument("--json", action="store_true")
    models.set_defaults(func=cmd_models)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except NvidiaVisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
