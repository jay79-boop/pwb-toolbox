"""Tests for the NVIDIA hosted vision client.

Everything here runs offline. The HTTP layer is exercised against a fake
``requests.Session`` and every image is built in memory, so the suite never
reaches ``integrate.api.nvidia.com`` and needs no ``NVIDIA_API_KEY``.
"""

import base64
import io
import json
import os

import pytest

from tools.nvidia_vision import (
    Answer,
    DEFAULT_MODEL,
    ENV_KEY,
    ImageTooLarge,
    MAX_INLINE_BYTES,
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
    main,
    parse_completion,
    shrink_to_fit,
)

KEY = "nvapi-test-key"


# --------------------------------------------------------------------------
# fake transport
# --------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, payload=None, lines=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self._lines = lines or []
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload

    def iter_lines(self):
        for line in self._lines:
            yield line if isinstance(line, bytes) else line.encode("utf-8")


class FakeSession:
    """Records every POST and replies from a queue of responses."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, stream=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers or {},
                "json": json,
                "stream": stream,
                "timeout": timeout,
            }
        )
        if not self.queue:
            raise AssertionError("more requests than the test queued responses")
        return self.queue.pop(0)


def make_client(*responses, **kwargs):
    """A client wired to a fake session, with retry sleeps recorded not slept."""
    slept = []
    kwargs.setdefault("backoff", 1.0)
    client = VisionClient(
        KEY,
        session=FakeSession(*responses),
        sleep=slept.append,
        **kwargs,
    )
    client.slept = slept
    return client


def sse(*events):
    """Encode ``events`` as the SSE lines the endpoint sends, plus [DONE]."""
    lines = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    lines.append("data: [DONE]")
    return lines


def delta(content=None, reasoning=None, finish=None, **rest):
    choice = {"index": 0, "delta": {}, "finish_reason": finish}
    if content is not None:
        choice["delta"]["content"] = content
    if reasoning is not None:
        choice["delta"]["reasoning_content"] = reasoning
    return {"model": DEFAULT_MODEL, "choices": [choice], **rest}


# --------------------------------------------------------------------------
# the key: NVIDIA's own snippet ships an unexpanded shell variable
# --------------------------------------------------------------------------


def test_unexpanded_shell_variable_is_rejected_before_any_request():
    # "Bearer $NVIDIA_API_KEY" is a Python string literal, not an expansion.
    # Sending it costs a round trip to learn nothing; catch it at construction.
    with pytest.raises(MissingKey) as exc:
        VisionClient("$NVIDIA_API_KEY", session=FakeSession())
    assert "unexpanded" in str(exc.value)

    with pytest.raises(MissingKey):
        VisionClient("${NVIDIA_API_KEY}", session=FakeSession())


def test_missing_key_names_the_variable_to_set(monkeypatch):
    monkeypatch.delenv(ENV_KEY, raising=False)
    with pytest.raises(MissingKey) as exc:
        VisionClient(session=FakeSession())
    assert ENV_KEY in str(exc.value)

    monkeypatch.setenv(ENV_KEY, "   ")
    with pytest.raises(MissingKey):
        VisionClient(session=FakeSession())


def test_key_comes_from_the_environment_when_not_passed(monkeypatch):
    monkeypatch.setenv(ENV_KEY, "nvapi-from-env")
    client = VisionClient(session=FakeSession())
    assert client.api_key == "nvapi-from-env"
    assert client.headers(False)["Authorization"] == "Bearer nvapi-from-env"


def test_accept_header_flips_with_streaming():
    client = make_client()
    assert client.headers(False)["Accept"] == "application/json"
    assert client.headers(True)["Accept"] == "text/event-stream"


# --------------------------------------------------------------------------
# payload
# --------------------------------------------------------------------------


def test_payload_puts_text_first_then_one_part_per_image():
    payload = build_payload(
        "what is this",
        ["https://example.com/a.jpg", "https://example.com/b.jpg"],
    )
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "what is this"}
    assert [part["image_url"]["url"] for part in content[1:]] == [
        "https://example.com/a.jpg",
        "https://example.com/b.jpg",
    ]
    assert payload["messages"][0]["role"] == "user"


def test_payload_omits_optional_fields_rather_than_sending_nulls():
    payload = build_payload("hi", seed=None, reasoning_effort=None)
    assert "seed" not in payload
    assert "reasoning_effort" not in payload

    payload = build_payload("hi", seed=0, reasoning_effort="max")
    assert payload["seed"] == 0
    assert payload["reasoning_effort"] == "max"


def test_payload_extra_can_add_fields_the_module_does_not_model():
    payload = build_payload("hi", extra={"top_p": 0.5})
    assert payload["top_p"] == 0.5


def test_text_only_prompt_needs_no_images():
    payload = build_payload("just text")
    assert len(payload["messages"][0]["content"]) == 1


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------


def png_bytes(width, height, kind="gradient"):
    """A real PNG, either smoothly compressible or incompressible noise."""
    from PIL import Image

    if kind == "noise":
        raw = os.urandom(width * height * 3)
        image = Image.frombytes("RGB", (width, height), raw)
    else:
        image = Image.new("RGB", (width, height))
        image.putdata(
            [
                ((x * 255) // max(width - 1, 1), (y * 255) // max(height - 1, 1), 128)
                for y in range(height)
                for x in range(width)
            ]
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_encoded_length_matches_real_base64():
    for size in (0, 1, 2, 3, 4, 100, 1000, 65_537):
        raw = b"x" * size
        assert encoded_length(size) == len(base64.b64encode(raw))


def test_guess_mime_reads_the_extension_and_rejects_non_images():
    assert guess_mime("a.png") == "image/png"
    assert guess_mime("a.JPG") == "image/jpeg"
    assert guess_mime("a.webp") == "image/webp"
    with pytest.raises(NvidiaVisionError):
        guess_mime("notes.txt")


def test_remote_url_is_passed_through_untouched(tmp_path):
    # Nothing to measure or resize: the bytes never come through this process.
    part = image_part("https://example.com/x.jpg", max_bytes=1)
    assert part == {
        "type": "image_url",
        "image_url": {"url": "https://example.com/x.jpg"},
    }
    already = data_uri(b"abc", "image/png")
    assert image_part(already, max_bytes=1)["image_url"]["url"] == already


def test_local_file_becomes_a_data_uri(tmp_path):
    path = tmp_path / "chart.png"
    raw = png_bytes(40, 40)
    path.write_bytes(raw)

    url = image_part(path)["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == raw


def test_missing_file_says_so(tmp_path):
    with pytest.raises(NvidiaVisionError) as exc:
        image_part(tmp_path / "nope.png")
    assert "no such file" in str(exc.value)


def test_small_image_is_not_touched_by_the_shrinker():
    raw = png_bytes(20, 20)
    out, mime = shrink_to_fit(raw, MAX_INLINE_BYTES)
    assert out is raw
    assert mime == ""


def test_oversized_image_is_downscaled_under_the_cap():
    # Calibrated off the image rather than hard-coded: a gradient compresses so
    # well that any round number for the cap silently stops testing anything.
    raw = png_bytes(900, 900)
    cap = encoded_length(len(raw)) - 1

    out, mime = shrink_to_fit(raw, cap)
    assert len(out) < len(raw)
    assert encoded_length(len(out)) <= cap
    assert mime in ("image/png", "image/jpeg")


def test_shrinker_stays_on_png_while_png_can_fit():
    # Charts are line art: a JPEG at the quality needed to hit a size target
    # smears axis text and thin wicks, so PNG has to be tried at every scale
    # before the format changes.
    raw = png_bytes(900, 900)
    _, mime = shrink_to_fit(raw, encoded_length(len(raw)) - 1)
    assert mime == "image/png"


def test_shrinker_falls_back_to_jpeg_when_no_png_scale_fits():
    from PIL import Image

    raw = png_bytes(700, 700, kind="noise")
    # Calibrate the cap just under the smallest PNG the ladder is allowed to
    # produce, so the test pins the fallback rather than a hard-coded size.
    smallest = Image.open(io.BytesIO(raw)).resize((350, 350))
    buffer = io.BytesIO()
    smallest.save(buffer, format="PNG", optimize=True)
    cap = encoded_length(len(buffer.getvalue())) - 1

    out, mime = shrink_to_fit(raw, cap, min_side=320)
    assert mime == "image/jpeg"
    assert encoded_length(len(out)) <= cap


def test_shrinker_refuses_to_go_below_min_side():
    raw = png_bytes(400, 400, kind="noise")
    with pytest.raises(ImageTooLarge) as exc:
        shrink_to_fit(raw, 100, min_side=320)
    assert "320px" in str(exc.value)


def test_no_resize_reports_the_cap_instead_of_shrinking(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(png_bytes(600, 600, kind="noise"))
    with pytest.raises(ImageTooLarge) as exc:
        image_part(path, max_bytes=10_000, resize=False)
    assert "--no-resize" in str(exc.value)


def test_image_part_resizes_and_relabels_the_mime(tmp_path):
    path = tmp_path / "big.png"
    path.write_bytes(png_bytes(900, 900))
    url = image_part(path, max_bytes=40_000)["image_url"]["url"]
    assert url.startswith("data:image/")
    encoded = url.split(",", 1)[1]
    assert len(encoded) <= 40_000


# --------------------------------------------------------------------------
# streaming
# --------------------------------------------------------------------------


def test_iter_stream_drops_keepalives_and_the_done_sentinel():
    lines = ["", "data: [DONE]", ": ping", 'data: {"a": 1}', 'data:{"b": 2}']
    assert list(iter_stream(lines)) == [{"a": 1}, {"b": 2}]


def test_stream_keeps_reasoning_out_of_the_answer():
    # A reasoning model splits the two across delta keys. Concatenating them
    # returns a reply with the model's scratchpad pasted on the front.
    lines = sse(
        delta(reasoning="let me look"),
        delta(reasoning=" at the chart"),
        delta(content="A candlestick"),
        delta(content=" chart.", finish="stop"),
    )
    answer = collect_stream(lines)
    assert answer.text == "A candlestick chart."
    assert answer.reasoning == "let me look at the chart"
    assert answer.finish_reason == "stop"
    assert answer.model == DEFAULT_MODEL


def test_stream_survives_a_truncated_final_fragment():
    lines = ["data: " + json.dumps(delta(content="kept")), 'data: {"choices": [{']
    assert collect_stream(lines).text == "kept"


def test_stream_captures_usage_when_the_tail_carries_it():
    lines = sse(delta(content="hi"), {"usage": {"total_tokens": 12}, "choices": []})
    assert collect_stream(lines).usage == {"total_tokens": 12}


def test_stream_error_event_raises():
    lines = ["data: " + json.dumps({"error": "model not found"})]
    with pytest.raises(NvidiaVisionError) as exc:
        collect_stream(lines)
    assert "model not found" in str(exc.value)


def test_bytes_and_str_lines_are_both_accepted():
    text = json.dumps(delta(content="ok"))
    assert collect_stream([f"data: {text}".encode()]).text == "ok"
    assert collect_stream([f"data: {text}"]).text == "ok"


# --------------------------------------------------------------------------
# non-streamed responses
# --------------------------------------------------------------------------


def completion(content="an answer", **rest):
    return {
        "model": DEFAULT_MODEL,
        "choices": [
            {"index": 0, "message": {"content": content}, "finish_reason": "stop"}
        ],
        "usage": {"total_tokens": 7},
        **rest,
    }


def test_parse_completion_reads_the_message():
    answer = parse_completion(completion())
    assert answer.text == "an answer"
    assert answer.finish_reason == "stop"
    assert answer.usage == {"total_tokens": 7}


def test_parse_completion_handles_the_list_of_parts_shape():
    payload = completion(content=[{"type": "text", "text": "a"}, {"text": "b"}])
    assert parse_completion(payload).text == "ab"


def test_parse_completion_keeps_reasoning_separate():
    payload = completion()
    payload["choices"][0]["message"]["reasoning_content"] = "thinking"
    answer = parse_completion(payload)
    assert answer.text == "an answer"
    assert answer.reasoning == "thinking"


def test_parse_completion_raises_on_an_error_body_and_on_no_choices():
    with pytest.raises(NvidiaVisionError):
        parse_completion({"error": {"message": "bad model"}})
    with pytest.raises(NvidiaVisionError):
        parse_completion({"choices": []})


# --------------------------------------------------------------------------
# the client
# --------------------------------------------------------------------------


def test_ask_sends_the_key_and_returns_the_answer():
    client = make_client(FakeResponse(payload=completion()))
    answer = client.ask("what is this", ["https://example.com/a.jpg"])

    assert answer.text == "an answer"
    call = client.session.calls[0]
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["stream"] is False
    assert call["json"]["stream"] is False
    assert call["json"]["messages"][0]["content"][1]["image_url"] == {
        "url": "https://example.com/a.jpg"
    }


def test_ask_streaming_returns_the_whole_answer_and_tees_each_fragment():
    lines = sse(delta(content="one "), delta(content="two", finish="stop"))
    client = make_client(FakeResponse(lines=lines))
    seen = []

    answer = client.ask("go", stream=True, on_chunk=seen.append)

    assert answer.text == "one two"
    assert seen == ["one ", "two"]
    assert client.session.calls[0]["stream"] is True
    assert client.session.calls[0]["headers"]["Accept"] == "text/event-stream"


def test_retryable_status_is_retried_with_backoff_then_succeeds():
    client = make_client(
        FakeResponse(status_code=503, text="upstream busy"),
        FakeResponse(status_code=429, text="slow down"),
        FakeResponse(payload=completion()),
    )
    assert client.ask("go").text == "an answer"
    assert len(client.session.calls) == 3
    assert client.slept == [1.0, 2.0]  # doubling, and never actually slept


def test_a_client_error_is_not_retried_and_carries_the_body():
    client = make_client(FakeResponse(status_code=400, text="unknown field"))
    with pytest.raises(NvidiaVisionError) as exc:
        client.ask("go")
    assert "400" in str(exc.value)
    assert "unknown field" in str(exc.value)
    assert client.slept == []
    assert len(client.session.calls) == 1


def test_retries_give_up_and_report_the_last_failure():
    client = make_client(
        *[FakeResponse(status_code=503, text="down")] * 3, max_retries=2
    )
    with pytest.raises(NvidiaVisionError) as exc:
        client.ask("go")
    assert "503" in str(exc.value)
    assert len(client.session.calls) == 3


def test_a_non_json_body_on_a_200_is_reported_as_such():
    client = make_client(FakeResponse(text="<html>gateway</html>"))
    with pytest.raises(NvidiaVisionError) as exc:
        client.ask("go")
    assert "not JSON" in str(exc.value)


def test_model_can_be_overridden_per_call():
    client = make_client(FakeResponse(payload=completion()))
    client.ask("go", model="other/model")
    assert client.session.calls[0]["json"]["model"] == "other/model"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_ask_prints_the_answer(monkeypatch, capsys):
    captured = {}

    class Stub:
        def ask(self, prompt, images=(), **kwargs):
            captured["prompt"] = prompt
            captured["images"] = list(images)
            captured["kwargs"] = kwargs
            return Answer(text="a chart")

    monkeypatch.setattr("tools.nvidia_vision._client_from", lambda args: Stub())
    assert main(["ask", "https://x/y.png", "--prompt", "read it"]) == 0
    assert capsys.readouterr().out.strip() == "a chart"
    assert captured["prompt"] == "read it"
    assert captured["images"] == ["https://x/y.png"]
    assert captured["kwargs"]["seed"] == 0


def test_cli_negative_seed_omits_the_seed(monkeypatch):
    captured = {}

    class Stub:
        def ask(self, prompt, images=(), **kwargs):
            captured.update(kwargs)
            return Answer(text="")

    monkeypatch.setattr("tools.nvidia_vision._client_from", lambda args: Stub())
    main(["ask", "--seed", "-1"])
    assert captured["seed"] is None


def test_cli_json_output_is_not_polluted_by_the_stream(monkeypatch, capsys):
    class Stub:
        def ask(self, prompt, images=(), **kwargs):
            assert kwargs["on_chunk"] is None, "--json must suppress the printer"
            return Answer(text="a chart", reasoning="hmm", model="m")

    monkeypatch.setattr("tools.nvidia_vision._client_from", lambda args: Stub())
    assert main(["ask", "--stream", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["text"] == "a chart"
    assert body["reasoning"] == "hmm"


def test_cli_reports_a_vision_error_without_a_traceback(monkeypatch, capsys):
    class Stub:
        def ask(self, *a, **k):
            raise NvidiaVisionError("boom")

    monkeypatch.setattr("tools.nvidia_vision._client_from", lambda args: Stub())
    assert main(["ask"]) == 1
    assert "boom" in capsys.readouterr().err
