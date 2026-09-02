"""Read an image with a vision model on NVIDIA's hosted API.

NVIDIA publishes a copy-paste snippet for this endpoint. Four things in it are
wrong or incomplete in ways that only show up as a failed request, and this
module exists so none of them has to be rediscovered:

**The key is a string literal, not a shell expansion.** The published example
writes ``"Authorization": "Bearer $NVIDIA_API_KEY"``. Python does not
interpolate ``$VAR`` inside a string, so that header goes out with the dollar
sign and the variable name in it and the API answers 401. Here the key is read
from the ``NVIDIA_API_KEY`` environment variable, and a missing or
still-unexpanded one raises *before* a request is built rather than after.

**An inline image is capped.** The hosted endpoint accepts a base64 image
embedded in the request body only up to :data:`MAX_INLINE_BYTES`; past that the
image has to go through NVIDIA's asset upload instead. Rather than fail at that
edge, :func:`image_part` downscales until the encoding fits and says by how
much. The cap is a constant and a ``--max-bytes`` flag because it is NVIDIA's
number, not ours, and it can move.

**Charts are line art, so the shrink ladder is PNG-first.** JPEG at the quality
needed to hit a size target smears thin candle wicks and axis text, which is
exactly the content a chart read depends on. :func:`shrink_to_fit` therefore
scales the image down as PNG first and only falls back to JPEG when no scale
above ``min_side`` fits -- a photograph degrades gracefully that way, a chart
does not.

**A stream is not JSON.** With ``stream=True`` the response is
``text/event-stream``: ``data:`` lines carrying JSON fragments and a final
``data: [DONE]``. Calling ``.json()`` on it fails. Reasoning models additionally
split their output across two delta keys -- ``reasoning_content`` for the
thinking and ``content`` for the answer -- and gluing them together produces a
reply with the model's scratchpad pasted on the front. :func:`collect_stream`
keeps them apart.

Usage::

    python tools/nvidia_vision.py ask chart.png --prompt "What is in this image?"
    python tools/nvidia_vision.py ask https://example.com/x.jpg --stream
    python tools/nvidia_vision.py chart NQ=F --keep out.png

The image sources accepted anywhere are a local path, an ``http(s)://`` URL, or
a ``data:`` URI already built. Only local files are ever resized -- a remote URL
is handed to NVIDIA untouched, because the bytes never pass through here.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import mimetypes
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import requests

#: The hosted chat-completions endpoint.
INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

#: Environment variable holding the API key. Get one at build.nvidia.com.
ENV_KEY = "NVIDIA_API_KEY"

#: Default model. Overridable everywhere -- NVIDIA's catalog changes often and
#: not every model on it accepts images, so a hard-coded id would go stale.
DEFAULT_MODEL = "moonshotai/kimi-k3"

#: Largest base64 payload NVIDIA accepts inline. Bigger images need their asset
#: upload API, which this module does not implement; it downscales instead.
MAX_INLINE_BYTES = 180_000

#: Status codes worth retrying: rate limiting and transient server failures.
#: Mirrors ``pwb_toolbox.scraping.polite.RETRY_STATUS``.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

#: Extensions the API understands, mapped to the mime type to declare.
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class NvidiaVisionError(RuntimeError):
    """Raised when the API returns something other than a usable answer."""


class MissingKey(NvidiaVisionError):
    """Raised when no usable API key is available."""


class ImageTooLarge(NvidiaVisionError):
    """Raised when an image cannot be shrunk under the inline cap."""


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------


def encoded_length(raw_bytes: int) -> int:
    """Length of the base64 encoding of ``raw_bytes`` bytes, without encoding."""
    return 4 * math.ceil(raw_bytes / 3)


def guess_mime(path: str | Path) -> str:
    """Mime type for ``path``, preferring the extensions the API documents."""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_TYPES:
        return IMAGE_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed and guessed.startswith("image/"):
        return guessed
    raise NvidiaVisionError(
        f"{path}: not a recognised image type "
        f"(expected one of {', '.join(sorted(IMAGE_TYPES))})"
    )


def data_uri(raw: bytes, mime: str) -> str:
    """A ``data:`` URI for ``raw``, which is how an image is embedded inline."""
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def shrink_to_fit(
    raw: bytes,
    limit: int = MAX_INLINE_BYTES,
    *,
    min_side: int = 320,
) -> tuple[bytes, str]:
    """Return ``(bytes, mime)`` whose base64 encoding fits within ``limit``.

    Scales down as PNG first so chart text and candle wicks survive, and only
    re-encodes as JPEG when no scale at or above ``min_side`` gets under the
    cap. Returns the input untouched when it already fits, so the common case
    costs nothing and needs no image library.
    """
    if encoded_length(len(raw)) <= limit:
        return raw, ""

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Pillow ships with matplotlib
        raise ImageTooLarge(
            f"image encodes to {encoded_length(len(raw)):,} bytes, over the "
            f"{limit:,}-byte inline cap, and Pillow is not installed to resize it"
        ) from exc

    image = Image.open(io.BytesIO(raw))
    image.load()

    def attempt(scale: float, fmt: str, **save_args: Any) -> bytes | None:
        width = max(int(image.width * scale), 1)
        height = max(int(image.height * scale), 1)
        if min(width, height) < min_side and scale < 1.0:
            return None
        resized = image if scale == 1.0 else image.resize((width, height))
        if fmt == "JPEG" and resized.mode not in ("RGB", "L"):
            resized = resized.convert("RGB")
        buffer = io.BytesIO()
        resized.save(buffer, format=fmt, **save_args)
        candidate = buffer.getvalue()
        return candidate if encoded_length(len(candidate)) <= limit else None

    ladder = (1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.25)
    for scale in ladder:
        fitted = attempt(scale, "PNG", optimize=True)
        if fitted is not None:
            return fitted, "image/png"

    for scale in ladder:
        for quality in (85, 70, 55, 40):
            fitted = attempt(scale, "JPEG", quality=quality, optimize=True)
            if fitted is not None:
                return fitted, "image/jpeg"

    raise ImageTooLarge(
        f"cannot get a {image.width}x{image.height} image under the "
        f"{limit:,}-byte inline cap without going below {min_side}px a side"
    )


def image_part(
    source: str | Path,
    *,
    max_bytes: int = MAX_INLINE_BYTES,
    resize: bool = True,
) -> dict[str, Any]:
    """Build one ``image_url`` content part from a path, URL or data URI.

    A remote URL is passed through untouched -- NVIDIA fetches it, the bytes
    never come through here, so there is nothing to measure or resize.
    """
    text = str(source)
    if text.startswith(("http://", "https://", "data:")):
        return {"type": "image_url", "image_url": {"url": text}}

    path = Path(text)
    if not path.is_file():
        raise NvidiaVisionError(f"{path}: no such file")

    mime = guess_mime(path)
    raw = path.read_bytes()

    if encoded_length(len(raw)) > max_bytes:
        if not resize:
            raise ImageTooLarge(
                f"{path}: encodes to {encoded_length(len(raw)):,} bytes, over the "
                f"{max_bytes:,}-byte inline cap (drop --no-resize to downscale)"
            )
        raw, shrunk_mime = shrink_to_fit(raw, max_bytes)
        mime = shrunk_mime or mime

    return {"type": "image_url", "image_url": {"url": data_uri(raw, mime)}}


def build_payload(
    prompt: str,
    images: Sequence[str | Path] = (),
    *,
    model: str = DEFAULT_MODEL,
    stream: bool = False,
    max_tokens: int = 16384,
    temperature: float = 1.0,
    seed: int | None = 0,
    reasoning_effort: str | None = None,
    max_bytes: int = MAX_INLINE_BYTES,
    resize: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the request body for one user turn of text plus images."""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for source in images:
        content.append(image_part(source, max_bytes=max_bytes, resize=resize))

    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    if seed is not None:
        payload["seed"] = seed
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# responses
# ---------------------------------------------------------------------------


@dataclass
class Answer:
    """One completion: the reply, the thinking behind it, and the raw body."""

    text: str = ""
    reasoning: str = ""
    model: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.text


def _decode(line: bytes | str) -> str:
    return line.decode("utf-8") if isinstance(line, bytes) else line


def iter_stream(lines: Iterable[bytes | str]) -> Iterator[dict[str, Any]]:
    """Yield the JSON object from each ``data:`` line of an SSE response.

    Blank keep-alive lines and the terminating ``[DONE]`` sentinel are dropped,
    and a fragment that is not valid JSON is skipped rather than killing the
    stream -- a truncated final chunk should not lose the text before it.
    """
    for line in lines:
        text = _decode(line).strip()
        if not text or not text.startswith("data:"):
            continue
        body = text[len("data:") :].strip()
        if not body or body == "[DONE]":
            continue
        try:
            yield json.loads(body)
        except json.JSONDecodeError:
            continue


def collect_stream(lines: Iterable[bytes | str]) -> Answer:
    """Accumulate a streamed response, keeping reasoning out of the answer."""
    answer = Answer()
    chunks: list[str] = []
    thoughts: list[str] = []
    seen: list[dict[str, Any]] = []

    for event in iter_stream(lines):
        seen.append(event)
        if isinstance(event.get("error"), (dict, str)):
            raise NvidiaVisionError(f"stream carried an error: {event['error']}")
        answer.model = event.get("model") or answer.model
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                chunks.append(piece)
            thought = delta.get("reasoning_content")
            if thought:
                thoughts.append(thought)
            if choice.get("finish_reason"):
                answer.finish_reason = choice["finish_reason"]
        if isinstance(event.get("usage"), dict):
            answer.usage = event["usage"]

    answer.text = "".join(chunks)
    answer.reasoning = "".join(thoughts)
    answer.raw = seen
    return answer


def parse_completion(payload: dict[str, Any]) -> Answer:
    """Pull the answer out of a non-streamed chat-completions body."""
    if "error" in payload:
        raise NvidiaVisionError(f"API returned an error: {payload['error']}")

    choices = payload.get("choices") or []
    if not choices:
        raise NvidiaVisionError(f"no choices in response: {payload}")

    message = choices[0].get("message") or {}
    content = message.get("content")
    # Some models answer with the same list-of-parts shape the request uses.
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )

    return Answer(
        text=content or "",
        reasoning=message.get("reasoning_content") or "",
        model=payload.get("model", ""),
        finish_reason=choices[0].get("finish_reason") or "",
        usage=payload.get("usage") or {},
        raw=payload,
    )


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


class VisionClient:
    """Calls the hosted endpoint, with the transport injected for testing.

    ``session`` is anything with ``requests.Session``'s ``post``; ``sleep`` is
    injectable for the same reason it is in ``PoliteSession`` -- so the retry
    path can be asserted without spending real seconds.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        session: Any | None = None,
        url: str = INVOKE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 120.0,
        max_retries: int = 3,
        backoff: float = 2.0,
        sleep=time.sleep,
        max_bytes: int = MAX_INLINE_BYTES,
    ):
        key = api_key if api_key is not None else os.environ.get(ENV_KEY, "")
        key = (key or "").strip()
        if not key:
            raise MissingKey(
                f"no API key: set {ENV_KEY} in the environment, or pass api_key=. "
                f"Get one at https://build.nvidia.com/"
            )
        if key.startswith("$") or key.startswith("${"):
            # The exact failure NVIDIA's own snippet ships with, arriving one
            # layer later: the placeholder got stored instead of expanded.
            raise MissingKey(
                f"{ENV_KEY} is {key!r}, which is an unexpanded shell variable "
                f"rather than a key. Nothing interpolates $VAR inside a Python "
                f"string -- export the real value instead."
            )

        self.api_key = key
        self.session = session or requests.Session()
        self.url = url
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self._sleep = sleep
        self.max_bytes = max_bytes

    def headers(self, stream: bool) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream" if stream else "application/json",
            "Content-Type": "application/json",
        }

    def post(self, payload: dict[str, Any]):
        """POST ``payload``, retrying the transient statuses. Returns the response."""
        stream = bool(payload.get("stream"))
        last = None
        for attempt in range(self.max_retries + 1):
            response = self.session.post(
                self.url,
                headers=self.headers(stream),
                json=payload,
                stream=stream,
                timeout=self.timeout,
            )
            if response.status_code < 400:
                return response
            last = response
            if response.status_code not in RETRY_STATUS or attempt == self.max_retries:
                break
            self._sleep(self.backoff * (2**attempt))

        body = _body_text(last)
        raise NvidiaVisionError(f"HTTP {last.status_code} from {self.url}: {body}")

    def ask(
        self,
        prompt: str,
        images: Sequence[str | Path] = (),
        *,
        stream: bool = False,
        model: str | None = None,
        on_chunk=None,
        **kwargs: Any,
    ) -> Answer:
        """Send one prompt plus images and return the :class:`Answer`.

        ``on_chunk`` is called with each text fragment as it streams, so a CLI
        can print progressively while the same call still returns the whole
        answer at the end.
        """
        payload = build_payload(
            prompt,
            images,
            model=model or self.model,
            stream=stream,
            max_bytes=kwargs.pop("max_bytes", self.max_bytes),
            **kwargs,
        )
        response = self.post(payload)

        if not stream:
            try:
                return parse_completion(response.json())
            except ValueError as exc:
                raise NvidiaVisionError(
                    f"response was not JSON: {_body_text(response)[:400]}"
                ) from exc

        if on_chunk is None:
            return collect_stream(response.iter_lines())
        return collect_stream(_tee(response.iter_lines(), on_chunk))


def _tee(lines: Iterable[bytes | str], on_chunk) -> Iterator[bytes | str]:
    """Pass SSE lines through, calling ``on_chunk`` with each text fragment."""
    for line in lines:
        text = _decode(line).strip()
        if text.startswith("data:"):
            body = text[len("data:") :].strip()
            if body and body != "[DONE]":
                try:
                    event = json.loads(body)
                except json.JSONDecodeError:
                    event = {}
                for choice in event.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        on_chunk(piece)
        yield line


def _body_text(response) -> str:
    if response is None:  # pragma: no cover - only reachable on a bug
        return "<no response>"
    try:
        return json.dumps(response.json())
    except Exception:
        return getattr(response, "text", "") or "<empty body>"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except NvidiaVisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
