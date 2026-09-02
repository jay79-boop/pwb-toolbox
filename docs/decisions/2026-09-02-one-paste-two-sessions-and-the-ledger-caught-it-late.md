# One paste, two sessions, and the ledger caught it thirty seconds late

*Decided 2026-09-02.*

**Decision:** read the Action Ledger *before opening a pull request*, not only
when appending to it. That is the check that would have caught this collision,
and it is the check that did — six minutes and one wasted PR too late.

[#178](https://github.com/jay79-boop/pwb-toolbox/pull/178) and
[#179](https://github.com/jay79-boop/pwb-toolbox/pull/179) were two clients for
the same NVIDIA vision endpoint, built by two sessions from the same pasted
snippet. #178 survived. #179 was closed, with two of its behaviours ported
across first.

| | opened | branch | shape |
| --- | --- | --- | --- |
| #178 | 01:03:14Z | `claude/nvidia-api-vision-mjkawb` | `tools/nvidia_vision.py`, 726 lines, 46 tests |
| #179 | 01:09:11Z | `claude/nvidia-api-vision-n3gc8c` | `pwb_toolbox/vision/`, 4 modules, 38 tests |

**Five minutes and fifty-seven seconds apart**, on branch names differing only
in their random suffix.

## This is not the 2026-08-30 case, and the difference is the useful part

[Two sessions fixed the same defect three minutes apart](2026-08-30-two-sessions-fixed-the-same-defect-three-minutes-apart.md)
records the previous collision. There the cause was structural: one real-world
event reached two sessions through different routes, with no shared artifact
between them. That record establishes, correctly, that "check the open pull
requests first" would have caught nothing, because for almost the whole overlap
there was nothing on GitHub to check.

Here the cause is at the source. **One input reached two sessions because it was
pasted into two sessions.** The owner confirms it was an accident — a re-send,
not a deliberate pair of takes.

That distinction matters because it removes the temptation to draw the same
conclusion twice. The previous record's mitigation candidate was the desk
agent's run log, on the reasoning that a finding should reach a shared, committed
place before a branch does. That reasoning is right and it generalises, but the
run log is the wrong artifact for anything that did not come from a desk-agent
run. The general one already exists.

## The check that actually fired

The collision was found by reading the Action Ledger — not by searching GitHub,
not by scanning branches, and not from anything in this repository. The other
session had written its rows there minutes earlier, including an open item
naming its own PR.

**And it fired for the ordinary reason, not a clever one.** `CLAUDE.md` requires
a session to append its NEEDS YOU items to the ledger, so the ledger is read at
the end of nearly every substantial piece of work. That read is what surfaced
`"Review + merge PR #178"` in a session that had just opened #179.

So the ledger is already the one channel both sessions reliably touch. It was
consulted thirty seconds after #179 was opened instead of thirty seconds before,
and that is the entire cost: the work was already done either way, but the
duplicate pull request, its description, and its watch subscription were not
needed.

## Would reading it earlier have worked?

**Before starting: no, and this record does not claim otherwise.** #178's rows
did not exist when #179's session began — the same "nothing to catch" that the
2026-08-30 record establishes. A rule that would not have prevented the incident
it was written for is worse than none.

**Before opening the pull request: yes, demonstrably.** By 01:09 the rows were
there, and reading them is what settled it. That is a narrow claim about a
narrow moment, which is why the decision is stated at exactly that moment and
not one step earlier.

## What the previous decision got right, and one refinement

*Keep the broader one and close the narrower; do not resolve the conflict between
them.* That held. #178 was kept whole — its retry path, its `models` subcommand,
its PNG-first shrink ladder, its 46 tests, its wording. #179 was closed and none
of its tests or prose came across.

**The refinement: closing the narrower PR does not mean discarding a behaviour it
uniquely had.** #179 read an image's mime type off its magic bytes rather than
its extension, and accepted raw `bytes` as a source. Neither is a wording
difference, and neither conflicts with anything in #178 — so both were re-added
to #178 with new tests written against #178's conventions. The rule the previous
record was protecting against is *inheriting tests that pin the wording that
lost*, and porting a behaviour with a fresh test is not that.

The extension-versus-bytes one was a real defect in #178: a screenshot pipeline
writing JPEG bytes into a `.png` sent `data:image/png;base64,` in front of a
JPEG. The endpoint sniffs the content too and usually forgives it, which is
exactly why it would have gone unnoticed until it did not.

## Left open

**No test is added, and none can be.** A duplicate is visible only from above
both sessions; `pytest` runs inside one checkout. The 2026-08-24 record on
[a written rule with no check behind it](2026-08-24-a-written-rule-with-no-check-behind-it-lasted-eight-hours.md)
applies, and the honest position is that this rule has no enforcement behind it —
only the fact that it has now fired once, on the incident it was written for,
which is more than the rule it replaces could say.

**Not decided here:** whether pasting into a session should be routed through
anything at all. The owner works from a phone over Remote Control and from the
desk, and a re-send is a normal consequence of that. Adding ceremony to a paste
would cost more than the two collisions have.
