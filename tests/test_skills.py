"""Hold the skills in `.claude/skills/` to the bar in `docs/skills.md`.

Nothing else in CI reads a skill, so a skill that names a tool which has since
been renamed goes wrong silently and confidently -- which is the failure mode
skills are most prone to. These checks cover the mechanical half: the
frontmatter parses, every repo path a skill names still exists, and the
descriptions (which load into *every* session whether or not the skill fires)
stay inside a budget.

Whether the prose is still *true* is not checkable here and stays on the author.
"""

import pathlib
import re

import pytest

SKILLS = pathlib.Path(__file__).resolve().parent.parent / ".claude" / "skills"

# Vendored skills track upstream and are restored by `uipro init`. They are not
# ours to reformat, retitle or shrink -- see the last section of docs/skills.md.
VENDORED = {"ui-ux-pro-max", "build-puzzle-process"}

# Description text is paid for on every turn of every session, fired or not.
# These are tripwires for creep, not hard limits on useful prose: raise them
# deliberately, in the same commit as the skill that needs the room.
MAX_DESCRIPTION_WORDS = 110
MAX_TOTAL_DESCRIPTION_WORDS = 1000

# Only tokens that start with one of these are treated as claims about a path.
REPO_DIRS = (
    "tools/",
    "docs/",
    "tests/",
    "pwb_toolbox/",
    "pine/",
    "static/",
    ".claude/",
    ".github/",
)

# Backticked tokens containing a slash. Glob and gitignore syntax (`season/*`,
# `!season/README.md`) and placeholders (`docs/<lab-name>.md`) are not paths.
_TOKEN = re.compile(r"`([^`\s]+/[^`\s]*)`")
_NOT_A_PATH = set("*?!<>|$")


def _skill_dirs():
    return sorted(d for d in SKILLS.iterdir() if (d / "SKILL.md").is_file())


def _frontmatter(text):
    """The `key: value` block between the first two `---` lines."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}
    fields, key = {}, None
    for line in lines[1:end]:
        match = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if match:
            key = match.group(1)
            fields[key] = match.group(2).strip()
        elif key and line.strip():
            fields[key] += " " + line.strip()
    return {k: v.strip("\"'") for k, v in fields.items()}


def _referenced_paths(text):
    for token in _TOKEN.findall(text):
        if _NOT_A_PATH & set(token):
            continue
        if token.startswith(REPO_DIRS):
            yield token.rstrip(".,;:")


ALL = _skill_dirs()
OURS = [d for d in ALL if d.name not in VENDORED]


def test_there_are_skills_to_check():
    """Guards the glob itself: a silent zero would make every test below pass."""
    assert len(ALL) >= 5
    assert OURS, "every skill looks vendored -- has VENDORED gone stale?"


@pytest.mark.parametrize("skill", ALL, ids=lambda d: d.name)
def test_frontmatter_names_the_directory(skill):
    fields = _frontmatter((skill / "SKILL.md").read_text(encoding="utf-8"))
    assert fields.get("name") == skill.name
    assert fields.get("description"), "a skill with no description never fires"


@pytest.mark.parametrize("skill", OURS, ids=lambda d: d.name)
def test_description_stays_inside_its_budget(skill):
    fields = _frontmatter((skill / "SKILL.md").read_text(encoding="utf-8"))
    words = len(fields["description"].split())
    assert words <= MAX_DESCRIPTION_WORDS, (
        f"{skill.name}'s description is {words} words. Descriptions load on "
        f"every turn of every session, fired or not -- see docs/skills.md."
    )


def test_total_description_budget():
    total = sum(
        len(
            _frontmatter((d / "SKILL.md").read_text(encoding="utf-8"))[
                "description"
            ].split()
        )
        for d in OURS
    )
    assert total <= MAX_TOTAL_DESCRIPTION_WORDS, (
        f"{len(OURS)} skills now cost {total} words of always-loaded "
        f"description. Before raising the cap, read the retirement rule in "
        f"docs/skills.md -- the question is whether one of them should go."
    )


@pytest.mark.parametrize("skill", OURS, ids=lambda d: d.name)
def test_every_path_a_skill_names_still_exists(skill):
    root = SKILLS.parent.parent
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    missing = [p for p in _referenced_paths(text) if not (root / p).exists()]
    assert not missing, (
        f"{skill.name} names paths that are gone: {missing}. A dead path is a "
        f"retirement prompt, not just a typo -- see docs/skills.md."
    )
