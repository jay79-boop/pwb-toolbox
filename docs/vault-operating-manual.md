# Vault operating manual

*Written 2026-08-29.*

How a session should behave in the owner's Obsidian vault: the standing rules, the
operating rules, and the note schema. **This file is canonical for all three.**

## Scope, and what is deliberately not here

This repository is a public fork. The vault's personal half — the owner's profile, an
active legal matter, the topic-note index that names it, and the night-by-night history
of how the vault was built — is **not** in this file and must not be added to it. That
material lives in the local vault and in the private published artifact,
[Vault Operating Manual](https://claude.ai/code/artifact/a07251b6-b7b4-4a5b-a23f-b8fff0d65816),
which carries everything below plus the personal half.

So the two are a subset, not a duplicate: **the rules are canonical here**; the artifact
mirrors them for human reading and adds what cannot be published. When a rule changes,
change it here first.

The same split governs `engagements/`, `spec_desk/`, `night_lab/` and `season/`, all
gitignored for this reason.

## Two memory systems, both live

The vault is the fuller, cross-session, human-browsable record; durable facts go there,
in the right topic note. Claude Code's native auto-memory is **not** retired — it is the
fast, harness-native boot layer read automatically every session. When something durable
changes, update both.

> **Corrected 2026-07-29.** An earlier version of this rule said the auto-memory folder
> had been retired. That was never decided — a concurrent session wrote it — and it has
> been overruled. Neither system is archived in favour of the other.

## Standing rules

**Autonomy on safe actions.** Don't stop to ask permission for reversible, local,
low-risk actions — do them and report afterward. Reserve confirmation for genuinely
destructive, ambiguous, or external-facing actions.

**Log decisions to the vault.** Whenever a decision gets made in a session — the
assistant's own, or one made on the user's behalf — record it. Don't let a decision live
only in chat history, where it is effectively lost to future sessions. Log it in the
day's daily note under that session's *Decisions Made* heading. If it changes a standing
rule, project state, or topic fact rather than being a one-off, also update or create the
relevant topic note and cross-link it. This applies automatically, without being asked.
*(Established 28 July 2026, explicitly as a standing rule.)*

**Canonical project folders and handoffs.** Any project with real working files — code,
docs, assets, not just vault notes — gets one organised root folder. Cowork and Claude
Code both read and write that same folder; no forked copies in two places. At the end of
a session, write a concise `handoff.md` in the project root: current progress, decisions
made, next steps. Short and current, not a full history — the daily note is the
historical log; `handoff.md` is the "start here" snapshot. To jumpstart a new session,
drop that file in rather than re-explaining. Applies once a project has actual files on
disk, not to vault-only topics. *(Established 29 July 2026 as a general practice.)*

**Daily notes.** Log session summaries in `01 - Daily Notes/`, created fresh each day
from the template.

## Operating rules for AI

- **Frontmatter always.** Every note gets YAML frontmatter (status, project, type) on
  creation. Fix incomplete frontmatter when you are already editing a note; don't stop
  just to add it to files you are only reading.
- **Append before you create.** Default to adding to an existing note over spinning up a
  new one — fewer, fuller notes beat many thin ones.
- **Wikilinks.** Link people, named businesses, products and platforms, and any note this
  one depends on or extends. Never link the note's own title, or the same target twice.
- **Folder indexes stay in sync.** A folder with five or more notes, or a distinct topic
  area, gets a `<Folder Name>.md` index listing each note with a one-line description.
  Update it in the same pass as any create, rename, or move.
- **Renaming.** Do it inside the Obsidian app so links auto-repair. A shell rename breaks
  them — if that happens, fix every stale reference by hand.
- **Checkpoint persistence.** Whenever something changes that a future session needs to
  know, persist it without being asked: the relevant note, today's daily note, and — only
  for a new always-on rule — `CLAUDE.md`.
- **Archiving.** Only on explicit request. Set `status: archived`, move to an Archive
  folder, confirm what moved and where. **Never archive on your own initiative.**
- **Daily notes.** At the start of a conversation, check yesterday's note; backfill from
  context if missing and say it is reconstructed; skip creating one if there is truly no
  context for that day. When work wraps, offer to create or update today's note — never
  ask "are you done?", wait for the actual signal.
- **Living profile.** Update profile and topic notes silently as you learn new things, and
  log what changed in the day's *Profile Updates* section. A passing mention is not a
  personality trait — on contradiction, update the existing entry rather than adding a
  duplicate.

## Note schema

| Location | What lives there |
| --- | --- |
| `VAULT-INDEX.md` | Profile, guidelines, links |
| `01 - Daily Notes/` | One note per day; per-session log (Index / Decisions / Notes Touched) |
| `02 - Profile/` | Who the user is |
| `03 - Legal/` | Active legal matters |
| `04 - Projects/` | Side projects and subagents |
| `05 - Graphify/` | graphify setup, known bugs and issues |
| `06 - Working Style/` | Standing feedback on how to collaborate |

| Field | Accepted values |
| --- | --- |
| `status` | `active` · `completed` · `parked` · `idea` · `archived` |
| `project` | `personal` · `legal` · `farm-business` · `build-log` · `graphify` · `meta` |
| `type` | `index` · `reference` · `guide` · `plan` · `log` |

Open work lives in `Active Priorities` at the vault root. Check it at the start of every
conversation, and verify an item's real state before acting on it.

## Working preferences

- Plain language, direct, no hedging.
- Autonomy on safe actions. No permission check-ins on reversible, local, low-risk steps.
- **Be a partner, not a yes-man.** Argue your position when you think the owner is wrong;
  only change your answer if the argument actually lands.
- Most guidance is a guideline, not a law — except anything explicitly marked **Locked**.
- **The owner drives the trust-and-access ramp.** Never propose expanding your own access;
  default to scoping down.

## Provenance

The vault structure is adapted from the ai-memory-vault template by Jared Rhodenizer,
CC BY-NC-SA 4.0, used for personal, non-commercial purposes. Migrated 28–29 July 2026
from Claude Code's native auto-memory; the original memory files were left in place and
kept live.
