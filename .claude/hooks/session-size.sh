#!/bin/bash
# UserPromptSubmit hook: say something when THIS session has grown expensive.
#
# Asked for 2026-08-24: "warn me when it start getting too big."
#
# Why a transcript and not `list_sessions`: the harness already writes per-turn
# usage to the session's own .jsonl as it goes, so this costs nothing -- no API
# call, no tokens, no window. A spend warning that spends the window to tell you
# about the window is not a warning worth having, and on 2026-08-24 four
# concurrent sessions investigating the drain consumed roughly half of it.
#
# Why UserPromptSubmit: stdout here lands in the model's context, so the warning
# reaches the owner in the reply they were already getting rather than in a log
# nobody opens.
#
# It speaks once per tier and then goes quiet until the tier changes. A warning
# repeated on every prompt is wallpaper -- the same reason `night_lab verdict`
# prints nothing on a quiet night.
#
# Every failure path exits 0 in silence. This runs before every single prompt;
# breaking the session it was meant to protect would be a worse outcome than
# missing a warning.
set -uo pipefail

INPUT="$(cat 2>/dev/null || true)"
[ -n "$INPUT" ] || exit 0

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[ -f "$ROOT/tools/spend_watch.py" ] || exit 0

PY="$(command -v python3 || command -v python || true)"
[ -n "$PY" ] || exit 0

read -r TRANSCRIPT SESSION_ID <<<"$(
  printf '%s' "$INPUT" | "$PY" -c '
import json, sys
try:
    d = json.load(sys.stdin) or {}
except Exception:
    raise SystemExit
print(d.get("transcript_path", ""), d.get("session_id", "unknown"))
' 2>/dev/null || true)"

[ -n "${TRANSCRIPT:-}" ] || exit 0
[ -f "$TRANSCRIPT" ] || exit 0

REPORT="$("$PY" "$ROOT/tools/spend_watch.py" session "$TRANSCRIPT" --quiet 2>/dev/null || true)"
[ -n "$REPORT" ] || exit 0

TIER="$(printf '%s' "$REPORT" | head -1 | sed -n 's/^\[\([A-Z]*\)\].*/\1/p')"
[ -n "$TIER" ] || exit 0

STATE_DIR="${TMPDIR:-/tmp}/pwb-spend-watch"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
STATE="$STATE_DIR/${SESSION_ID:-unknown}.tier"
if [ -f "$STATE" ] && [ "$(cat "$STATE" 2>/dev/null)" = "$TIER" ]; then
  exit 0
fi
printf '%s' "$TIER" >"$STATE" 2>/dev/null || true

cat <<REPORT_END

Session-size warning (from .claude/hooks/session-size.sh, not from the owner):

$REPORT

Tell them this in ONE line at the end of your reply, in plain language -- what
the session has cost to carry and whether it is worth continuing here or
starting a fresh one for the next task. Then answer what they actually asked.
Do not restate it on later turns; this fires again only if it gets worse.
REPORT_END
