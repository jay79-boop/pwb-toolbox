#!/bin/bash
# PreToolUse hook: actually STOP a session that has grown too expensive.
#
# Asked for 2026-09-03: "I need some hard stop to a session that drain my
# session in 5 to 10 mins."
#
# Why this exists when session-size.sh already warns: session-size.sh is a
# UserPromptSubmit hook, and a UserPromptSubmit hook can only inject TEXT. It
# asks the model to mention the cost in one line. That is a smoke detector.
# It fired correctly on 2026-08-24 and again on 2026-09-03 and the session
# kept going both times, because a warning nobody is forced to obey is a
# suggestion. PreToolUse is the only hook type that can refuse a tool call, so
# it is the only place a real stop can live.
#
# WHAT IT DOES: past PWB_HARD_STOP_TOKENS of accumulated context re-reads, every
# tool call is denied except a narrow "save your work" set. The session can
# still commit, push and write files -- it cannot start new work.
#
# WHY NOT BLOCK EVERYTHING: a stop that strands uncommitted work costs more than
# the tokens it saves. git and file writes stay open so the session can land
# what it has and hand over cleanly. Tighten WRAP_UP_OK below to block those too.
#
# ESCAPE HATCH, deliberately present: `PWB_HARD_STOP_OFF=1` in the environment,
# or a `.claude/.hard-stop-off` file. The spend-safety skill's rule is that a
# guardrail must never permanently block legitimate automation -- an unattended
# desk-agent run that trips this must have a way through that does not require
# editing code at 2am.
#
# Every failure path exits 0 in silence. This runs before EVERY tool call;
# breaking the session it protects would be a far worse outcome than not firing.
set -uo pipefail

THRESHOLD="${PWB_HARD_STOP_TOKENS:-15000000}"

# --- escape hatches, checked first and cheaply ------------------------------
[ "${PWB_HARD_STOP_OFF:-0}" = "1" ] && exit 0

INPUT="$(cat 2>/dev/null || true)"
[ -n "$INPUT" ] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[ -f "$ROOT/.claude/.hard-stop-off" ] && exit 0

PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || exit 0

# --- read transcript path + tool name from the hook payload -----------------
read -r TRANSCRIPT TOOL <<<"$(
  printf '%s' "$INPUT" | "$PY" -c '
import json, sys
try:
    d = json.load(sys.stdin) or {}
except Exception:
    raise SystemExit
print(d.get("transcript_path", ""), d.get("tool_name", ""))
' 2>/dev/null || true)"

[ -n "${TRANSCRIPT:-}" ] || exit 0
[ -f "$TRANSCRIPT" ] || exit 0

# --- tools that stay open so work in flight can be landed -------------------
case "${TOOL:-}" in
  Write|Edit|NotebookEdit|TodoWrite|TaskUpdate) exit 0 ;;
  Bash)
    # allow only git/save commands through, not arbitrary shell
    CMD="$(printf '%s' "$INPUT" | "$PY" -c '
import json, sys
try:
    d = json.load(sys.stdin) or {}
except Exception:
    raise SystemExit
print((d.get("tool_input") or {}).get("command", "").strip())
' 2>/dev/null || true)"
    case "$CMD" in
      git\ commit*|git\ add*|git\ push*|git\ status*|git\ diff*|git\ log*) exit 0 ;;
    esac
    ;;
esac

# --- how much context has this session re-read? -----------------------------
TOTAL="$("$PY" - "$TRANSCRIPT" <<'PY' 2>/dev/null || true
import json, sys
t = 0
try:
    with open(sys.argv[1], encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                u = (json.loads(line).get("message") or {}).get("usage") or {}
            except Exception:
                continue
            t += u.get("cache_read_input_tokens", 0) or 0
            t += u.get("cache_creation_input_tokens", 0) or 0
except Exception:
    raise SystemExit
print(t)
PY
)"

case "${TOTAL:-}" in ''|*[!0-9]*) exit 0 ;; esac
[ "$TOTAL" -ge "$THRESHOLD" ] || exit 0

HUMAN="$(( TOTAL / 1000000 ))M"
LIMIT="$(( THRESHOLD / 1000000 ))M"

REASON="HARD STOP: this session has re-read ${HUMAN} tokens of context, past its ${LIMIT} limit.

Every further turn re-reads all of that before doing any work, so this session
is now the expensive part, not the task. New work is blocked here.

Still allowed, so nothing in flight is stranded: git add/commit/push/status/diff/log,
and Write/Edit. Land what you have, tell the owner what is done and what is not,
then STOP and let them open a fresh session for the next task.

Do not try to work around this. Do not spawn a subagent to continue. Say plainly
that the session hit its limit.

Owner override if this fired on something that genuinely must finish here:
set PWB_HARD_STOP_OFF=1, or create the file .claude/.hard-stop-off
Raise the limit permanently with PWB_HARD_STOP_TOKENS (currently ${THRESHOLD})."

# Modern hook contract: JSON deny on stdout.
"$PY" - "$REASON" <<'PY' 2>/dev/null
import json, sys
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": sys.argv[1],
}}))
PY

# Belt and braces: exit 2 with the reason on stderr is the older blocking
# contract. Harnesses that read the JSON above ignore this; ones that do not
# still block. Both mean deny, so there is no path where they disagree.
printf '%s\n' "$REASON" >&2
exit 2
