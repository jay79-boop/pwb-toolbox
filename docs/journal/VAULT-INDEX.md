---
type: index
status: active
project: meta
---

# Vault Index (Template)

This is a stripped-down copy of a working vault setup — the tooling/structure layer only, no personal content. Read this file first each session; it links out to everything else that matters once you've filled it in.

## Profile

<!-- Fill in: who the user is, in 1-3 sentences. This is what every session should already know without asking. -->

## Guidelines (working style)

- **Autonomy**: Don't stop to ask permission for reversible, local, low-risk actions — just do them and report afterward. Reserve confirmation for genuinely destructive/ambiguous/external-facing actions.
- **Decision logging**: Any decision made in a session gets recorded in the vault (daily note, plus the relevant topic note if it's standing), not left only in chat.
- **Memory discipline**: if you also use Claude Code's native auto-memory (`~/.claude/projects/.../memory/`), keep both in sync — the vault is the fuller, human-browsable record; auto-memory is the fast, harness-native boot layer. When something durable changes, update both.
- **Daily notes**: Log session summaries in `01 - Daily Notes/`, created fresh each day from `Daily Note Template.md`.
- **Project folders & handoffs**: any project with real working files (not just vault notes) gets a canonical shared root folder and an end-of-session `handoff.md`.
- **New installs from a link**: when a bare GitHub link gets dropped in chat, catalog it immediately and calibrate how deep a pass it actually warrants before building anything. Full rule: [Clone-and-Run Calibration](06 - Working Style\Clone-and-Run Calibration.md).

## System Overview

```
VAULT-INDEX.md              This file — profile, guidelines, links
01 - Daily Notes/            One note per day, per-session log (Index / Decisions / Notes Touched)
04 - Projects/                Project Note Template, GitHub Links Catalog, Cloned Reference Repos
06 - Working Style/           Standing feedback on how to collaborate, incl. Clone-and-Run Calibration
agents/                      Subagent definitions — see Subagent Template.md
skills/                      Custom Claude Code skills
copilot/                     Obsidian Copilot plugin's custom prompt library
```

Add topic folders as needed (numbered prefixes keep them ordered in the file explorer), and list each below under "Topic Notes" once it exists.

## Topic Notes

- [Project Note Template](04 - Projects\Project Note Template.md) — the pattern for a fully-verified project note (frontmatter, status, findings, next step)
- [GitHub Links Catalog](04 - Projects\GitHub Links Catalog.md) — every GitHub link dropped in chat, one place, updated as new ones come up
- [Cloned Reference Repos](04 - Projects\Cloned Reference Repos.md) — consolidation point for repos cloned as reference only, not run/verified
- [Clone-and-Run Calibration](06 - Working Style\Clone-and-Run Calibration.md) — the workflow/decision rule for how deep a pass a dropped link actually warrants, plus a security hook-check
<!-- Add one line per standing note as you create more: - Note Name — one-line description -->

## Vault Rules for AI

- **Frontmatter always.** Every note gets YAML frontmatter (`status`, `project`, `type`) on creation; fix incomplete frontmatter when you're already editing a note, don't stop just to add it to files you're only reading.
- **Append before you create.** Default to adding to an existing note over spinning up a new one — fewer, fuller notes beat many thin ones.
- **Wikilinks**: always link people, named businesses/products/platforms, and any note this one directly depends on or extends. Never link the note's own title, or the same target twice in one note.
- **Folder indexes stay in sync.** A folder with 5+ notes or a distinct topic area gets a `<Folder Name>.md` index listing each note with a one-line description; update it in the same pass as any create/rename/move.
- **Renaming**: do it inside the Obsidian app so `links` auto-repair. A shell rename breaks them — if that happens, find and fix every `old name` reference by hand.
- **Checkpoint persistence.** Whenever something changes that a future session needs to know, persist it without being asked — update the relevant note and today's daily note.
- **Archiving**: only on explicit request — set `status: archived`, move to an Archive folder, confirm what moved and where. Never archive on your own initiative.
- **Daily notes**: at the start of a conversation, check yesterday's note; backfill from context if missing and say it's reconstructed, skip creating one if there's truly no context for that day. When work wraps for the day, offer to create/update today's note — never ask "are you done?", wait for the actual signal.

### Valid Field Values

**status:** `active` | `completed` | `parked` | `idea` | `archived`
**project:** fill in your own project tags as they come up (e.g. `personal`, `work`, `meta`)
**type:** `index` | `reference` | `guide` | `plan` | `log`

## My Preferences for Working with AI

<!-- Fill in your own — this section is what shapes tone/behavior every session. Examples of what goes here: -->
- Plain language, direct, no hedging.
- Autonomy on safe actions — no permission check-ins on reversible, local, low-risk steps.
- Be a partner, not a yes-man — argue your position when you think I'm wrong; only change your answer if the argument actually lands.
- Most guidance is a guideline, not a law, except things explicitly marked Locked.
