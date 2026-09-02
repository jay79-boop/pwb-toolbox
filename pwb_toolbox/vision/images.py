"""Turn an image reference into the ``image_url`` part a chat request carries.

Three shapes of reference arrive here and they are handled differently:

* an ``http(s)`` URL is passed through untouched -- the endpoint fetches it,
  so nothing is read or encoded locally
* a path on disk is read, sniffed for its media type and inlined as a
  ``data:`` URI
* raw ``bytes`` are inlined the same way, with the type sniffed from the
  leading magic bytes

**The size cap is on the base64 text, not on the file.** NVIDIA documents an
inline limit of roughly 180,000 *characters*, and base64 inflates by 4/3, so a
135 KB screenshot is already at the ceiling while the file listing still says
it is comfortably small. Checking ``len(data)`` instead of ``len(encoded)``
passes locally and fails against the API, which is why :func:`prepare` measures
the encoded string and reports it back.
"""

import base64
import os
import pathlib

#: NVIDIA's documented ceiling for an inline base64 payload, in characters of
#: encoded text. Conservative: some endpoints accept more. Every entry point
#: takes an override rather than treating this as a fact about all of them.
DEFAULT_MAX_INLINE_CHARS = 180_000

#: Extension -> media type. Only consulted when the magic bytes say nothing.
EXTENSION_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

#: Leading bytes -> media type, which beats the extension when both are known.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


class UnsupportedImage(ValueError):
    """Raised when the media type of an image cannot be established."""


class ImageTooLarge(ValueError):
    """Raised when an inlined image exceeds the endpoint's payload limit."""


class PreparedImage:
    """One image, encoded and measured.

    ``url`` is what goes on the wire -- either the original http(s) URL or a
    ``data:`` URI. ``encoded_chars`` is the length actually counted against the
    limit, and ``scale`` records how much the pixels were shrunk to get there
    (``1.0`` when nothing was resized, ``None`` for a remote URL).
    """

    __slots__ = ("url", "media_type", "encoded_chars", "scale")

    def __init__(self, url, media_type=None, encoded_chars=0, scale=None):
        self.url = url
        self.media_type = media_type
        self.encoded_chars = encoded_chars
        self.scale = scale

    @property
    def is_remote(self):
        return self.scale is None

    def part(self):
        """The request fragment for this image."""
        return {"type": "image_url", "image_url": {"url": self.url}}

    def __repr__(self):  # pragma: no cover - debugging aid
        if self.is_remote:
            return f"PreparedImage(remote, url={self.url!r})"
        return (
            f"PreparedImage(media_type={self.media_type!r}, "
            f"encoded_chars={self.encoded_chars}, scale={self.scale:.3f})"
        )


def is_remote(reference):
    """True when ``reference`` is a URL the endpoint should fetch itself."""
    if not isinstance(reference, str):
        return False
    lowered = reference.strip().lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def sniff(data, hint=None):
    """The media type of ``data``, preferring the magic bytes over ``hint``.

    ``hint`` is a filename or extension. It is only consulted when the content
    itself is unrecognised, because a ``.png`` holding a JPEG is a real thing
    that a screenshot pipeline produces and the endpoint reads the bytes.
    """
    for prefix, media in _MAGIC:
        if data.startswith(prefix):
            return media
    # RIFF....WEBP -- the four size bytes sit between the two markers.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if hint:
        suffix = os.path.splitext(str(hint))[1].lower() or str(hint).lower()
        media = EXTENSION_TYPES.get(suffix)
        if media:
            return media
    raise UnsupportedImage(
        "could not tell what kind of image this is; supported types are "
        + ", ".join(sorted(set(EXTENSION_TYPES.values())))
    )


def data_uri(data, media_type):
    """``data:`` URI for ``data``, and the length of its base64 payload."""
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{encoded}", len(encoded)


def _candidate_types(media_type):
    """Formats to try at a given size, losslessly-preferred first."""
    seen, out = set(), []
    for candidate in (media_type, "image/jpeg"):
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def _encode(image, media_type):
    """``image`` as ``media_type`` bytes, or None when Pillow refuses."""
    import io

    frame = image
    if media_type == "image/jpeg" and frame.mode not in ("RGB", "L"):
        frame = frame.convert("RGB")
    options = {"quality": 90} if media_type in ("image/jpeg", "image/webp") else {}
    buffer = io.BytesIO()
    try:
        frame.save(buffer, format=_pillow_format(media_type), **options)
    except (KeyError, OSError, ValueError):
        return None
    return buffer.getvalue()


def _shrink(data, media_type, max_chars, steps=8):
    """Re-encode ``data`` smaller until its base64 fits, or give up.

    Returns ``(bytes, media_type, scale)``, or None when it cannot be done.
    Pillow is optional here -- it arrives with matplotlib rather than being
    required outright -- so its absence is reported as "cannot shrink" rather
    than crashing a call that had a perfectly good URL alternative.

    Each step takes 85% of the previous *linear* dimension, so eight steps
    cover roughly a 14x reduction in area. At each size the original format is
    tried before JPEG, so a PNG only becomes lossy once staying lossless has
    failed -- and the returned media type says so when it does.
    """
    try:
        import io

        from PIL import Image
    except ImportError:
        return None

    try:
        original = Image.open(io.BytesIO(data))
        original.load()
    except Exception:
        return None

    scale = 1.0
    for _ in range(steps):
        scale *= 0.85
        width = max(1, int(original.width * scale))
        height = max(1, int(original.height * scale))
        resized = original.resize((width, height), Image.LANCZOS)
        for out_type in _candidate_types(media_type):
            candidate = _encode(resized, out_type)
            # 4 characters of base64 per 3 bytes, so measure the encoding.
            if candidate is not None and len(base64.b64encode(candidate)) <= max_chars:
                return candidate, out_type, scale
    return None


def _pillow_format(media_type):
    return {
        "image/png": "PNG",
        "image/jpeg": "JPEG",
        "image/webp": "WEBP",
        "image/gif": "GIF",
        "image/bmp": "BMP",
    }[media_type]


def prepare(reference, max_inline_chars=DEFAULT_MAX_INLINE_CHARS, downscale=True):
    """A :class:`PreparedImage` for a URL, a path, or raw bytes.

    ``downscale`` is worth a thought rather than a default you inherit: shrinking
    a chart screenshot costs nothing legible, and shrinking a scanned statement
    can cost the digits. Pass ``downscale=False`` for documents and handle
    :class:`ImageTooLarge` by splitting or re-scanning instead.
    """
    if is_remote(reference):
        return PreparedImage(reference.strip())

    if isinstance(reference, (bytes, bytearray)):
        data, hint = bytes(reference), None
    else:
        path = pathlib.Path(reference)
        if not path.is_file():
            raise FileNotFoundError(f"no such image: {path}")
        data, hint = path.read_bytes(), path.name

    media_type = sniff(data, hint)
    uri, chars = data_uri(data, media_type)
    if chars <= max_inline_chars:
        return PreparedImage(uri, media_type, chars, 1.0)

    if downscale:
        smaller = _shrink(data, media_type, max_inline_chars)
        if smaller is not None:
            data, media_type, scale = smaller
            uri, chars = data_uri(data, media_type)
            return PreparedImage(uri, media_type, chars, scale)

    raise ImageTooLarge(
        f"image encodes to {chars:,} base64 characters, over the "
        f"{max_inline_chars:,} the endpoint accepts inline"
        + (
            " and it could not be shrunk further"
            if downscale
            else "; pass downscale=True, resize it yourself, or host it and "
            "pass the URL"
        )
    )


def image_part(reference, max_inline_chars=DEFAULT_MAX_INLINE_CHARS, downscale=True):
    """The ``image_url`` request fragment for one image reference."""
    return prepare(reference, max_inline_chars, downscale).part()
