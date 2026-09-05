"""Agent Analytics on the vision client, verified against Amplitude's own mock.

Nothing here reaches Amplitude or NVIDIA: the SDK's ``MockAmplitudeAI``
captures every event, and the client is a stub that returns the shape
``VisionClient.ask`` returns. What is asserted is the shape Amplitude's Agent
Analytics needs to draw a session -- the agent id, a closed session, and the
seven fields on every AI response its data quality gate checks -- plus the two
behaviours that only matter when something is missing: an error still emits a
response, and a missing key disables tracking loudly rather than silently.
"""

from __future__ import annotations

import json

import pytest

amplitude_ai = pytest.importorskip("amplitude_ai")
from amplitude_ai import (  # noqa: E402
    PROP_AGENT_ID,
    PROP_COST_USD,
    PROP_INPUT_TOKENS,
    PROP_LATENCY_MS,
    PROP_MODEL_NAME,
    PROP_OUTPUT_TOKENS,
    PROP_PROVIDER,
    PROP_SESSION_ID,
)
from amplitude_ai.testing import MockAmplitudeAI  # noqa: E402

from pwb_toolbox.vision import Answer, NvidiaVisionError  # noqa: E402
from pwb_toolbox.vision import telemetry  # noqa: E402
from tools.nvidia_vision import main  # noqa: E402

AGENT_PROP = PROP_AGENT_ID
USAGE = {"prompt_tokens": 321, "completion_tokens": 45, "total_tokens": 366}


class StubClient:
    model = "nvidia/nemotron-nano-12b-v2-vl"

    def __init__(self, answer=None, error=None):
        self.answer = answer or Answer(
            text="price sits above the marked level",
            reasoning="",
            model="nvidia/nemotron-nano-12b-v2-vl",
            finish_reason="stop",
            usage=dict(USAGE),
        )
        self.error = error
        self.calls = []

    def ask(self, prompt, images=(), **kwargs):
        self.calls.append((prompt, list(images), kwargs))
        if self.error:
            raise self.error
        return self.answer


class Ticker:
    """A clock that advances a fixed amount per read, so latency is > 0."""

    def __init__(self, step=0.25):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


def make(mock=None, **kwargs):
    mock = mock or MockAmplitudeAI()
    tracker = telemetry.VisionTelemetry(
        mock, user_id="desk-tester", clock=Ticker(), **kwargs
    )
    return mock, tracker


def props(event):
    return event.event_properties or {}


def events(mock, name):
    return [e for e in mock.events if e.event_type == name]


# --------------------------------------------------------------------------
# the session
# --------------------------------------------------------------------------


def test_one_ask_is_one_closed_session_under_the_vision_agent():
    mock, tracker = make()
    client = StubClient()

    answer = tracker.ask(client, "read the levels", ["chart.png"], seed=0)

    assert answer is client.answer
    assert client.calls == [("read the levels", ["chart.png"], {"seed": 0})]
    mock.assert_event_tracked(
        "[Agent] User Message", **{AGENT_PROP: telemetry.AGENT_ID}
    )
    mock.assert_event_tracked("[Agent] AI Response", **{AGENT_PROP: telemetry.AGENT_ID})
    (user_message,) = events(mock, "[Agent] User Message")
    session_id = props(user_message)[PROP_SESSION_ID]
    assert session_id.startswith("vision-")
    mock.assert_session_closed(session_id)
    assert len(events(mock, "[Agent] Session End")) == 1


def test_the_prompt_is_the_user_message_and_the_images_are_context():
    mock, tracker = make()
    tracker.ask(StubClient(), "what is this", ["a.png", "b.png"], stream=True)
    (user_message,) = events(mock, "[Agent] User Message")
    p = props(user_message)
    assert p["$llm_message"]["text"] == "what is this"
    context = json.loads(p["[Agent] Context"])  # the SDK stores context as JSON
    assert context["images"] == 2
    assert context["stream"] is True
    assert context["model"] == StubClient.model


def test_every_ai_response_clears_amplitudes_data_quality_gate():
    """The seven fields Agent Analytics needs, on every response event."""
    mock, tracker = make()
    tracker.ask(StubClient(), "read it", ["chart.png"])
    responses = events(mock, "[Agent] AI Response")
    assert responses, "no AI Response event was emitted"
    for event in responses:
        p = props(event)
        assert event.user_id == "desk-tester"
        assert p[PROP_SESSION_ID]
        assert p[PROP_MODEL_NAME] == "nvidia/nemotron-nano-12b-v2-vl"
        assert p[PROP_PROVIDER] == "nvidia"
        assert p[PROP_LATENCY_MS] > 0
        assert p[PROP_INPUT_TOKENS] == USAGE["prompt_tokens"]
        assert p[PROP_OUTPUT_TOKENS] == USAGE["completion_tokens"]
        # zero, explicitly: NVIDIA's hosted models carry no price
        assert p[PROP_COST_USD] == 0
        assert p["$llm_message"]["text"] == "price sits above the marked level"


def test_the_sdks_own_quality_summary_agrees():
    mock, tracker = make()
    tracker.ask(StubClient(), "read it", ["chart.png"])
    assert "Ready to deploy." in mock.summary()


def test_a_failed_call_still_leaves_a_response_event_and_re_raises():
    mock, tracker = make()
    client = StubClient(error=NvidiaVisionError("HTTP 500 from nvidia"))

    with pytest.raises(NvidiaVisionError):
        tracker.ask(client, "read it", ["chart.png"])

    (response,) = events(mock, "[Agent] AI Response")
    p = props(response)
    assert p["[Agent] Is Error"] is True
    assert "HTTP 500" in p["[Agent] Error Message"]
    assert p[PROP_LATENCY_MS] > 0
    assert p[PROP_MODEL_NAME] == StubClient.model
    (user_message,) = events(mock, "[Agent] User Message")
    mock.assert_session_closed(props(user_message)[PROP_SESSION_ID])


def test_usage_absent_from_the_answer_does_not_break_tracking():
    mock, tracker = make()
    client = StubClient(answer=Answer(text="ok", model="m", usage={}))
    tracker.ask(client, "read it")
    (response,) = events(mock, "[Agent] AI Response")
    p = props(response)
    assert p[PROP_MODEL_NAME] == "m"
    assert p.get(PROP_INPUT_TOKENS) is None


def test_the_model_flag_wins_over_the_clients_default():
    mock, tracker = make()
    client = StubClient(answer=Answer(text="ok", model="", usage={}))
    tracker.ask(client, "read it", model="other/model")
    (response,) = events(mock, "[Agent] AI Response")
    assert props(response)[PROP_MODEL_NAME] == "other/model"


def test_two_asks_are_two_sessions_under_one_agent():
    mock, tracker = make()
    tracker.ask(StubClient(), "one")
    tracker.ask(StubClient(), "two")
    ids = {props(e)[PROP_SESSION_ID] for e in events(mock, "[Agent] User Message")}
    assert len(ids) == 2
    assert {props(e)[AGENT_PROP] for e in mock.events} == {telemetry.AGENT_ID}


# --------------------------------------------------------------------------
# the key, the user id, and the CLI
# --------------------------------------------------------------------------


def test_no_key_means_no_tracker_and_one_warning(monkeypatch, capsys):
    monkeypatch.delenv(telemetry.ENV_AI_KEY, raising=False)
    monkeypatch.setattr(telemetry, "_warned", False)
    assert telemetry.from_environment() is None
    assert telemetry.from_environment() is None
    err = capsys.readouterr().err
    assert err.count(telemetry.ENV_AI_KEY) == 1, "the warning must print once"
    assert "disabled" in err


def test_a_blank_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv(telemetry.ENV_AI_KEY, "   ")
    monkeypatch.setattr(telemetry, "_warned", True)
    assert telemetry.from_environment() is None


def test_a_key_builds_a_tracker_over_the_real_sdk(monkeypatch):
    monkeypatch.setenv(telemetry.ENV_AI_KEY, "amp-test-key-not-real")
    tracker = telemetry.from_environment()
    assert isinstance(tracker, telemetry.VisionTelemetry)
    assert isinstance(tracker.ai, amplitude_ai.AmplitudeAI)
    tracker.close()  # nothing tracked, so nothing to send


def test_the_default_user_id_is_long_enough_for_amplitude(monkeypatch):
    monkeypatch.delenv(telemetry.ENV_USER_ID, raising=False)
    assert len(telemetry.default_user_id()) >= 5
    assert telemetry.default_user_id().startswith("desk-")
    monkeypatch.setenv(telemetry.ENV_USER_ID, "gexio-desk")
    assert telemetry.default_user_id() == "gexio-desk"


def test_the_cli_routes_ask_through_the_tracker_and_closes_it(monkeypatch, capsys):
    mock = MockAmplitudeAI()
    closed = []

    class Tracker(telemetry.VisionTelemetry):
        def close(self):
            closed.append(True)
            super().close()

    tracker = Tracker(mock, user_id="desk-tester", clock=Ticker())
    monkeypatch.setattr("tools.nvidia_vision._telemetry", lambda: tracker)
    monkeypatch.setattr("tools.nvidia_vision._client_from", lambda args: StubClient())

    assert main(["ask", "chart.png", "--prompt", "read it"]) == 0
    assert "price sits above" in capsys.readouterr().out
    assert closed == [True]
    mock.assert_event_tracked("[Agent] AI Response", **{AGENT_PROP: telemetry.AGENT_ID})


def test_the_cli_without_a_key_runs_untracked(monkeypatch, capsys):
    monkeypatch.delenv(telemetry.ENV_AI_KEY, raising=False)
    monkeypatch.setattr(telemetry, "_warned", False)
    monkeypatch.setattr("tools.nvidia_vision._client_from", lambda args: StubClient())
    assert main(["ask", "chart.png", "--prompt", "read it"]) == 0
    out, err = capsys.readouterr()
    assert "price sits above" in out
    assert telemetry.ENV_AI_KEY in err
