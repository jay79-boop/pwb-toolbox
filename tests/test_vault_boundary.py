"""Keep the Obsidian vault's contents out of this public fork.

`jay79-boop/ray-vault` can be attached to a session in about a minute now --
`docs/vault-route.md` has the route. That is new, and it is useful, and it is
also the first time a session working in this repository has had the vault's
personal half sitting readable on the same disk as a public checkout.

The rule that follows from it is one-way: **read from the vault, never copy into
this repo.** A rule with nothing checking it lasts until the first session that
did not read it, which is the whole finding behind
`docs/decisions/2026-08-24-a-written-rule-with-no-check-behind-it-lasted-eight-hours.md`.
So this file is the check.

What it does and does not reach is worth saying plainly, because a guard trusted
past its reach is worse than no guard. It catches the vault arriving as *files*
or as *quoted paths* -- a mirror, a `git add -A` after a sync, a snippet pasted
with its source path still attached. It cannot catch prose retyped out of a
vault note with no filename and no path on it. Nothing static can. That half is
carried by the doc and by `.claude/skills/vault-route/`.

**No fingerprint here is a secret.** The vault's signature is structural -- a
numbered topic folder, a fixed set of root index files -- so every check is
written against the *shape* and never against the content. A blocklist of the
sensitive strings would itself be the leak it exists to prevent.
"""

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The vault's top-level folders are numbered: `NN - Title/`, eleven of them.
# Nothing in this repository is named that way, which is what makes the shape
# usable as a fingerprint at all.
#
# Two details are load-bearing. The trailing slash separates a path from prose
# that merely opens with a number. And the lookbehind rejects a date -- this
# repo names decisions `2026-08-24-...`, and without it every one of them would
# read as a vault folder.
VAULT_DIR = re.compile(r"(?<![\w-])\d\d - [A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)*/")

# For file *contents* the bar is one notch higher, and the distinction is the
# useful part of this whole file: **naming a vault folder is structure, naming a
# note inside one is content.** `docs/vault-operating-manual.md` is the
# sanctioned public copy of the vault's rules, and a rule cannot say where daily
# notes go without saying `01 - Daily Notes/`. A note path underneath it is a
# different act -- nothing in this repo has any business citing one.
#
# This was not the first draft. The first flagged the operating manual, and
# exempting that file wholesale would have blinded the check to every real leak
# in the one file most likely to grow them.
VAULT_NOTE = re.compile(VAULT_DIR.pattern + r"[A-Za-z0-9]")

# The vault's root markers, by name. Structural, not content.
VAULT_ROOT_FILES = frozenset(
    {
        "VAULT-INDEX.md",
        "TOPIC-INDEX.md",
        "Active Priorities.md",
        "Next Actions.md",
        "Daily Note Template.md",
    }
)

# Files allowed to carry the fingerprint, because carrying it is their job.
# Keep this set at one. A second entry means the vault's shape has started
# spreading through the repo, which is the thing being guarded against -- so
# adding to it is a decision, not a fix.
FINGERPRINT_ALLOWED = frozenset({"tests/test_vault_boundary.py"})

# Read every tracked file, but not an arbitrarily large one. 4 MB is far above
# anything committed here and far below a vault mirror worth hiding in.
MAX_READ_BYTES = 4 * 1024 * 1024


def _tracked_files():
    """Every path git tracks. `git ls-files`, not a walk of the disk.

    What matters is what is *committed*: the vault clone itself lives outside
    the checkout, and an untracked scratch copy is not a leak until it is added.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _first_fingerprint(text):
    """The first vault-shaped *folder* in `text`, or None."""
    match = VAULT_DIR.search(text)
    return match.group(0) if match else None


def _first_note_path(text):
    """The first vault-shaped *note path* in `text`, or None."""
    match = VAULT_NOTE.search(text)
    return match.group(0) if match else None


def test_there_are_tracked_files_to_check():
    """Guards the enumeration itself: a silent zero passes everything below."""
    assert len(_tracked_files()) > 100


def test_no_vault_shaped_directory_is_tracked():
    """The whole-mirror case: a sync ran, then someone staged everything."""
    offenders = [p for p in _tracked_files() if _first_fingerprint(p)]
    assert not offenders, (
        f"Tracked paths look like vault folders: {offenders[:5]}. This fork is "
        f"public and the vault is not -- see docs/vault-route.md."
    )


def test_no_vault_root_index_file_is_tracked():
    """The partial-mirror case: the vault's own index files came along."""
    offenders = [
        p for p in _tracked_files() if pathlib.PurePosixPath(p).name in VAULT_ROOT_FILES
    ]
    assert not offenders, (
        f"Tracked files carry the vault's root index names: {offenders}. Those "
        f"belong only in the vault -- see docs/vault-route.md."
    )


def test_no_tracked_file_quotes_a_vault_path():
    """The snippet case: content pasted in with its source path attached."""
    offenders = []
    for rel in _tracked_files():
        if rel in FINGERPRINT_ALLOWED:
            continue
        path = ROOT / rel
        if not path.is_file() or path.stat().st_size > MAX_READ_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary, or a symlink to nowhere: no prose to leak
        found = _first_note_path(text)
        if found:
            offenders.append(f"{rel}: {found!r}")
    assert not offenders, (
        f"Tracked files quote vault paths: {offenders[:5]}. Cite the vault by "
        f"repo name, never by note path -- see docs/vault-route.md."
    )


def test_docs_journal_stays_gitignored_and_untracked():
    """Pins the 2026-08-29 decision not to mirror the vault into this repo.

    `tools/obsidian_sync.py` still works and is still correct; what was wrong
    was pointing it here. The ignore is what makes that enforced rather than
    remembered, so a later branch cannot quietly drop it.
    """
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "docs/journal/note.md"], cwd=ROOT
    )
    assert ignored.returncode == 0, (
        "docs/journal/ is no longer gitignored. An accidental sync followed by "
        "`git add -A` could now publish the vault -- see "
        "docs/decisions/2026-08-29-a-tool-that-needs-a-local-path-should-find-it.md."
    )
    tracked = [p for p in _tracked_files() if p.startswith("docs/journal/")]
    assert not tracked, f"docs/journal/ has tracked files: {tracked[:5]}."


# -- the fingerprint itself, convicted and acquitted on planted text ----------
#
# The checks above pass trivially on a clean tree, so they cannot tell a working
# regex from one that matches nothing. These can.

CONVICT_FOLDER = [
    "00 - Foundation/values.md",
    "06 - Working Style/Pending Lessons.md",
    "08 - Electrical Trade School/resume.md",
    "read `10 - Agent Reports/nightly.md` for the rest",
    "/home/user/ray-vault/03 - Legal/matter.md",
    "01 - Daily Notes/",  # a bare folder is still a folder
]

ACQUIT_FOLDER = [
    # this repo's own decision filenames, the near-miss that matters most
    "docs/decisions/2026-08-24-guard-the-irreversible-path-not-the-whole-surface.md",
    "2026-08-29 - Retired/",  # a date, even spaced and capitalised
    "PowerShell 5.1 - Windows",  # no trailing slash: prose, not a path
    "24 - the day the window died/",  # lowercase: prose again
    "tools/spec_desk.py",
    "the run took 28s",
]

# The line the content check actually draws. Everything in ACQUIT_FOLDER is
# acquitted here too, by construction -- these are the cases where the two
# checks deliberately disagree.
CONVICT_NOTE = [
    "06 - Working Style/Pending Lessons.md",
    "03 - Legal/matter.md",
    "01 - Daily Notes/2026-08-29.md",
]

ACQUIT_NOTE = [
    # the operating manual's sanctioned form: where notes go, not which note
    "Log session summaries in `01 - Daily Notes/`, created fresh each day.",
    "the vault's `09 - Review Packets/` folder",
    "05 - Graphify/",
]


@pytest.mark.parametrize("text", CONVICT_FOLDER)
def test_fingerprint_convicts_a_vault_folder(text):
    assert _first_fingerprint(text), f"{text!r} should read as a vault folder"


@pytest.mark.parametrize("text", ACQUIT_FOLDER)
def test_fingerprint_acquits_everything_else(text):
    assert not _first_fingerprint(text), f"{text!r} is not a vault folder"


@pytest.mark.parametrize("text", CONVICT_NOTE)
def test_note_path_convicts_a_cited_note(text):
    assert _first_note_path(text), f"{text!r} cites a vault note"


@pytest.mark.parametrize("text", ACQUIT_NOTE + ACQUIT_FOLDER)
def test_note_path_acquits_a_bare_folder(text):
    assert not _first_note_path(text), f"{text!r} names no note"
