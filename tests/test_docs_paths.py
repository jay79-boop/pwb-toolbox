"""Hold the prose in ``CLAUDE.md`` and ``docs/`` to the bar the skills already meet.

``tests/test_skills.py`` checks that every repo path a *skill* names still
exists, because nothing else in CI reads a skill. The same was never true of the
documentation, and the same failure mode applies with more force: ``docs/`` is
five times the size of the skills directory and is where a reader is sent when
they need to be right.

It had already happened. ``docs/blueprint-guide.md`` told the reader to run
``tools/blueprint-xlsx-to-json.py`` and ``tools/blueprint-json-to-xlsx.py``.
Neither has ever existed -- the real tool is ``tools/blueprint_converter.py``,
which does both jobs as subcommands and validates as a third. The guide even
hedged with "(if you have it)", which is what a doc does instead of failing.

This is the mechanical half only, and deliberately so. A path either resolves or
it does not, and that is decidable; whether the sentence around it is still true
is not, and stays on the author. ``tests/test_docs_examples.py`` covers the
neighbouring case -- Python examples bound against live signatures -- and the
two do not overlap: that one reads calls, this one reads paths.

**Not every repo-shaped token is a claim that the file is here.** Four
legitimate kinds have turned up. One is decidable without a list -- a path git
ignores is produced by a run, so its absence in a fresh clone and in CI is
expected, and :func:`is_generated` asks git rather than keeping a list. The
other three turned up on the first run: a file described in the past tense
("there used to be a `.github/workflows/pages.yml`"), a Claude Code convention
that is not this repository's tree (`.claude/commands/`), and a file that lives
on the owner's machine only. Guessing at those from nearby words is the mistake
``docs/decisions/2026-08-24-a-written-rule-with-no-check-behind-it-lasted-eight-hours.md``
records -- a proximity regex there matched its own cure and raised findings on
exactly the things that had been fixed. So they are listed instead, each with a
reason, and :func:`test_the_allowlist_has_not_gone_stale` deletes the entry for
you when the reason expires.

Following the convict-and-acquit pattern ``test_docs_examples.py`` established:
a scanner that resolves nothing reports every document clean, so
:func:`test_the_scanner_finds_paths_to_check` pins a floor and
:func:`test_the_scanner_convicts_a_dead_path` requires known-bad input to be
caught. Without that pair a green tick here would mean nothing.
"""

import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Only backticked tokens starting with one of these are read as path claims.
# Kept in step with REPO_DIRS in tests/test_skills.py -- the two scanners are
# separate on purpose (each test file in this repo carries its own), but they
# should agree on what counts as a claim about this repository.
REPO_DIRS = (
    "tools/",
    "docs/",
    "tests/",
    "pwb_toolbox/",
    "pwb_toolbox_legacy/",
    "pine/",
    "static/",
    ".claude/",
    ".github/",
)


def is_generated(path):
    """Would git ignore this path? Then it is produced by a run, not carried.

    A document may legitimately name an output directory that is absent in a
    fresh clone and in CI, and asking git is better than listing them: the
    answer stays right when the tree changes underneath.

    ``docs/journal`` is why this exists rather than an allowlist entry. It was
    a tracked directory when this check was written, then a sync-only mirror an
    hour later; an entry asserting "absent" would have failed while it was
    tracked, and no entry at all would have failed once it was not. Deriving it
    from ``.gitignore`` is correct in both states without anyone editing a list.

    A directory rule is written with a trailing slash (``docs/journal/``), and
    git will not match that against the bare token prose uses (``docs/journal``)
    when the directory is not on disk to prove it is one -- which is exactly the
    case this has to answer. So ask about both forms.
    """
    candidates = [str(path)] if str(path).endswith("/") else [str(path), f"{path}/"]
    return any(
        subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", candidate],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        == 0
        for candidate in candidates
    )


# Repo-shaped tokens that are correct prose about something not in this tree
# *and* that git does not already know is generated -- see is_generated above,
# which handles that larger case on its own. Every entry carries the reason it
# is here, because an allowlist without one becomes the place broken links go
# to be forgotten. The test below removes an entry once its reason expires, so
# this cannot quietly outgrow its purpose.
NOT_IN_THIS_TREE = {
    ".github/workflows/pages.yml": (
        "Removed deliberately; docs/design-tooling.md describes it in the past "
        "tense and says why it was not fixed."
    ),
    ".claude/commands/": (
        "A Claude Code convention docs/working-directories.md explains --add-dir "
        "against, not a directory this repository has."
    ),
    ".claude/settings.local.json": (
        "Machine-local and gitignored. docs/local-checkout.md says outright that "
        "this is Claude Code behaviour, not a fact about this repository."
    ),
    "docs/journal": (
        "Deliberately absent and gitignored, by the 2026-08-29 decision that the "
        "Obsidian vault is not mirrored into this public fork. CLAUDE.md, "
        "docs/layout.md and that decision record all have to be able to name the "
        "directory in order to say it must stay empty."
    ),
    "docs/journal/": (
        "Same directory, written with the trailing slash the .gitignore entry "
        "uses. The prose quotes the ignore rule verbatim, so both spellings "
        "appear."
    ),
    "tools/blueprint-xlsx-to-json.py": (
        "Never existed. The 2026-08-29 decision record names it as the broken "
        "reference this check was written to catch, so the record has to be able "
        "to say the name."
    ),
}

# Backticked tokens containing a slash. Globs (`season/*`), gitignore negations
# (`!season/README.md`), placeholders (`docs/<lab-name>.md`) and shell brace
# expansion (`pwb_toolbox/options/{greeks,decay}.py`) are not paths.
_TOKEN = re.compile(r"`([^`\s]+/[^`\s]*)`")
_NOT_A_PATH = set("*?!<>|${}")

# A trailing sentence mark is punctuation, not part of the name.
_TRAILING = ".,;:)"


def referenced_paths(text):
    """Yield every repo path the prose claims exists.

    A pytest node id (``tests/test_x.py::test_y``) is a claim about the file to
    the left of the ``::``; the selector after it is not part of any filename.
    """
    for token in _TOKEN.findall(text):
        if _NOT_A_PATH & set(token):
            continue
        token = token.split("::")[0].rstrip(_TRAILING)
        if token.startswith(REPO_DIRS):
            yield token


def _prose_files():
    """Every hand-written document, newest layer first."""
    files = [ROOT / "CLAUDE.md"]
    files += sorted((ROOT / "docs").glob("*.md"))
    files += sorted((ROOT / "docs" / "decisions").glob("*.md"))
    return [f for f in files if f.is_file()]


PROSE = _prose_files()


def _rel(path):
    return str(path.relative_to(ROOT))


def test_the_scanner_finds_paths_to_check():
    """Acquit guard: a scanner resolving nothing would pass every test below."""
    assert (
        len(PROSE) >= 40
    ), f"only {len(PROSE)} documents found -- has the glob broken?"
    total = sum(
        len(list(referenced_paths(f.read_text(encoding="utf-8")))) for f in PROSE
    )
    assert total >= 250, (
        f"the scanner sees only {total} path claims across {len(PROSE)} documents. "
        f"It read 283 when this test was written, so a number this low means the "
        f"scanner stopped matching, not that the docs stopped naming files."
    )


def test_the_scanner_convicts_a_dead_path():
    """Convict guard: known-bad input must be found, or the check is decorative."""
    planted = (
        "Run `tools/blueprint-xlsx-to-json.py` first, then read "
        "`docs/backtesting.md` and `pwb_toolbox/datasets/` for context."
    )
    found = list(referenced_paths(planted))
    assert "tools/blueprint-xlsx-to-json.py" in found, "the dead path was not seen"
    missing = [p for p in found if not (ROOT / p).exists()]
    assert missing == ["tools/blueprint-xlsx-to-json.py"], (
        f"expected exactly the planted dead path to fail, got {missing} -- the "
        f"two live paths beside it must still resolve."
    )


def test_the_scanner_ignores_what_is_not_a_path():
    """Globs, negations, placeholders and brace expansion are prose, not claims."""
    noise = (
        "Ignore `season/*` and `!season/README.md`, write `docs/<lab-name>.md`, "
        "expand `pwb_toolbox/options/{greeks,decay}.py`, and see "
        "`https://example.com/x` or `owner/repo#12`."
    )
    assert list(referenced_paths(noise)) == []


def test_the_scanner_reads_a_pytest_node_id_as_its_file():
    """`tests/x.py::test_y` claims the file exists, not a file with `::` in it."""
    found = list(referenced_paths("see `tests/test_skills.py::test_total_budget`"))
    assert found == ["tests/test_skills.py"]


@pytest.mark.parametrize("doc", PROSE, ids=_rel)
def test_every_path_the_prose_names_still_exists(doc):
    text = doc.read_text(encoding="utf-8")
    missing = sorted(
        {
            p
            for p in referenced_paths(text)
            if p not in NOT_IN_THIS_TREE
            and not (ROOT / p).exists()
            and not is_generated(p)
        }
    )
    assert not missing, (
        f"{_rel(doc)} sends the reader to paths that are not there: {missing}. "
        f"Fix the name or delete the claim -- a doc that would fail as written "
        f"costs a reader more than a stale sentence does. If the mention is "
        f"deliberate: if the path is generated by a run, gitignore it and this "
        f"check exempts it automatically; otherwise (a removed file, another "
        f"tool's convention) add it to NOT_IN_THIS_TREE with the reason."
    )


def test_a_gitignored_path_is_exempt_and_a_plain_missing_one_is_not():
    """The exemption must be narrow: generated, not merely absent."""
    assert is_generated("engagements/anything.json"), (
        "engagements/* is gitignored, so a doc naming it is not making a false "
        "claim -- if this fails, .gitignore changed and the rule needs a look."
    )
    assert not is_generated("tools/blueprint-xlsx-to-json.py"), (
        "a path that simply does not exist must NOT be exempted, or the check "
        "stops catching the bug it was written for."
    )
    assert not is_generated("tools/front_door.py"), "a tracked file is not generated"


def test_the_exemption_survives_docs_journal_moving_either_way():
    """The case that forced this: tracked one hour, sync-only mirror the next.

    ``docs/journal`` is named by ``docs/layout.md``. Whether it is in the tree
    depends on whether the vault mirror is committed -- it was, and PR #150
    made it gitignored instead. Either state has to pass without an edit here,
    so assert the disjunction the real check applies rather than today's answer.
    """
    path = "docs/journal"
    assert (ROOT / path).exists() or is_generated(path), (
        f"{path} is neither in the tree nor gitignored, so docs/layout.md now "
        f"names something that genuinely is not there. Either restore it, "
        f"gitignore it, or drop the mention."
    )


@pytest.mark.parametrize("path", sorted(NOT_IN_THIS_TREE), ids=lambda p: p)
def test_the_allowlist_has_not_gone_stale(path):
    """An allowlist nothing prunes is where broken links go to be forgotten."""
    assert not (ROOT / path).exists(), (
        f"{path} is allowlisted as absent but is now in the tree. Delete its "
        f"NOT_IN_THIS_TREE entry so the real check covers it again."
    )
    mentioned = any(path in f.read_text(encoding="utf-8") for f in PROSE)
    assert mentioned, (
        f"no document mentions {path} any more. Delete its NOT_IN_THIS_TREE "
        f"entry -- it is exempting nothing."
    )


def test_every_decision_file_is_in_the_index():
    """The index is hand-maintained and the rule is append-only, so it drifts."""
    index = (ROOT / "docs" / "decisions" / "README.md").read_text(encoding="utf-8")
    linked = set(re.findall(r"\]\((\d{4}-\d{2}-\d{2}-[^)]+\.md)\)", index))
    on_disk = {p.name for p in (ROOT / "docs" / "decisions").glob("*.md")} - {
        "README.md"
    }

    assert on_disk, "no decision files found -- has the directory moved?"
    assert not (on_disk - linked), (
        f"decision files exist but are not in docs/decisions/README.md: "
        f"{sorted(on_disk - linked)}. An unindexed decision is one nobody finds."
    )
    assert not (linked - on_disk), (
        f"docs/decisions/README.md links to files that are gone: "
        f"{sorted(linked - on_disk)}."
    )
