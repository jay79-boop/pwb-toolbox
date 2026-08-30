# Retiring `build-puzzle-process`, and what its "vendored" label was hiding

*Decided 2026-08-29.*

**Decision:** `.claude/skills/build-puzzle-process/` is deleted. The repo keeps
nine skills of its own plus `ui-ux-pro-max`. Its content is recoverable in full:

    git show 0b168fd:.claude/skills/build-puzzle-process/SKILL.md

**Why it went, against the retirement rule in `docs/skills.md`:**

- **It had collapsed into another skill** — criterion 3, verbatim. `git log`
  over the directory returns exactly one commit, `0b168fd`, *"Extract a
  process-mapping standard and make the tools able to hold it"*. That is the
  same commit that created `process-mapping`. The general skill was extracted
  *from* this one, and the source was kept beside its own replacement.
- **Its trigger condition could not be met here.** The description opens "ONLY
  load this when the Puzzle MCP is actually connected". `.mcp.json` declares one
  server, 21st. No Puzzle MCP is configured, and the only other mentions of
  puzzleapp.io in the repo are design credit for `static/flow-canvas.html`,
  which `docs/specs/2026-08-22-flow-canvas-design.md` records as a clean-room
  redesign rather than an integration.
- **It was the collision problem, not an example of it.** `docs/skills.md`
  already named it as the shape of the trigger-collision cost: 79 words of
  always-loaded description, of which two of the three sentences exist to steer
  the model away. A skill whose description argues against its own trigger is
  paying rent to be ignored.

**The part worth keeping is the label, not the skill.** It sat in the `VENDORED`
set in both `tests/test_skills.py` and `tools/front_door.py`, and `docs/skills.md`
asserted it "tracks upstream" and is "restored by `uipro init`". None of that was
true. `ui-ux-pro-max` arrived 2026-08-17 via a different pull request; this
arrived by hand six days later, and no installer would ever have put it back.

That mislabel had a cost beyond tidiness. `VENDORED` is the exemption from
`test_description_stays_inside_its_budget` and from the total-budget check, so
the entry made its own always-loaded cost invisible to the test that exists to
measure it: the suite reported 641 of 1000 words spent while the real figure was
720. **An exemption that is also a measurement blind spot will hide precisely the
thing it should surface.** So the exemption is now stated as a checkable claim
about provenance — a skill is vendored if re-running its installer restores it,
and `git log` over the directory settles it — rather than a word applied by hand.

**The other nine were reviewed and kept.** All were added between 2026-08-22 and
2026-08-24, so criterion 1 ("has not fired in months") cannot retire any of them
yet; every path they name resolves; and no tool one drives has been removed or
rewritten. The pair to watch next time is `steward` and `spend-safety`, which
both descend from the 2026-08-24 window exhaustion. They are kept apart on
purpose: `spend-safety` guards *money* — a card, a broker order, a metered API —
while `steward` guards the *usage window*, which is not billed and cannot be
bought back. The distinction is load-bearing and both descriptions state it, so
they are not yet one skill with a section each.
