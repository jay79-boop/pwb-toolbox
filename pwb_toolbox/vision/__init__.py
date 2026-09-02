"""Read images with NVIDIA's hosted vision-language models.

Built for the three kinds of picture this desk produces that no CSV covers: a
chart screenshot, a trade-journal shot, and a statement or filing that arrived
as a scan rather than an export.

    from pwb_toolbox.vision import VisionClient

    client = VisionClient()
    print(client.describe("chart.png", prompt="Read the levels off this chart"))

The key comes from ``NVIDIA_API_KEY``. Everything network-facing goes through
an injectable ``session``, so the tests run with no key and no network.

Two things bite on the first attempt and both are handled here rather than
documented and forgotten: a ``"Bearer $NVIDIA_API_KEY"`` string does not expand
in Python, and the inline image limit counts base64 *characters*, which a file
of three-quarters that size already exceeds. See :mod:`pwb_toolbox.vision.client`
and :mod:`pwb_toolbox.vision.images`.
"""

from .client import (
    API_KEY_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    MissingApiKey,
    NvidiaError,
    VisionClient,
    completion_text,
    resolve_api_key,
    stream_events,
    text_part,
    user_message,
)
from .images import (
    DEFAULT_MAX_INLINE_CHARS,
    EXTENSION_TYPES,
    ImageTooLarge,
    PreparedImage,
    UnsupportedImage,
    data_uri,
    image_part,
    is_remote,
    prepare,
    sniff,
)

__all__ = [
    "API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_INLINE_CHARS",
    "DEFAULT_MODEL",
    "DEFAULT_PROMPT",
    "EXTENSION_TYPES",
    "ImageTooLarge",
    "MissingApiKey",
    "NvidiaError",
    "PreparedImage",
    "UnsupportedImage",
    "VisionClient",
    "completion_text",
    "data_uri",
    "image_part",
    "is_remote",
    "prepare",
    "resolve_api_key",
    "sniff",
    "stream_events",
    "text_part",
    "user_message",
]
