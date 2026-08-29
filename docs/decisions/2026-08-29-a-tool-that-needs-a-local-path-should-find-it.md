# A tool that needs a local path should find it, not ask for it

*Decided 2026-08-29.*

**Decision:** `tools/obsidian_sync.py` discovers the vault itself. `--vault` is
optional and now only an override. With it omitted, the tool reads Obsidian's own
registry — `obsidian.json`, which records the absolute path of every vault the app
has ever opened — and falls back to scanning for a folder holding `.obsidian/`.
When it finds nothing it prints every location it looked in; when it finds two it
lists both rather than guessing.

**Why:** the old `--vault` was `required=True`, so every run began by asking a human
where their vault was. That question sent the owner off to check their phone and
consider setting up Obsidian Sync, across more than one session. It was never a
question that needed a human: the answer is a JSON file on the same disk the tool is
already reading. `%APPDATA%\obsidian\obsidian.json` on Windows,
`~/Library/Application Support/obsidian` on macOS, `~/.config/obsidian` on Linux.

**The general rule, which is the part worth keeping:** a local path is a *fact about
the machine*, and a tool running on that machine can nearly always read the fact
itself. Prompting for one is a design smell — it converts a lookup into a round trip,
and the round trip is charged to the person, not the program. Before adding a required
path argument, ask what on disk already records it: a config file, a registry, a
well-known location, a `.git` directory. Ask only for what is genuinely unknowable
here — a credential, a decision, a value only a human holds.

**A "not found" must say where it looked.** The failure path prints each candidate
config directory with `[found]`/`[absent]` and each scanned root. That turns "no vault"
from a shrug into a finding: an absent registry in every location is positive evidence
that Obsidian has never run on this machine, which is a different problem with a
different fix than a vault sitting somewhere unexpected.

**It refuses to pick between two vaults.** A run wipes and rewrites `docs/journal`,
so guessing wrong is destructive. Ambiguity prints both paths as ready-to-paste
`--vault "..."` lines — still no hunting, just one choice made by the person who knows
which vault is which.

## The guard added alongside it

`docs/journal` is committed rather than gitignored, and this fork is public — the same
condition that already puts `engagements/`, `spec_desk/`, `night_lab/` and `season/`
in `.gitignore`, and the same one behind
[the vault rules are public; the vault is not](2026-08-29-the-vault-rules-are-public-the-vault-is-not.md),
which records that the vault holds an active legal matter.

So `--commit`/`--push` now refuses to run unless the vault has a `.syncignore`, or
`--allow-publish` is passed explicitly. The `.syncignore` is not itself proof that the
right things are excluded; it is proof that exclusion was *considered*, which is the
most a tool can check. The interlock is on the irreversible half only — a `--dry-run`
or a plain local `sync` never touches it, because mirroring into a working tree is
undoable and a push to a public repository is not: history, forks and GitHub's caches
all keep what was pushed.

Making discovery automatic is exactly what raises this stake. Before, the person naming
the vault path had at least looked at the vault that turn. Now the tool can find and
mirror a vault nobody has thought about, which is convenient in the same motion that it
is dangerous.

## What discovery actually found, and the second fix it forced

Run against the real machine, the registry named two vaults:
`C:\Users\Gexio\OneDrive\.claude` and `C:\Users\Gexio\OneDrive\.claude\Projects`.

That is not a notes folder. It is the owner's Claude configuration repo — hooks, skills,
plans, scheduled tasks — and `Projects/` is the session-transcript tree, which the
`gexio-machine` skill describes as "verbatim chat logs carrying SSNs, claim numbers and
financial detail", kept out of git by the vault's own `.gitignore`.

`obsidian_sync.py` read `.syncignore` and nothing else, so **every protection that
`.gitignore` already provided would have been bypassed** by a mirror into `docs/journal`
on a public fork. A simulation of that vault shape mirrored 4 notes including the
transcripts; with the fix it mirrors 2 and reports the 2 it held back.

So the tool now honours the vault's own git ignore rules, via `git check-ignore` rather
than by parsing `.gitignore` — that gets nested ignore files, negation and precedence
right, and it consults the index, so a file the owner deliberately tracks is still
mirrored. `--no-gitignore` turns it off.

**The general point is reuse over inference.** A vault that is a git repo already carries
a curated list of what must not leave it, maintained by the person who knows why. That
list is a better exclusion set than anything this tool could infer, and it was sitting
there unread. Prefer the safety list a user already maintains to a parallel one that
must be kept in sync by hand.

**And it sharpens the earlier point about discovery.** Automatic discovery is what made
this urgent: the tool can now find, and mirror, a vault nobody has looked at this turn.
Convenience and hazard arrived in the same commit, which is why the guard belongs in the
same one.
