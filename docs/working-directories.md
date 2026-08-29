# Working in more than one directory

"It can only see one repo" has **two different causes**, they look identical from
a chat, and they have nothing in common. Establish which one you are in before
changing anything — the fix for one does nothing at all for the other.

| What you see | Cause | Fix |
| --- | --- | --- |
| It **asks permission** every time it touches another folder | Local session. The files are right there; the permission is missing. | `permissions.additionalDirectories` in `~/.claude/settings.json` |
| It says the folder **does not exist** | Cloud session. That repo was never cloned into the container. | Ask the session to add the repository by name |

`tools/install_workspace_dirs.py --diagnose` names which one you are in and
writes nothing.

## Cause 1 — the local session that keeps asking

A session can read and edit files in the directory it was launched from. Anywhere
else it prompts, every time. `permissions.additionalDirectories` in user-level
settings applies to **every** project, so that is where a machine-wide answer
belongs.

```bash
python tools/install_workspace_dirs.py --diagnose  # why can a session not see X?
python tools/install_workspace_dirs.py --check     # report the diff, write nothing
python tools/install_workspace_dirs.py             # install
```

**The default registers your home directory**, not a list of repos. That is
deliberate: a list is a snapshot, and it goes stale the day you create repo
number eleven — which is the failure this tool exists to end. Registering the
home directory once covers every repo you will ever make, with nothing to re-run.

`--repos-only` takes the narrow path instead — scan for git repositories and
register exactly those. Precise, and stale on your next `git init`.

### What makes the broad grant safe

Breadth is only defensible because it is paired with **deny rules, which outrank
every allow**. The installer adds them by name:

- `~/.claude/projects/**` — roughly 300 MB of session transcripts carrying SSNs,
  claim numbers and financial detail. This is the one that matters.
- `~/.claude/.credentials.json`, `~/.claude.json` — auth tokens and MCP config.
- `~/OneDrive/.claude/projects/**`, `~/OneDrive/Backups/claude-config/**` — the
  mirrored copies of the same thing. The vault repo itself stays reachable, so
  you can still work in it.
- `~/.ssh/**`, `~/.aws/**` — keys.
- `~/AppData/**` — browser profiles, cookies, app tokens.

Deny governs the **Read and Edit tools only**. A program under one of those paths
still runs: denying `Read(~/AppData/**)` does not stop `python.exe` from
executing, it stops a session reading files there. `--no-blocklist` skips them,
which leaves the broad grant unguarded — there is no good reason to use it.

The installer merges: your own deny rules keep their place and are never
reordered, and a `settings.json` it cannot parse is reported and left untouched
rather than overwritten. That one file holds the model, every permission rule and
every hook on the machine.

## Cause 2 — the cloud session that cannot find the folder

A cloud session (claude.ai/code, or a cloud session in the desktop app) runs in a
container holding only the repositories attached to it. The others were never
cloned. There is no file for a permission to apply to, so **no setting on your
machine can fix this** — and the local installer says so rather than pretending.

The fix is to attach the repo, and the fastest route is to say so in the chat:

> add ray-vault to this session

The session can list the repositories your account can reach and attach any of
them mid-conversation. It cannot see a repository that has not been attached, so
asking is not a formality — it is the whole mechanism. In the desktop app, the
**+** button on a cloud session does the same thing.

Do not keep a list of your repositories in this file. It goes stale silently;
ask the session to enumerate them at read time instead.

That example is not hypothetical: `ray-vault` is the Obsidian vault, and the
route is written up in [vault-route.md](vault-route.md) — including the one rule
that is not obvious from here, which is that a session may read it and must never
push to it.

## The four mechanisms, and what each actually grants

| You want | Use |
| --- | --- |
| Every session, in every project, to read and edit your other repos | `permissions.additionalDirectories` |
| This session to also load another repo's skills, commands and subagents | `/add-dir <path>`, or `claude --add-dir <path>` |
| This session to **move** — the new repo's `CLAUDE.md`, hooks, settings, MCP | `/cd <path>` |
| Your projects visible in the desktop app's left panel | sidebar controls → group by project |

The distinction that trips people: **`additionalDirectories` grants file access
and nothing else.** A directory listed there is readable and editable without
prompts, but Claude Code does not load its `CLAUDE.md`, its skills, its hooks or
its `.mcp.json`. A session started in pwb-toolbox with the trade journal
reachable can edit the journal — and will not know the journal's own rules.

`--add-dir` and `/add-dir` are richer: they load skills, `.claude/commands/` and
subagents from the added directory. They still do not load its `CLAUDE.md` unless
`CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` is set.

## The desktop app's left panel

The sidebar lists **sessions, not folders**. There is no tree of your repos to
populate — a project appears once a session exists in it. What makes it read like
a project list:

- The controls at the top of the sidebar filter by status, project or
  environment, and **group sessions by project**. Grouping is what turns a flat
  session list into a per-directory one.
- **+ New session** (`Ctrl`+`N` on Windows) asks for the folder. That folder is
  the session's project, and it is what the grouping keys on.

So: start one session in each repo, once, with grouping on. After that they stay.

## The version trap

`/cd` needs Claude Code **v2.1.169 or later** to exist at all, and **v2.1.246 or
later** to apply the new directory's settings, hooks, MCP servers and skills
immediately. Between those two versions it moves the session but the new
directory's configuration does not take effect until the session is resumed —
which looks exactly like `/cd` having silently done nothing.

The last version recorded for the owner's machine is **v2.1.235** (noted
2026-08-18 in the `gexio-machine` skill), which sits inside that window. It may
have updated itself since; that is not knowable from a cloud session. Check with
`claude --version` before relying on `/cd`.

## Worktrees are not a second directory

The desktop app gives each session its own git worktree under
`<project-root>/.claude/worktrees/`, so two sessions on the same repo do not see
each other's edits until a commit. That is isolation working as designed, not a
missing directory — and it is why `find_repos` stops at the first repo on a
branch rather than listing every worktree as a project of its own.
