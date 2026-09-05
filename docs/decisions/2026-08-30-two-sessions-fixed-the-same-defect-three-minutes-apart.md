# Two sessions fixed the same defect three minutes apart

*Decided 2026-08-30.*

**Decision:** When two branches carry the same fix, keep the broader one and
close the narrower rather than resolving the conflict between them. Resolving
means choosing between two wordings of one output line, and inheriting the
tests that pin the wording that lost.

[#171](https://github.com/jay79-boop/pwb-toolbox/pull/171) and
[#172](https://github.com/jay79-boop/pwb-toolbox/pull/172) repaired the same
defect, in the same block of the same file, from the same base commit
`61d309c`. #172 merged. #171 was closed unmerged, and this records why it was
closed rather than resolved — and why the obvious lesson to draw from it is the
wrong one.

The defect itself is
[the password prompt asking for a credential that does not exist](2026-08-30-the-password-prompt-asked-for-a-credential-that-does-not-exist.md),
which is #172's own record. This entry is about the collision, not the bug.

## The timeline is the whole point

| | opened | session | files | additions |
| --- | --- | --- | --- | --- |
| #171 | 06:30:46Z | `run_job.ps1 --add-dir and alerts` | 2 | 61 |
| #172 | 06:34:00Z | `Free the desk agent jobs from TradingView Desktop` | 6 | 234 |

**Three minutes and fourteen seconds apart.** Not one session ignoring the
other's open pull request — at the moment either session began this work, the
other's pull request did not exist. They had been running concurrently for
hours; the artifacts became visible only in the last three minutes of it.

Both sessions did check the history, and both cite it correctly. #171 names
[#164](https://github.com/jay79-boop/pwb-toolbox/pull/164), the same assumption
in `autologon.ps1`. #172 names #168 and #170. Neither was careless about prior
work. Prior work was not the problem.

## The obvious rule would not have worked

The first thing this looks like it teaches is *check the open pull requests
before starting on a named defect*. That rule is already satisfied — the
session-start orientation lists open pull requests unprompted, and both
sessions read history and cited it. It would have caught nothing here, because
for almost the entire overlap there was nothing on GitHub to catch.

Writing it down anyway would have been the failure this log already records
once: [a written rule with no check behind it lasted eight
hours](2026-08-24-a-written-rule-with-no-check-behind-it-lasted-eight-hours.md).
A rule that would not have prevented the incident it was written for is worse
than none, because it closes the question.

Nor would the branch names have helped. `claude/run-job-add-dir-alerts-olnud1`
and `claude/laughing-thompson-gifgvx` are named for the task each session
*started* on, not what it ended up containing. Scanning the branch list would
have shown two branches that appear unrelated to this bug and to each other.

## What actually happened

One real-world event reached two sessions. The owner ran the desk-agent
registration, and it asked for a password they do not have. Session
`Free the desk agent...` heard it from the owner directly — its pull request
quotes them: *"yes do the conversion, but I don't have the password. none was
created."* Session `run_job.ps1...` found the same thing in the run's output,
and says so: *"Found in a live run, not by reading."*

Two independent observers of one event, each with the standing instruction to
fix what they find, and no channel between them. Duplication was the correct
behaviour for each of them individually. That is what makes it a structural
problem rather than a mistake either one made.

## The decision

**When two branches carry the same fix, keep the broader one and close the
narrower — do not resolve the conflict.** Resolving it means choosing between
two wordings of the same output, and merging the loser's tests, which then pin
the wording that lost.

Here #172 was broader on every axis that matters. It repairs three copies of
the dead-end advice — the prompt, the scheduler's read-back, and
`autologon.ps1`'s section 3 — where #171 repairs only the read-back. It names
the PIN case at the prompt that does the asking rather than in the summary
printed afterwards. And its
`test_no_advice_anywhere_makes_the_password_the_only_way_out` is a generalised
guard: it scans both scripts for any instruction to supply a password without
ARSO named within a few lines, so a fourth copy cannot appear silently. #171's
two tests pin one block of wording.

Verified before closing: #171's tests fail against merged `main`, because
`main` orders the two routes the other way and states the PIN line in the
prompt. That is a wording difference, not lost behaviour —
`tests/test_desk_agent_launcher.py` is green on `main` at 69 passed, and the
merged note still names ARSO, still says no password is needed, and still
points at `tools/autologon.ps1`.

## What this cost, honestly

One session's work on `tools/register_desk_agent.ps1` and
`tests/test_desk_agent_launcher.py`, discarded. That is the small half.

The larger half is that both sessions were among the five long-lived sessions
that exhausted the 05:40–10:40Z usage window that morning, and both were still
working inside it — #171 last pushed 06:32Z, #172 at 06:34Z. Duplicated work
was not the sole cause of that window running out, and this record does not
claim it was; the measured cause was accumulated context being re-read on every
turn. But the duplicate is a clean example of what the window was being spent
on.

## Left open

**Nothing in this repository can check for this, and no test is added here.**
A duplicate is only visible from above both sessions, and `pytest` runs inside
one checkout. Claiming otherwise would repeat the mistake the section above
declines to make.

The machinery that could address it already exists and was not in use that
morning: the `agent-fleet` skill's project-lead role, whose entire purpose is
one owner per work item. Whether to run the fleet for ordinary desk work is a
cost decision for the owner — a lead session is itself a session that
accumulates context — and it is not decided here.

**A cheaper mitigation, untested:** when a session finds a defect from a live
run, the finding could go to the run log before the fix goes to a branch, since
`tools/desk_agent/runs.jsonl` is committed and is the one place both sessions
already read. That would have given a three-hour warning instead of a
three-minute one. It is written down here as a candidate, not as a rule, and
nothing has been changed to implement it.
