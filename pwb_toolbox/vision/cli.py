"""Command line front end: ``python -m pwb_toolbox.vision``.

    python -m pwb_toolbox.vision chart.png --prompt "Read the levels off this"
    python -m pwb_toolbox.vision page1.png page2.png --no-downscale --no-stream

Answers stream to stdout by default so a long read appears as it is written.
``--verbose`` reports what each image became on the wire, which is the fastest
way to see that a screenshot was silently resized to fit the payload limit.
"""

import sys

import click

from .client import (
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    MissingApiKey,
    NvidiaError,
    VisionClient,
)
from .images import DEFAULT_MAX_INLINE_CHARS, ImageTooLarge, UnsupportedImage


@click.command()
@click.argument("images", nargs=-1, required=True)
@click.option(
    "--prompt",
    default=DEFAULT_PROMPT,
    show_default=True,
    help="What to ask about the images.",
)
@click.option(
    "--model", default=DEFAULT_MODEL, show_default=True, help="Catalog model id."
)
@click.option("--max-tokens", type=int, default=16384, show_default=True)
@click.option("--temperature", type=float, default=1.0, show_default=True)
@click.option(
    "--seed",
    type=int,
    default=None,
    help="Fix the sampling seed for a repeatable answer.",
)
@click.option(
    "--reasoning-effort",
    type=click.Choice(["low", "medium", "high", "max"]),
    default=None,
    help="Ask a reasoning model to think harder. Costs tokens and latency.",
)
@click.option("--stream/--no-stream", default=True, show_default=True)
@click.option(
    "--show-reasoning",
    is_flag=True,
    help="Also print the model's working, to stderr. Streaming only.",
)
@click.option(
    "--downscale/--no-downscale",
    default=True,
    show_default=True,
    help="Shrink an oversized image to fit. Turn OFF for scans whose small print matters.",
)
@click.option(
    "--max-inline-chars", type=int, default=DEFAULT_MAX_INLINE_CHARS, show_default=True
)
@click.option(
    "--verbose", is_flag=True, help="Report what each image became on the wire."
)
def cli(
    images,
    prompt,
    model,
    max_tokens,
    temperature,
    seed,
    reasoning_effort,
    stream,
    show_reasoning,
    downscale,
    max_inline_chars,
    verbose,
):
    """Ask an NVIDIA vision model about IMAGES (paths or http(s) URLs)."""
    params = {"max_tokens": max_tokens, "temperature": temperature}
    if seed is not None:
        params["seed"] = seed
    if reasoning_effort is not None:
        params["reasoning_effort"] = reasoning_effort

    try:
        client = VisionClient(
            model=model,
            max_inline_chars=max_inline_chars,
            downscale=downscale,
        )
    except MissingApiKey as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        if verbose:
            for reference, ready in zip(images, client.prepare_images(list(images))):
                if ready.is_remote:
                    click.echo(f"{reference}: sent as a URL", err=True)
                else:
                    click.echo(
                        f"{reference}: {ready.media_type}, "
                        f"{ready.encoded_chars:,} base64 chars, "
                        f"scale {ready.scale:.2f}",
                        err=True,
                    )

        if stream:
            for channel, text in client.events(prompt, list(images), **params):
                if channel == "content":
                    click.echo(text, nl=False)
                elif show_reasoning:
                    click.echo(text, nl=False, err=True)
            click.echo()
        else:
            click.echo(client.describe(list(images), prompt=prompt, **params))
    except (ImageTooLarge, UnsupportedImage, FileNotFoundError) as exc:
        raise click.ClickException(str(exc)) from exc
    except NvidiaError as exc:
        click.echo(exc.body[:2000], err=True)
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli())
