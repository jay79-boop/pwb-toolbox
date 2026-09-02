"""Tests for `pwb_toolbox.vision`.

Nothing here touches the network and nothing needs an API key: HTTP is served
by `FakeSession`, and every test that builds a client passes one in.

Three of these pin traps rather than features, because each one produced a
call that looked right and failed at the endpoint:

* `test_a_literal_shell_variable_is_refused` -- NVIDIA's catalog snippet is
  shell, and `"Bearer $NVIDIA_API_KEY"` pasted into Python sends those
  eighteen characters and comes back 401.
* `test_the_limit_counts_base64_characters_not_raw_bytes` -- the cap is on the
  encoded payload, which is 4/3 the size of the file. Measuring the file passes
  locally and fails on the wire.
* `test_reasoning_is_not_glued_to_the_answer` -- a model given
  `reasoning_effort` streams its working on a second channel, so concatenating
  every delta returns the scratch work with the answer buried after it.
"""

import base64
import json

import pytest

from pwb_toolbox.vision import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    ImageTooLarge,
    MissingApiKey,
    NvidiaError,
    UnsupportedImage,
    VisionClient,
    completion_text,
    prepare,
    resolve_api_key,
    sniff,
    stream_events,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff\xe0"


class FakeResponse:
    """Enough of a `requests.Response` for the client to work against."""

    def __init__(self, status_code=200, payload=None, lines=(), text=None):
        self.status_code = status_code
        self._payload = payload
        self._lines = list(lines)
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def iter_lines(self):
        return iter(self._lines)


class FakeSession:
    """Records the request it was handed and replays a canned response."""

    def __init__(self, response=None):
        self.response = response or FakeResponse(payload={"choices": []})
        self.calls = []

    def post(self, url, headers=None, json=None, stream=False, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers or {},
                "json": json,
                "stream": stream,
                "timeout": timeout,
            }
        )
        return self.response


def client(response=None, **kwargs):
    kwargs.setdefault("api_key", "nvapi-test-key")
    session = FakeSession(response)
    return VisionClient(session=session, **kwargs), session


def sse(*chunks):
    """Server-sent-event lines for a sequence of chunk dicts."""
    return [f"data: {json.dumps(chunk)}" for chunk in chunks] + ["data: [DONE]"]


def delta(**fields):
    return {"choices": [{"delta": fields}]}


# --- the API key -------------------------------------------------------


def test_an_explicit_key_is_used_and_stripped():
    assert resolve_api_key("  nvapi-abc  ") == "nvapi-abc"


def test_the_key_is_read_from_the_environment():
    assert resolve_api_key(env={API_KEY_ENV: "nvapi-from-env"}) == "nvapi-from-env"


def test_a_missing_key_says_where_to_get_one():
    with pytest.raises(MissingApiKey) as excinfo:
        resolve_api_key(env={})
    assert API_KEY_ENV in str(excinfo.value)
    assert "build.nvidia.com" in str(excinfo.value)


@pytest.mark.parametrize(
    "literal",
    ["$NVIDIA_API_KEY", "${NVIDIA_API_KEY}", "%NVIDIA_API_KEY%"],
)
def test_a_literal_shell_variable_is_refused(literal):
    """The catalog snippet's `"Bearer $NVIDIA_API_KEY"` is a shell idiom.

    Python sends the string as written, and the endpoint answers 401 with
    nothing that points at the cause. Naming it here costs one comparison.
    """
    with pytest.raises(MissingApiKey) as excinfo:
        resolve_api_key(literal, env={API_KEY_ENV: "nvapi-real"})
    assert "does not expand" in str(excinfo.value)


# --- turning an image into a request part ------------------------------


def test_a_url_is_passed_through_rather_than_downloaded():
    ready = prepare("https://example.com/chart.png")
    assert ready.is_remote
    assert ready.url == "https://example.com/chart.png"
    assert ready.part() == {
        "type": "image_url",
        "image_url": {"url": "https://example.com/chart.png"},
    }


def test_bytes_become_a_data_uri():
    ready = prepare(PNG_MAGIC + b"\x00" * 64)
    assert ready.media_type == "image/png"
    assert ready.url.startswith("data:image/png;base64,")
    assert ready.scale == 1.0
    payload = ready.url.split(",", 1)[1]
    assert base64.b64decode(payload).startswith(PNG_MAGIC)


def test_a_file_is_read_from_disk(tmp_path):
    path = tmp_path / "shot.png"
    path.write_bytes(PNG_MAGIC + b"\x00" * 32)
    assert prepare(path).media_type == "image/png"


def test_a_missing_file_is_reported_as_such(tmp_path):
    with pytest.raises(FileNotFoundError):
        prepare(tmp_path / "nope.png")


def test_the_content_beats_a_wrong_extension():
    """A screenshot pipeline really does write JPEG bytes into a .png."""
    assert sniff(JPEG_MAGIC + b"\x00" * 16, "shot.png") == "image/jpeg"


def test_the_extension_is_used_when_the_content_says_nothing():
    assert sniff(b"\x00" * 32, "scan.webp") == "image/webp"


def test_an_unrecognisable_image_is_rejected():
    with pytest.raises(UnsupportedImage):
        sniff(b"\x00" * 32)


def test_the_limit_counts_base64_characters_not_raw_bytes():
    """150,000 raw bytes is under a 180,000 cap; its base64 is 200,000 and is not.

    Measuring the file rather than the encoding passes here and 413s on the
    wire, which is the whole reason the limit is expressed in characters.
    """
    data = PNG_MAGIC + b"\x11" * (150_000 - len(PNG_MAGIC))
    assert len(data) < 180_000
    assert len(base64.b64encode(data)) > 180_000
    with pytest.raises(ImageTooLarge) as excinfo:
        prepare(data, 180_000, False)
    assert "base64 characters" in str(excinfo.value)


def test_an_oversized_image_suggests_the_alternatives():
    with pytest.raises(ImageTooLarge) as excinfo:
        prepare(PNG_MAGIC + b"\x22" * 4000, 1000, False)
    message = str(excinfo.value)
    assert "downscale=True" in message and "pass the URL" in message


def test_something_that_cannot_be_shrunk_still_raises():
    """Downscaling is best-effort: garbage bytes are not an image to resize."""
    with pytest.raises(ImageTooLarge) as excinfo:
        prepare(PNG_MAGIC + b"\x33" * 4000, 1000, True)
    assert "could not be shrunk" in str(excinfo.value)


def test_a_real_oversized_screenshot_is_shrunk_to_fit(tmp_path):
    Image = pytest.importorskip("PIL.Image", reason="Pillow arrives with matplotlib")
    import random

    # Noise, so PNG cannot compress the picture away to nothing.
    rng = random.Random(0)
    image = Image.new("RGB", (900, 900))
    image.putdata(
        [
            (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for _ in range(900 * 900)
        ]
    )
    path = tmp_path / "chart.png"
    image.save(path)

    limit = 60_000
    assert len(base64.b64encode(path.read_bytes())) > limit

    ready = prepare(path, limit, True)
    assert ready.encoded_chars <= limit
    assert 0 < ready.scale < 1.0


# --- the request body --------------------------------------------------


def test_the_prompt_leads_and_the_images_follow():
    api, session = client()
    body = api.build_payload("read this", ["https://example.com/a.png"])
    content = body["messages"][0]["content"]
    assert body["model"] == DEFAULT_MODEL
    assert body["stream"] is False
    assert content[0] == {"type": "text", "text": "read this"}
    assert content[1]["image_url"]["url"] == "https://example.com/a.png"


def test_one_string_is_one_image_not_a_list_of_characters():
    api, _ = client()
    body = api.build_payload("q", "https://example.com/a.png")
    images = [p for p in body["messages"][0]["content"] if p["type"] == "image_url"]
    assert len(images) == 1


def test_several_images_ride_in_one_message():
    api, _ = client()
    body = api.build_payload(
        "compare the statements",
        ["https://example.com/a.png", "https://example.com/b.png"],
    )
    images = [p for p in body["messages"][0]["content"] if p["type"] == "image_url"]
    assert len(images) == 2


def test_a_call_parameter_beats_a_client_default():
    api, _ = client(defaults={"temperature": 0.2, "seed": 7})
    body = api.build_payload("q", None, False, temperature=0.9)
    assert body["temperature"] == 0.9
    assert body["seed"] == 7


def test_defaults_cannot_replace_the_messages_they_do_not_know_about():
    api, _ = client(defaults={"messages": [{"role": "user", "content": "stale"}]})
    body = api.build_payload("fresh", ["https://example.com/a.png"])
    assert body["messages"][0]["content"][0]["text"] == "fresh"


def test_the_headers_say_which_kind_of_response_is_wanted():
    api, session = client(FakeResponse(payload={"choices": []}))
    api.describe(prompt="q")
    headers = session.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer nvapi-test-key"
    assert headers["Accept"] == "application/json"
    assert session.calls[0]["stream"] is False


def test_a_streaming_call_asks_for_events_and_an_unbuffered_body():
    api, session = client(FakeResponse(lines=sse(delta(content="hi"))))
    list(api.stream("q"))
    assert session.calls[0]["headers"]["Accept"] == "text/event-stream"
    assert session.calls[0]["stream"] is True
    assert session.calls[0]["json"]["stream"] is True


# --- reading the stream ------------------------------------------------


def test_content_deltas_are_joined_in_order():
    lines = sse(
        delta(content="Nvidia "), delta(content="led "), delta(content="the tape.")
    )
    assert "".join(t for c, t in stream_events(lines) if c == "content") == (
        "Nvidia led the tape."
    )


def test_reasoning_is_not_glued_to_the_answer():
    """`reasoning_effort` puts the working on its own channel; keep it there."""
    lines = sse(
        delta(reasoning_content="let me look at the axis"),
        delta(content="The chart shows "),
        delta(content="a double top."),
    )
    events = list(stream_events(lines))
    assert [c for c, _ in events] == ["reasoning", "content", "content"]
    assert "".join(t for c, t in events if c == "content") == (
        "The chart shows a double top."
    )


def test_the_stream_helper_returns_the_answer_only():
    api, _ = client(
        FakeResponse(lines=sse(delta(reasoning_content="hmm"), delta(content="a top")))
    )
    assert "".join(api.stream("q")) == "a top"


def test_the_done_sentinel_ends_the_stream():
    lines = ["data: [DONE]", 'data: {"choices":[{"delta":{"content":"late"}}]}']
    assert list(stream_events(lines)) == []


def test_keepalives_and_junk_are_skipped_rather_than_raised_on():
    lines = [
        "",
        ": keep-alive",
        "data: not json at all",
        b'data: {"choices":[{"delta":{"content":"ok"}}]}',
        "data: [DONE]",
    ]
    assert list(stream_events(lines)) == [("content", "ok")]


def test_bytes_and_str_lines_read_the_same():
    chunk = json.dumps(delta(content="x"))
    assert list(stream_events([f"data: {chunk}".encode()])) == [("content", "x")]


# --- reading a whole response ------------------------------------------


def test_the_answer_comes_back_as_a_string():
    api, _ = client(
        FakeResponse(payload={"choices": [{"message": {"content": "a candlestick"}}]})
    )
    assert api.describe("https://example.com/a.png") == "a candlestick"


def test_an_answer_returned_as_content_parts_is_joined():
    payload = {
        "choices": [
            {
                "message": {
                    "content": [{"type": "text", "text": "two "}, {"text": "gaps"}]
                }
            }
        ]
    }
    assert completion_text(payload) == "two gaps"


def test_an_empty_choice_list_gives_an_empty_answer():
    assert completion_text({"choices": []}) == ""


def test_a_rejected_key_says_so():
    api, _ = client(FakeResponse(status_code=401, text="invalid key"))
    with pytest.raises(NvidiaError) as excinfo:
        api.describe("https://example.com/a.png")
    assert excinfo.value.status == 401
    assert API_KEY_ENV in str(excinfo.value)
    assert excinfo.value.body == "invalid key"


def test_a_server_error_carries_the_body_the_endpoint_sent():
    api, _ = client(FakeResponse(status_code=500, text="upstream exploded"))
    with pytest.raises(NvidiaError) as excinfo:
        api.describe("https://example.com/a.png")
    assert excinfo.value.status == 500
    assert excinfo.value.body == "upstream exploded"


def test_an_http_error_on_a_streaming_call_is_raised_before_parsing():
    api, _ = client(FakeResponse(status_code=429, text="rate limited"))
    with pytest.raises(NvidiaError) as excinfo:
        list(api.stream("q"))
    assert excinfo.value.status == 429


def test_an_error_body_with_a_200_is_still_an_error():
    """The endpoint sometimes reports a model failure inside a 200 response."""
    api, _ = client(FakeResponse(payload={"error": {"message": "model unavailable"}}))
    with pytest.raises(NvidiaError) as excinfo:
        api.describe("https://example.com/a.png")
    assert "model unavailable" in str(excinfo.value)


def test_a_body_that_is_not_json_is_reported_as_such():
    api, _ = client(FakeResponse(payload=None, text="<html>gateway</html>"))
    with pytest.raises(NvidiaError) as excinfo:
        api.describe("https://example.com/a.png")
    assert "not JSON" in str(excinfo.value)
    assert excinfo.value.body == "<html>gateway</html>"
