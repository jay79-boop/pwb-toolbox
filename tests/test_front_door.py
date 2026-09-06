"""Hold ``tools/front_door.py`` to what the page claims, not how it is marked up.

``docs/page-style.md`` sets that rule after a test on a generated page pinned a
CSS class name and broke on a restyle that changed nothing about the page's
meaning. So these assert that every command, skill, page, subpackage and
decision the scanners find is actually rendered, and that the numbers in the
header are the ones the scan produced.

The load-bearing test is :func:`test_the_page_carries_no_live_state`. The page
exists because the owner cannot use 72,000 words of Claude-facing prose, and it
was nearly built as a status dashboard -- which
``docs/decisions/2026-08-29-retiring-the-live-work-dashboard.md`` turns down by
name, having just retired one for carrying facts that "were written down for
months and were wrong within hours every time". A later session will be tempted
to add a PR count to this page for exactly the reasons the first one was built.
That test is what stops it.
"""

import html
import re

import pytest

from tools import front_door


@pytest.fixture(scope="module")
def index():
    return front_door.build_index()


@pytest.fixture(scope="module")
def page(index):
    return front_door.render(index)


def test_the_scan_finds_the_repository():
    """Acquit guard: empty scans would satisfy every containment test below."""
    index = front_door.build_index()
    counts = index.counts()
    assert counts["commands"] >= 20, counts
    assert counts["skills"] >= 8, counts
    assert counts["pages"] >= 6, counts
    assert counts["library"] >= 6, counts
    assert counts["decisions"] >= 25, counts


@pytest.mark.parametrize(
    "group", ["commands", "skills", "pages", "library", "decisions"]
)
def test_every_scanned_entry_reaches_the_page(index, page, group):
    # Names carry angle brackets (`<transcript>.jsonl`) and quotes ("good
    # night"), so the page holds the escaped form -- compare like with like.
    missing = [e.name for e in getattr(index, group) if html.escape(e.name) not in page]
    assert (
        not missing
    ), f"{group} found by the scan but absent from the rendered page: {missing}"


def test_the_header_counts_match_the_scan(index, page):
    counts = index.counts()
    header = page.split("</header>")[0]
    for group in ("commands", "skills", "pages", "decisions"):
        assert f"{counts[group]} {group}" in header.replace("\n", " ").replace(
            "  ", " "
        ), f"the header does not state {counts[group]} {group}"


def test_the_page_carries_no_live_state(page):
    """The one rule this page must not break -- see the module docstring."""
    body = re.sub(r"<div class=\"derive\">.*?</div>\s*</div>", "", page, flags=re.S)

    assert not re.search(
        r"\b\d+\s+open\s+pull\s+request", body, re.I
    ), "the page states a count of open pull requests"
    assert not re.search(
        r"\bmergeable|\bci (is )?(green|red|passing|failing)", body, re.I
    ), "the page states CI status"
    # A 7-to-40 character hex run is a commit SHA. The build stamp is allowed to
    # name the commit it rendered from -- that is provenance, not a claim about
    # where main points -- so it is checked separately below.
    stamp = re.search(r"built from ([0-9a-f]{7,40})", page)
    shas = set(re.findall(r"\b[0-9a-f]{7,40}\b", body)) - {
        stamp.group(1) if stamp else ""
    }
    assert not shas, f"the page carries commit SHAs beyond its own build stamp: {shas}"


def test_the_page_says_where_live_state_comes_from(page):
    """Refusing to carry a fact is only honest if it points at what does."""
    assert 'class="derive"' in page
    assert (
        "artifact/a9da0f16-1b7f-4658-a21f-70271be5c413" in page
    ), "the Action Ledger is where anything waiting on the owner lives"
    assert "github.com/jay79-boop/pwb-toolbox/pulls" in page


def test_nothing_on_the_page_is_typed_into_the_generator(index):
    """Every blurb must be quotable from the file it claims to come from."""
    root = front_door.ROOT
    source = front_door.commands_source(root)
    assert source is not None, "no file carries the ## Commands block"
    commands = source.read_text(encoding="utf-8")
    where = source.relative_to(root)
    for entry in index.commands:
        assert entry.name in commands, f"{entry.name!r} is not in {where}"
        if entry.blurb:
            assert entry.blurb in commands, f"{entry.blurb!r} is not in {where}"

    for entry in index.skills:
        text = (root / ".claude" / "skills" / entry.name / "SKILL.md").read_text(
            encoding="utf-8"
        )
        opening = entry.blurb.split(".")[0][:40]
        assert opening in " ".join(text.split()), f"{entry.name}: blurb not in SKILL.md"


def test_vendored_skills_are_not_listed_as_ours(index):
    """They track upstream; listing them implies the owner commissioned them."""
    names = {e.name for e in index.skills}
    assert not (names & front_door.VENDORED), names & front_door.VENDORED


def test_decisions_are_newest_first(index):
    dates = [e.meta for e in index.decisions if e.meta]
    assert dates == sorted(dates, reverse=True)


def test_every_decision_shows_what_was_decided(index):
    """A record rendered without its decision line is a title and no content."""
    thin = [(e.meta, e.name) for e in index.decisions if len(e.blurb) < 20]
    assert not thin, f"decisions rendered with no usable text: {thin}"


def test_markup_is_escaped(index, page):
    """Blurbs come from prose full of angle brackets and ampersands."""
    assert "<script>alert" not in page
    for entry in index.decisions + index.skills:
        assert "<" not in entry.name or "&lt;" in page

    body = page.split("<script>")[0]
    opened = len(re.findall(r"<div\b", body))
    closed = len(re.findall(r"</div>", body))
    assert opened == closed, f"unbalanced divs: {opened} opened, {closed} closed"


def test_the_page_commits_to_a_ground(page):
    """docs/page-style.md: a transparent body borrows the host's dark theme."""
    assert re.search(
        r"body\{[^}]*background:var\(--page\)", page.replace(" ", "")
    ), "body must set an explicit background or the page breaks on a dark host"
    assert (
        "prefers-color-scheme" not in page
    ), "the house style is committed light, not theme-aware"


def test_backticks_become_code_but_markup_cannot_get_in():
    """Blurbs are markdown, so `paths` render as code -- nothing else does."""
    assert front_door._inline("see `tools/x.py`") == "see <code>tools/x.py</code>"
    hostile = front_door._inline("<script>alert(1)</script> and `a & b`")
    assert "<script>" not in hostile
    assert "&lt;script&gt;" in hostile
    assert "<code>a &amp; b</code>" in hostile
