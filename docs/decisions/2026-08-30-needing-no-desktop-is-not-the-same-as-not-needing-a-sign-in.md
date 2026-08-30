# Needing no desktop is not the same as not needing a sign-in

*Decided 2026-08-30.*

`tools/autologon.ps1` shipped in
[#163](2026-08-29-the-jobs-stopped-needing-a-desktop-so-the-tasks-stopped-needing-one.md)
with a report that contradicted itself. The first run on the owner's actual
machine found it in about ninety seconds, which is roughly ninety seconds
faster than every check applied to it in the container.

## What it printed

The password prompt had been declined -- pressing enter, which the script
itself offers and which registers everything `Interactive`. The report then
said, in section 1:

> Neither route is needed as things stand: every registered task runs on a
> stored credential and needs no desktop.

and in its summary:

> Nothing here is broken, and nothing needs signing in: no registered task
> needs a desktop, so they run whether you are logged on or not.

Both false. Four lines above the summary, its own section 3 had it right:

> PWB-DeskAgent-Journal  LogonType: Interactive  ...  no desktop needed
>       This job needs no desktop but is registered Interactive, so it
>       still only runs while you are signed in.

## The mistake

Two facts, treated as one:

| | question |
| --- | --- |
| `$desktopNeeded` | does any registered task still **drive the chart**? |
| `$signInMatters` | does any registered task still **depend on a sign-in**? |

Only the first was computed. Sections 1 and the summary then answered the
second one from it.

They come apart exactly when a job needs no desktop but is still registered
`Interactive` -- which is the **default** state, and the state after declining
the prompt. Needing no chart does not free a job from the sign-in; carrying a
stored credential does. The script was reporting the conversion it had offered
rather than the machine it had just finished reading.

This is the same failure the file already records twice and was explicitly
written against: an unchecked thing rounded up to a passing one. It arrived
here from a new direction -- not by skipping a check, but by letting one check
answer a question it had not been asked.

## The fix

`$signInMatters` is computed alongside `$desktopNeeded`, off each task's actual
`LogonType`, using the same `S4U`/`Password` test section 3 uses -- one
definition of "runs without a desktop", so the two halves of the report cannot
drift apart again. Only `$signInMatters` may retire the sign-in.

And the declined case is **named rather than passed over**: when nothing needs
a chart but the tasks are `Interactive`, the summary says the conversion has
not been applied and keeps sections 1 and 2 live. Going quiet would have been
the same bug wearing a politer face, because silence reads as success.

## What the tests are worth, honestly

Four breaks convict: gating the summary branch on `$desktopNeeded`, gating the
section 1 note the same way, collapsing the two facts in the loop, and
softening the "has NOT been applied" line into silence.

**The first two assertions written for this did not convict, and the break
found that too.** One searched for `$signInMatters` within 600 characters of
the claim -- proximity, which passes happily when a *different* branch mentions
the variable nearby. The other checked that `$desktopless` appeared anywhere in
the setup block, which stays true when the variable is still assigned and no
longer read: presence is not use. Both were rewritten to pin the branch
*condition* and the *assignment line*, and only then did the breaks fail.

Worth recording because a test that cannot fail is worse than no test: it
occupies the slot where a real check would go, and it reports as green. The
convict step is what caught it, and it only caught it because the break was
verified to have applied -- the first attempt at these breaks silently matched
nothing and reported a clean pass.

## Left open

**Still nothing here executes PowerShell.** The container has no Windows host,
so this fix is checked by reading, by the ASCII and brace-balance tests, and by
structural assertions over the source. The bug it repairs was found by running
the script, not by any of that. The next real run is the test.

**The conversion itself is still not applied on the machine.** Both tasks are
`Interactive` by the owner's choice at the prompt. That is a supported end
state -- it stores no new credential and the jobs work exactly as before -- and
the report now says so plainly instead of claiming otherwise.
