"""Read an image with a vision model on NVIDIA's hosted API.

Built for the three kinds of picture this desk produces that no CSV covers: a
chart screenshot, a trade-journal shot, and a statement or filing that arrived
as a scan rather than an export.

    from pwb_toolbox.vision import VisionClient

    client = VisionClient()
    answer = client.ask("Read the session levels off this", ["chart.png"])

The key comes from ``NVIDIA_API_KEY``. Nothing in the test suite reads it: the
transport is injectable, so the tests run with no key and no network.

:mod:`pwb_toolbox.vision.nvidia` holds the implementation and, at its head, the
four traps in NVIDIA's published snippet that this exists to absorb -- a key
that never expands, an inline size cap measured after base64 inflation, a
shrink ladder that has to stay on PNG or chart text smears, and a stream whose
reasoning arrives on a different delta key from its answer.

``tools/nvidia_vision.py`` is the command line over this.
"""

from .nvidia import (
    DEFAULT_MODEL,
    ENV_KEY,
    IMAGE_TYPES,
    INVOKE_URL,
    MAX_INLINE_BYTES,
    MODELS_URL,
    RETRY_STATUS,
    Answer,
    ImageTooLarge,
    MissingKey,
    NvidiaVisionError,
    VisionClient,
    build_payload,
    collect_stream,
    data_uri,
    encoded_length,
    guess_mime,
    image_part,
    iter_stream,
    parse_completion,
    shrink_to_fit,
    sniff_mime,
)

__all__ = [
    "Answer",
    "DEFAULT_MODEL",
    "ENV_KEY",
    "IMAGE_TYPES",
    "INVOKE_URL",
    "ImageTooLarge",
    "MAX_INLINE_BYTES",
    "MODELS_URL",
    "MissingKey",
    "NvidiaVisionError",
    "RETRY_STATUS",
    "VisionClient",
    "build_payload",
    "collect_stream",
    "data_uri",
    "encoded_length",
    "guess_mime",
    "image_part",
    "iter_stream",
    "parse_completion",
    "shrink_to_fit",
    "sniff_mime",
]
