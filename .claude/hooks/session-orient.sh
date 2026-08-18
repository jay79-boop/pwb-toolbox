#!/bin/bash
# SessionStart hook: orient the session before it does anything else.
#
# Deliberately separate from session-start.sh. That script sets up the remote
# container and emits the async-handshake JSON on stdout; mixing prose into the
# same stream would corrupt it. This one only prints context and always runs,
# local and remote alike.
#
# Why it exists: the repo owner runs several unrelated projects and does not
# retain the procedures between sittings. Being told the state of play without
# having to ask for it — and without having to remember the phrasing that asks
# for it — is worth more than any single feature in the repo. Asked for
# 2026-08-18 after two days lost to re-establishing context by hand.
set -euo pipefail

cat <<'ORIENT'
Session orientation for pwb-toolbox — act on this before your first reply.

Open by offering a short catch-up, unprompted, in five lines or fewer:

  - the current branch, and whether it is behind or ahead of main
  - uncommitted or unpushed work sitting in the tree
  - open pull requests and whether their CI is green
  - anything visibly unfinished (a failing test, a TODO left mid-task)
  - one suggested next step

Gather that from `git status`, `git log --oneline origin/main..HEAD` and the
GitHub tools. Do not narrate the commands. If everything is clean, merged and
green, say exactly that in one sentence — a short answer is the good outcome,
not a lazy one.

Then stop and wait. The catch-up is an offer, not a gate: if the owner opened
with a real request, answer that first and fold the state into a single line.

Two standing rules for this owner, expanded on in the gexio-machine skill:

  - They are on Windows and will not enjoy PowerShell. If a step can be run
    from this session, run it. Hand over a command only when the session
    genuinely cannot reach what the step needs, and say which case it is.
  - Describe outcomes back to them, not procedures. They ask for goals; the
    steps are yours to work out.
ORIENT
