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
