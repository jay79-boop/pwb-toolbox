"""Agent Analytics for the vision client: one ``ask()`` is one Amplitude session.

Amplitude's ``amplitude-ai`` SDK ships wrappers for OpenAI, Anthropic and the
other big providers, and none for NVIDIA's hosted endpoint -- so this is the
manual route its own instructions prescribe for an OpenAI-compatible proxy:
open a session around the call, record the prompt as the user message, the
answer as the AI response with the token counts and latency the endpoint
reported, and close the session so enrichment can run at once.

Three things are deliberate:

- **No key means no tracking, said once, and nothing else changes.** The key
  is read from ``AMPLITUDE_AI_API_KEY`` exactly like ``NVIDIA_API_KEY``; when
  it is unset :func:`from_environment` warns on stderr and returns ``None``,
  and the CLI calls the client directly. A silent no-op is how an analytics
  key that was never set at deploy goes unnoticed for a month.
- **Cost is reported as zero, explicitly.** NVIDIA's hosted models are not
  in the pricing table the SDK consults, and a missing cost fails Amplitude's
  data quality gate silently; ``0`` is the documented suppression value for a
  model with no price, and build.nvidia.com's credits carry none.
- **The ``amplitude_ai`` import is lazy.** ``pwb_toolbox.vision`` must import
  with the six-package subset that runs the rest of the desk; this module only
  needs the SDK once a key is present.

``tests/test_amplitude_verify.py`` runs it against the SDK's own mock: every
event asserted, nothing sent.
"""

from __future__ import annotations

import getpass
import os
import sys
import time
import uuid
from typing import Any, Sequence

from .nvidia import Answer

ENV_AI_KEY = "AMPLITUDE_AI_API_KEY"
ENV_USER_ID = "AMPLITUDE_USER_ID"
AGENT_ID = "nvidia-vision"
AGENT_DESCRIPTION = (
    "Reads a chart, a journal shot or a scanned statement with a vision model "
    "on NVIDIA's hosted API (tools/nvidia_vision.py)"
)
PROVIDER = "nvidia"
MISSING_KEY_WARNING = (
    f"warning: {ENV_AI_KEY} is not set -- Amplitude Agent Analytics disabled"
)

_warned = False


def default_user_id() -> str:
    """``AMPLITUDE_USER_ID`` if set, else ``desk-<login>``.

    Amplitude rejects an id shorter than five characters with HTTP 400, so
    the fallback carries a prefix rather than the bare login.
    """
    configured = (os.environ.get(ENV_USER_ID) or "").strip()
    if configured:
        return configured
    try:
        login = getpass.getuser()
    except Exception:  # pragma: no cover - no login name on some CI runners
        login = "unknown"
    return f"desk-{login or 'unknown'}"


def from_environment(*, err=None) -> "VisionTelemetry | None":
    """A tracker when ``AMPLITUDE_AI_API_KEY`` is set, else ``None`` -- loudly.

    The warning prints once per process, to ``err`` (default stderr), so a
    CLI run with no key says so without repeating itself per call.
    """
    global _warned
    key = (os.environ.get(ENV_AI_KEY) or "").strip()
    if not key:
        if not _warned:
            print(MISSING_KEY_WARNING, file=err or sys.stderr)
            _warned = True
        return None
    from amplitude_ai import AIConfig, AmplitudeAI  # lazy: see the module note

    ai = AmplitudeAI(api_key=key, config=AIConfig(content_mode="full"))
    return VisionTelemetry(ai)


class VisionTelemetry:
    """Wraps an ``AmplitudeAI`` (or its ``MockAmplitudeAI``) around ``ask()``."""

    def __init__(self, ai: Any, *, user_id: str | None = None, clock=time.monotonic):
        self.ai = ai
        self.user_id = user_id or default_user_id()
        self._clock = clock
        # One agent per process, at construction: a fresh agent per call would
        # give every turn a different Agent ID and break session grouping.
        self.agent = ai.agent(AGENT_ID, description=AGENT_DESCRIPTION)

    def ask(
        self,
        client: Any,
        prompt: str,
        images: Sequence[Any] = (),
        **kwargs: Any,
    ) -> Answer:
        """``client.ask(...)`` inside a tracked session; re-raises what it raises."""
        model = kwargs.get("model") or getattr(client, "model", "") or ""
        session_id = f"vision-{uuid.uuid4()}"
        try:
            with self.agent.session(
                user_id=self.user_id, session_id=session_id
            ) as session:
                session.track_user_message(
                    prompt,
                    context={
                        "images": len(images),
                        "model": model,
                        "stream": bool(kwargs.get("stream")),
                    },
                )
                start = self._clock()
                try:
                    answer = client.ask(prompt, images, **kwargs)
                except Exception as exc:
                    session.track_ai_message(
                        "",
                        model,
                        PROVIDER,
                        (self._clock() - start) * 1000,
                        is_error=True,
                        error_message=str(exc),
                        total_cost_usd=0,
                    )
                    raise
                latency_ms = (self._clock() - start) * 1000
                usage = answer.usage if isinstance(answer.usage, dict) else {}
                session.track_ai_message(
                    answer.text,
                    answer.model or model,
                    PROVIDER,
                    latency_ms,
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    total_cost_usd=0,  # not in the pricing table; see the module note
                    context={
                        "finish_reason": answer.finish_reason,
                        "has_reasoning": bool(answer.reasoning),
                    },
                )
                return answer
        finally:
            # A CLI is not serverless: nothing flushes for us, and the process
            # ends right after this call.
            self.ai.flush()

    def close(self) -> None:
        self.ai.flush()
        shutdown = getattr(self.ai, "shutdown", None)
        if callable(shutdown):
            shutdown()
