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

    from pwb_toolbox.vision import VisionClient

    client = VisionClient()
    answer = client.ask("What is in this image?", ["chart.png"])

The image sources accepted anywhere are a local path, raw ``bytes``, an
``http(s)://`` URL, or a ``data:`` URI already built. Only local images are ever
resized -- a remote URL is handed to NVIDIA untouched, because the bytes never
pass through here.

``tools/nvidia_vision.py`` is the command line over this: ``ask``, ``models``,
and ``chart``, which renders the desk's own chart for a symbol before reading
it. The CLI lives there rather than here because it reaches for
``tools/desk_levels.py``, and the shipped package does not depend on the desk.
"""

from __future__ import annotations

import base64
import io
import json
import math
import mimetypes
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import requests

#: The hosted chat-completions endpoint.
INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

#: The catalog listing. ``models`` reads this to answer "does that id exist,
#: and does it take images?" without spending a completion.
MODELS_URL = "https://integrate.api.nvidia.com/v1/models"

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

#: Leading bytes for the same set, which beat the extension when both are
#: known. An extension is what a file is called; this is what it is.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


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


def sniff_mime(raw: bytes) -> str | None:
    """Mime type read from the leading bytes, or None if they say nothing.

    Worth doing before trusting the extension: a screenshot pipeline writing
    JPEG bytes into a ``.png`` is an ordinary thing to happen, and the label
    that then goes on the wire disagrees with the payload behind it.
    """
    for prefix, mime in _MAGIC:
        if raw.startswith(prefix):
            return mime
    # RIFF....WEBP -- four size bytes sit between the two markers.
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def guess_mime(path: str | Path, raw: bytes | None = None) -> str:
    """Mime type for an image, preferring what the bytes say over its name.

    ``raw`` is the file's content when it has already been read. It wins over
    the extension, which is a claim rather than evidence; the extension is the
    fallback for a name with no bytes behind it yet.
    """
    if raw:
        sniffed = sniff_mime(raw)
        if sniffed is not None:
            return sniffed
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
    source: str | Path | bytes,
    *,
    max_bytes: int = MAX_INLINE_BYTES,
    resize: bool = True,
) -> dict[str, Any]:
    """Build one ``image_url`` content part from a path, URL, data URI or bytes.

    A remote URL is passed through untouched -- NVIDIA fetches it, the bytes
    never come through here, so there is nothing to measure or resize.

    Raw ``bytes`` are accepted so a screenshot that was rendered in memory does
    not have to be written to a file and read back to be asked about.
    """
    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        mime = sniff_mime(raw)
        if mime is None:
            raise NvidiaVisionError(
                "these bytes are not an image this API reads "
                f"(expected one of {', '.join(sorted(set(IMAGE_TYPES.values())))})"
            )
        name = "<bytes>"
    else:
        text = str(source)
        if text.startswith(("http://", "https://", "data:")):
            return {"type": "image_url", "image_url": {"url": text}}

        path = Path(text)
        if not path.is_file():
            raise NvidiaVisionError(f"{path}: no such file")

        raw = path.read_bytes()
        mime = guess_mime(path, raw)
        name = str(path)

    if encoded_length(len(raw)) > max_bytes:
        if not resize:
            raise ImageTooLarge(
                f"{name}: encodes to {encoded_length(len(raw)):,} bytes, over the "
                f"{max_bytes:,}-byte inline cap (drop --no-resize to downscale)"
            )
        raw, shrunk_mime = shrink_to_fit(raw, max_bytes)
        mime = shrunk_mime or mime

    return {"type": "image_url", "image_url": {"url": data_uri(raw, mime)}}


def build_payload(
    prompt: str,
    images: Sequence[str | Path | bytes] = (),
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
        models_url: str = MODELS_URL,
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
        self.models_url = models_url
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

    def _send(self, send, url: str, stream: bool = False, **kwargs: Any):
        """Call ``send``, retrying the transient statuses. Returns the response."""
        last = None
        for attempt in range(self.max_retries + 1):
            response = send(
                url,
                headers=self.headers(stream),
                stream=stream,
                timeout=self.timeout,
                **kwargs,
            )
            if response.status_code < 400:
                return response
            last = response
            if response.status_code not in RETRY_STATUS or attempt == self.max_retries:
                break
            self._sleep(self.backoff * (2**attempt))

        raise NvidiaVisionError(
            f"HTTP {last.status_code} from {url}: {_body_text(last)}"
        )

    def post(self, payload: dict[str, Any]):
        """POST ``payload`` to the completions endpoint."""
        return self._send(
            self.session.post,
            self.url,
            stream=bool(payload.get("stream")),
            json=payload,
        )

    def list_models(self, contains: str | None = None) -> list[str]:
        """Every model id the catalog offers, optionally filtered by substring.

        The cheapest answer to "does that id exist" -- it costs no completion,
        and a name absent here is why a call would come back 400.
        """
        response = self._send(self.session.get, self.models_url)
        try:
            body = response.json()
        except ValueError as exc:
            raise NvidiaVisionError(
                f"catalog was not JSON: {_body_text(response)[:400]}"
            ) from exc
        ids = [entry.get("id", "") for entry in body.get("data") or []]
        if contains:
            needle = contains.lower()
            ids = [name for name in ids if needle in name.lower()]
        return sorted(name for name in ids if name)

    def ask(
        self,
        prompt: str,
        images: Sequence[str | Path | bytes] = (),
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
