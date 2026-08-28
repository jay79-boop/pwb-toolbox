# Working in more than one directory

A Claude Code session can read and edit files in the directory it was launched
from, and nowhere else. Everything below is a way around that, and they are not
interchangeable — three of the four grant file access only, and picking the wrong
one is why a new chat keeps behaving as though pwb-toolbox is the whole world.

## The four mechanisms

| You want | Use | Lasts |
| --- | --- | --- |
| Every session, in every project, to read and edit your other repos | `permissions.additionalDirectories` in `~/.claude/settings.json` | forever, every project |
| This session to also load another repo's skills, commands and subagents | `/add-dir <path>`, or `claude --add-dir <path>` at launch | this session |
| This session to **move** to another repo — its `CLAUDE.md`, hooks, settings, MCP servers | `/cd <path>` | this session, until you move again |
| To see your projects in the desktop app's left panel | sidebar controls → **group by project** | a display setting |

The distinction that matters: **`additionalDirectories` grants file access and
nothing else.** A directory listed there is readable and editable without
prompts, but Claude Code does not load its `CLAUDE.md`, its skills, its hooks or
its `.mcp.json` from it. So a session started in pwb-toolbox with the trade
journal registered can open and edit the journal — but it will not know the
journal's own rules unless you `/cd` there.

`--add-dir` and `/add-dir` are the richer ones: they do load skills,
`.claude/commands/` and subagents from the added directory. They do **not** load
its `CLAUDE.md` unless `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1` is set.

## The tool

`tools/install_workspace_dirs.py` writes the first row of that table for you. It
scans for git repositories, prints what it found, and registers them in
user-level settings so **every** new chat can reach them:

```bash
python tools/install_workspace_dirs.py --scan    # list repos, touch nothing
python tools/install_workspace_dirs.py --check   # report the diff, write nothing
python tools/install_workspace_dirs.py           # install
python tools/install_workspace_dirs.py --prune   # also drop entries that are gone
```

It merges rather than replaces — your model, permission rules and every hook on
the machine live in that same file — backs the file up before writing, and is
idempotent. `--add PATH` registers a folder that is not a repo (the trade
journal, say). `--root PATH` and `--depth N` widen the search.

**It skips `.claude` directories on purpose.** `~/.claude/projects` is roughly
300 MB of session transcripts carrying SSNs, claim numbers and financial detail,
and this key grants *unprompted* read access to every session on the machine.
`--include-claude` overrides that; think before using it.

**It has to run locally.** A cloud container's `~/.claude` is reclaimed when the
session ends, so a cloud session cannot install anything durable. Running it in
one prints the reason rather than pretending to succeed.

## The desktop app's left panel

The sidebar lists **sessions, not folders**. There is no tree of your repos to
populate — a project appears in the panel once a session exists in it. What makes
it read like a project list:

- The controls at the top of the sidebar filter by status, project or
  environment, and **group sessions by project**. Grouping is the setting that
  turns a flat session list into a per-directory one.
- **+ New session** (`Ctrl`+`N` on Windows) asks for the folder. That folder is
  the session's project, and it is what the grouping keys on.
- `Ctrl`+`Tab` and `Ctrl`+`Shift`+`Tab` cycle sessions; `Ctrl`+click opens a
  second one in a split pane.

So the way to get all your repos into the panel is to start one session in each,
once, with grouping on. After that they stay.

The `+` button that adds multiple repos to a single session is a **cloud**
session feature — it is the desktop equivalent of `--add-dir`. Local sessions get
the same reach from `additionalDirectories` or `/add-dir`.

## The version trap

`/cd` needs Claude Code **v2.1.169 or later** to exist at all, and **v2.1.246 or
later** to apply the new directory's settings, hooks, MCP servers and skills
immediately. Between those two versions it moves the session but the new
directory's configuration does not take effect until the session is resumed —
which looks exactly like `/cd` having silently done nothing.

The last version recorded for the owner's machine is **v2.1.235** (noted
2026-08-18 in the `gexio-machine` skill), which sits inside that window. It may
have updated itself since; that is not knowable from a cloud session. Check with
`claude --version` before relying on `/cd`, and update if it is below 2.1.246.

## Worktrees are not a second directory

The desktop app gives each session its own git worktree under
`<project-root>/.claude/worktrees/`, so two sessions on the same repo do not see
each other's edits until a commit. That is isolation working as designed, not a
missing directory — and it is why `find_repos` stops at the first repo on a
branch rather than listing every worktree as a project of its own.
