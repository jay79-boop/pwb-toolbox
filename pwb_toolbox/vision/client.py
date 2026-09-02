"""A client for NVIDIA's hosted vision-language models.

The endpoint speaks the OpenAI chat-completions dialect, so the interesting
parts are the three places a first attempt goes wrong:

* **The key does not expand itself.** NVIDIA's catalog page shows
  ``"Authorization": "Bearer $NVIDIA_API_KEY"``, which is a shell idiom pasted
  into Python: the string is sent literally and the call comes back 401 with
  nothing to suggest why. :func:`resolve_api_key` reads the environment and
  refuses a value that still looks like an unexpanded variable.
* **Streaming is server-sent events, not JSON.** Lines arrive as ``data: {...}``
  with a ``data: [DONE]`` sentinel, keep-alive blanks in between, and a partial
  line at any buffer boundary. Anything that is not JSON is skipped rather than
  raised on.
* **Reasoning arrives on its own channel.** A model asked for
  ``reasoning_effort`` puts its working in ``delta.reasoning_content`` and its
  answer in ``delta.content``. Concatenating both gives you an answer with the
  scratch work glued to the front, so :meth:`VisionClient.events` keeps them
  labelled and the plain-text helpers return only the answer.

``session`` is injectable so the tests exercise all of that against a fake
transport, with no network and no API key.
"""

import json
import os

from .images import DEFAULT_MAX_INLINE_CHARS, prepare

#: The OpenAI-compatible root for NVIDIA's hosted catalog models.
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

#: A vision-language model on the catalog. Overridable everywhere; pinned here
#: so the default is a stated choice rather than whatever the caller had open.
DEFAULT_MODEL = "moonshotai/kimi-k3"

#: The environment variable the key is read from.
API_KEY_ENV = "NVIDIA_API_KEY"

DEFAULT_PROMPT = "What is in this image?"


class MissingApiKey(RuntimeError):
    """Raised when no usable API key was supplied or found in the environment."""


class NvidiaError(RuntimeError):
    """Raised when the endpoint answers with something other than a completion.

    Carries ``status`` and ``body`` because the useful half of an NVIDIA error
    is in the response body, and a bare ``raise_for_status`` throws it away.
    """

    def __init__(self, message, status=None, body=""):
        super().__init__(message)
        self.status = status
        self.body = body


def resolve_api_key(api_key=None, env=None):
    """The key to send, or a :class:`MissingApiKey` explaining what to fix."""
    env = os.environ if env is None else env
    key = api_key if api_key is not None else env.get(API_KEY_ENV, "")
    key = key.strip()
    if not key:
        raise MissingApiKey(
            f"no API key: pass api_key=... or set {API_KEY_ENV} in the "
            "environment. Get one at https://build.nvidia.com/ (Get API Key)."
        )
    if key.startswith("$") or key.startswith("${") or key.startswith("%"):
        raise MissingApiKey(
            f"the API key is still the literal text {key!r}. Python does not "
            "expand shell variables inside a string -- the catalog snippet's "
            f'"Bearer ${API_KEY_ENV}" has to become os.environ["{API_KEY_ENV}"].'
        )
    return key


def text_part(text):
    """The text fragment of a multimodal user message."""
    return {"type": "text", "text": text}


def user_message(prompt, parts=()):
    """A user turn carrying ``prompt`` followed by any image parts."""
    return {"role": "user", "content": [text_part(prompt), *parts]}


def stream_events(lines):
    """``(channel, text)`` for each delta in a server-sent-event stream.

    ``channel`` is ``"content"`` or ``"reasoning"``. Blank keep-alives, comment
    lines and anything that is not parseable JSON are skipped: a stream that
    hiccups mid-answer should lose a fragment, not raise.
    """
    for raw in lines:
        if not raw:
            continue
        line = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if payload == "[DONE]":
            return
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices") or ():
            delta = choice.get("delta") or {}
            reasoning = delta.get("reasoning_content")
            if reasoning:
                yield "reasoning", reasoning
            content = delta.get("content")
            if content:
                yield "content", content


def completion_text(payload):
    """The assistant's answer from a non-streamed response body."""
    for choice in payload.get("choices") or ():
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content:
            return content
        # Some models return the answer as content parts rather than a string.
        if isinstance(content, list):
            joined = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
            if joined:
                return joined
    return ""


class VisionClient:
    """Ask an NVIDIA-hosted vision model about one or more images.

    ``max_inline_chars`` and ``downscale`` are passed through to
    :func:`pwb_toolbox.vision.images.prepare` for every local image. Set
    ``downscale=False`` when the images are documents whose small print is the
    point -- an oversized scan then raises rather than being quietly resized.
    """

    def __init__(
        self,
        api_key=None,
        model=DEFAULT_MODEL,
        base_url=DEFAULT_BASE_URL,
        session=None,
        timeout=120.0,
        max_inline_chars=DEFAULT_MAX_INLINE_CHARS,
        downscale=True,
        defaults=None,
    ):
        self.api_key = resolve_api_key(api_key)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_inline_chars = max_inline_chars
        self.downscale = downscale
        self.defaults = dict(defaults or {})
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    # -- request building -------------------------------------------------

    def _headers(self, stream):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }

    def prepare_images(self, images):
        """:class:`~pwb_toolbox.vision.images.PreparedImage` for each reference.

        ``images`` may be one reference or an iterable of them. A ``str`` or
        ``bytes`` is one image, never an iterable of characters.
        """
        if images is None:
            return []
        if isinstance(images, (str, bytes, bytearray)) or hasattr(images, "__fspath__"):
            images = [images]
        return [prepare(one, self.max_inline_chars, self.downscale) for one in images]

    def build_payload(self, prompt, images=None, stream=False, **params):
        """The request body, with ``params`` overriding the client defaults."""
        prepared = self.prepare_images(images)
        # Tunables layer -- client defaults, then per-call params -- but the
        # structural keys are set last so neither can hand the request a
        # `messages` list that does not match the images just prepared.
        body = dict(self.defaults)
        body.update(params)
        body.setdefault("model", self.model)
        body["messages"] = [user_message(prompt, [one.part() for one in prepared])]
        body["stream"] = bool(stream)
        return body

    # -- transport --------------------------------------------------------

    def _post(self, body, stream):
        response = self._session.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(stream),
            json=body,
            stream=stream,
            timeout=self.timeout,
        )
        status = getattr(response, "status_code", None)
        if status is not None and status >= 400:
            raise NvidiaError(
                f"NVIDIA API returned HTTP {status}"
                + (
                    "; the key was rejected -- check NVIDIA_API_KEY"
                    if status in (401, 403)
                    else ""
                ),
                status=status,
                body=_body_text(response),
            )
        return response

    # -- the calls you actually make ---------------------------------------

    def events(self, prompt=DEFAULT_PROMPT, images=None, **params):
        """Stream ``(channel, text)`` pairs as the model produces them."""
        response = self._post(self.build_payload(prompt, images, True, **params), True)
        return stream_events(response.iter_lines())

    def stream(self, prompt=DEFAULT_PROMPT, images=None, **params):
        """Stream the answer only, dropping any reasoning channel."""
        for channel, text in self.events(prompt, images, **params):
            if channel == "content":
                yield text

    def describe(self, images=None, prompt=DEFAULT_PROMPT, **params):
        """The model's answer about ``images``, as one string.

        Non-streaming: one request, one body, one answer. Use :meth:`stream`
        when a long answer should appear as it is written.
        """
        response = self._post(
            self.build_payload(prompt, images, False, **params), False
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise NvidiaError(
                "NVIDIA API returned a body that is not JSON",
                status=getattr(response, "status_code", None),
                body=_body_text(response),
            ) from exc
        if "error" in payload and "choices" not in payload:
            raise NvidiaError(
                f"NVIDIA API reported an error: {payload['error']}",
                status=getattr(response, "status_code", None),
                body=json.dumps(payload),
            )
        return completion_text(payload)


def _body_text(response):
    text = getattr(response, "text", "")
    return text if isinstance(text, str) else ""
