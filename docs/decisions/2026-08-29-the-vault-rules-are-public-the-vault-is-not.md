# The vault rules are public; the vault is not

*Decided 2026-08-29.*

**Decision:** The Obsidian vault's *operating* half — standing rules, the nine AI
operating rules, the note schema and field values, and the working preferences — is
canonical in this repository at `docs/vault-operating-manual.md`. Its *personal* half —
the profile, an active legal matter, the topic-note index that names it, and the
night-by-night history of how the vault was built — stays out, and lives only in the
local vault and in a private published artifact.

**Why:** a session working in this repo needs the rules and cannot reach the vault. It
has no use at all for the profile. This fork is public, which is already why
`engagements/`, `spec_desk/`, `night_lab/` and `season/` are gitignored; the same line
applies here. Splitting by *what a session needs* rather than by *what the document
contains* lets the useful half be committed without the rest.

**The relationship, stated so it cannot drift:** the repo file is canonical for the
rules. The artifact mirrors them for human reading and adds what cannot be published,
and carries a banner saying so with a date. Change a rule here first. This is the one
sanctioned two-copy arrangement in the project, and it is sanctioned only because the
copies have different audiences and the subset relationship is written on both.

**What it costs:** the artifact can go stale against this file. Accepted, because the
alternative is either publishing an active legal matter to a public fork or having no
committed copy of rules that every session needs.

## The finding that produced this

A session was asked to merge duplicated published artifacts. It began from a container
whose `main` was cloned at 2026-08-23 — one day before `1426bf7` split the ledger — and
was handed that same pre-split `CLAUDE.md` as its startup context. It then spent the
session designing and building a ledger split: `docs/decisions/`, a fleet registry, a
canonical-home table. All of it already existed on `main`, done better: the real split
is three ways (state, decisions, skills), not two.

The redundant work was caught only because the pull request came back
`mergeable_state: dirty` and the conflict was inspected rather than resolved. Resolving
it blind would have merged 15 duplicate decision files and reverted `CLAUDE.md` to a
five-day-old base.

**So: a stale checkout is not a stale file, it is a stale worldview.** Nothing in the
session contradicted itself; the reasoning was sound against the copy it held. The
lesson is procedural rather than architectural, and it is cheap: **before designing
anything that touches `CLAUDE.md` or the ledger, run `git fetch origin main` and diff
against it.** The startup context is a snapshot, and a snapshot has a date.
