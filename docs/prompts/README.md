# Prompt archive

Long prompts that get retyped, kept so a skill can be distilled from what was
actually asked rather than from a guess at it.

**This is a staging area, not a library.** Nothing reads these files
automatically. A prompt earns a place here the first time you notice you are
writing it again; it earns a skill the third time. Once it is a skill, delete
the file — the skill is the live copy and two copies drift.

## What to paste

The prompt as you actually typed it, warts included. The awkward phrasing is
the useful part: it shows which step you did not trust the session to infer,
and that step is usually the one the skill needs to spell out.

Do **not** clean it up first. A tidied prompt is a guess about what you meant,
which is the thing this folder exists to avoid.

## Format

One file per recurring job, `kebab-case.md`, and only two things in it:

```markdown
# <the job, in a few words>

Asked roughly: 4 times. Last: 2026-08-24.

---

<the prompt text, verbatim>
```

That is the whole convention. The count is the only field that matters — it is
what tells you when the job has crossed the bar in `docs/skills.md`.

## Nothing sensitive

This fork is public. Account numbers, position sizes, API keys, broker
details and anything from the trade journal do not go in a prompt file — strip
them or leave the prompt out. The gitignored data directories (`season/`,
`spec_desk/`, `night_lab/`, `engagements/`) exist for that kind of content;
this folder is tracked.
