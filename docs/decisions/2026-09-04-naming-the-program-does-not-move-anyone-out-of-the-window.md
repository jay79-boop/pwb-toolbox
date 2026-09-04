# Naming the program does not move anyone out of the window

*Decided 2026-09-04.*

The barehands handoff in #192 failed on its first paste. A Claude Code prompt was
handed over as a fenced block under *"Open Claude Code, then paste this"*. It
went into PowerShell at `C:\Users\Gexio>`, which parsed the parentheses as a
subexpression:

```
Missing argument in parameter list.
    + CategoryInfo          : ParserError
```

Same error text as the 2026-08-29 indented-prose incident, from a different
cause. Nothing ran and nothing broke; the step stalled and the reply had to be
sent again.

## Why the existing rules did not catch it

Both were followed.

- The neighbouring `CLAUDE.md` bullet — *never indent prose underneath a
  checkbox* — stops prose being **accidentally** pasteable. That is not what
  happened here. The block was deliberate, and its text was genuinely meant for
  another program.
- The `gexio-machine` skill already requires that each step *"names the program
  it goes into and how to open it"*. It did: "Open Claude Code (Start menu, type
  `Claude Code`, press Enter), then paste this."

The gap is that **naming the program inside the same step does not move anyone
out of the window already in front of them.** The instruction to switch and the
thing to paste arrived together, so the paste landed wherever the cursor already
was.

## The rule

The switch gets its own step and its own visible success condition — a bordered
box replacing the prompt, say — and the paste is the step after it. Something
they see happen, rather than something they remember to do. The one-line form
lives in `CLAUDE.md` under "Flagging action items".

## Not covered here

The `gexio-machine` skill carries the copy of these format rules that reaches
sessions **outside** this repository, and a cloud session cannot durably edit
it. That copy needs a local write plus a re-upload; it is in the handoff to the
owner.
