---
status: active
project: meta
type: guide
---

# Clone-and-Run Calibration

The workflow for when a bare GitHub link gets dropped in chat with no other instruction: don't default every link to a full clone-run-verify-build-a-skill pass — check purpose and feasibility first.

## The workflow

1. **Catalog it immediately** in [GitHub Links Catalog](..\04 - Projects\GitHub Links Catalog.md), regardless of what happens next.
2. **Ask what's wanted** if it's not obvious: clone-and-run / just-clone-for-reference / research-first. Don't assume "clone and run" always warrants the full treatment.
3. **Decide the depth of pass:**
   - **Full build/verify/skill pass** — if it's a genuine runnable tool with plausible personal utility (a CLI, a service, something actually usable). Install it, run its real test suite or a real interaction (not just reading the README), note actual findings (platform quirks, credential blockers), and write it up as its own [project note](..\04 - Projects\Project Note Template.md).
   - **Clone-only reference** — if it needs infrastructure not available locally (GPU, specific hardware, heavy downloads) for its primary path, or it's pure demo/research/example code with no clear fit to an active project. Log it in [Cloned Reference Repos](..\04 - Projects\Cloned Reference Repos.md) instead of spinning up a full project note. Say plainly that it needs infra you don't have — don't force it, and don't install new heavy dependencies speculatively to make it work.
4. **Push back when the fit isn't obvious.** It's fine — good, even — to directly ask "why do you need this" when a request doesn't obviously connect to known projects. Executing on autopilot burns effort on repos that don't end up mattering.

**Why this exists:** after several repos in a row got the full build-verify-skill treatment on autopilot, one turned out to have no real personal use case and needed infrastructure the machine couldn't realistically support anyway. Checking first — or just asking — avoids wasted effort without meaningfully slowing down the links that do warrant the full pass.

## Security check — add to every pass, not just when asked

Before treating a freshly cloned repo as safe to explore:
- Check whether it ships a `.claude/` (or equivalent agent-config) directory with hook definitions — a hook (`PreToolUse`/`PostToolUse`/`SessionStart`, or an equivalent lifecycle hook in whatever agent tooling you're using) is the one thing a cloned repo can plant that executes automatically inside a session. This is a cheap check and catches the one risk that's both checkable and would actually execute automatically.
- A lighter, secondary check: grep first-party source (excluding `node_modules`/`.venv`/`dist`/`build`) for install/postinstall/prepare scripts and obvious exfil/malware patterns (webhook exfil, obfuscated eval/base64-exec, crypto-miner strings, curl-piped-to-shell). Weaker signal than the hook check — real supply-chain risk mostly hides in the dependency tree, which can't be manually audited — but still worth a pass.
