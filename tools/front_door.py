#!/usr/bin/env python3
"""Render the desk index: one page answering "what have we got, and why".

The owner does not retain procedures between sittings and should not have to.
Everything this repository knows is written for a Claude session to read -- a
skill only exists inside one, a pull request is the record of what a session
did, and ``docs/`` is 51,000 words nobody opens on a phone. This renders the
durable half of that as one page they can.

**It deliberately carries no live state.** Open pull requests, CI colour, what
``main`` points at and any count of them live nowhere at all, by the rule in
``CLAUDE.md`` under *The ledger* -- they were written down for months and were
wrong within hours every time. A dashboard that carried them was retired on
2026-08-29 (``docs/decisions/2026-08-29-retiring-the-live-work-dashboard.md``),
and that record rejects a static rebuild by name. So the page says where live
state comes from and links out to the two things that derive it, rather than
holding a copy that is stale before it is published.

What it *does* carry is the half that is true for weeks: what exists, how you
reach it, what Claude loads without being asked, and every decision on record.

Everything is scanned. Nothing here keeps its own list of tools, because a list
beside the tree is a second copy and the two stop agreeing on the first edit --
which is why the command one-liners come from the ``## Commands`` block in
``CLAUDE.md`` (already written in the owner's language) and the tool
one-liners from ``docs/layout.md``, rather than being typed in here.

    python tools/front_door.py build --out docs/desk-index.html
    python tools/front_door.py check          # what the page would claim
"""

import argparse
import html
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Skills that track upstream. They are not part of this desk and listing them
# would suggest the owner commissioned them -- see docs/skills.md.
VENDORED = {"ui-ux-pro-max", "build-puzzle-process"}


@dataclass
class Entry:
    """One thing that exists, and the one line that says what it is for."""

    name: str
    blurb: str
    detail: str = ""
    meta: str = ""


@dataclass
class Index:
    commands: list = field(default_factory=list)
    skills: list = field(default_factory=list)
    pages: list = field(default_factory=list)
    library: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    commit: str = ""
    built: str = ""

    def counts(self):
        return {
            "commands": len(self.commands),
            "skills": len(self.skills),
            "pages": len(self.pages),
            "library": len(self.library),
            "decisions": len(self.decisions),
        }


# --------------------------------------------------------------------------
# scanning -- every one-liner below is read from a file, never written here
# --------------------------------------------------------------------------


def _first_sentence(text, limit=240, sentences=1):
    """The opening of a passage, trimmed to whole sentences where it can be.

    ``sentences`` above one matters for the decision records: "Retire it." is
    the first sentence of "Retire it. Do not rebuild it as a static page." and
    on its own says nothing.
    """
    text = " ".join(text.split())
    found = re.findall(r".+?[.!?](?=\s|$)", text)
    out = "".join(found[:sentences]).strip() if found else text
    if not out or len(out) > limit:
        # Cut at a word boundary. "lives only i" is worse than no tail at all.
        clipped = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
        out = f"{clipped}…" if clipped else text[:limit]
    return out


def scan_commands(root=ROOT):
    """The ``## Commands`` block of CLAUDE.md: what the owner actually types.

    Each line is already ``command  # what it is for`` in their own words, so
    the blurb is lifted rather than invented. Lines with no comment are still
    listed -- a command with no explanation is a gap worth seeing.
    """
    text = (root / "CLAUDE.md").read_text(encoding="utf-8")
    match = re.search(r"^## Commands\s*\n+```bash\n(.*?)^```", text, re.S | re.M)
    if not match:
        return []
    entries = []
    for line in match.group(1).splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # `command  # what it is for` -- the gap before the hash is usually two
        # spaces and sometimes one, so split on any run of whitespace.
        parts = re.split(r"\s+#\s*", line.strip(), maxsplit=1)
        entries.append(
            Entry(
                name=parts[0].strip(),
                blurb=parts[1].strip() if len(parts) > 1 else "",
                meta="you type this",
            )
        )
    return entries


def scan_skills(root=ROOT):
    """Every skill this repo owns, with the description that makes it fire."""
    entries = []
    skills = root / ".claude" / "skills"
    if not skills.is_dir():
        return entries
    for directory in sorted(skills.iterdir()):
        source = directory / "SKILL.md"
        if not source.is_file() or directory.name in VENDORED:
            continue
        text = source.read_text(encoding="utf-8")
        block = re.search(r"^---\n(.*?)\n---", text, re.S)
        description = ""
        if block:
            found = re.search(
                r"^description:\s*(.*(?:\n[ \t]+.*)*)", block.group(1), re.M
            )
            if found:
                description = " ".join(found.group(1).split()).strip("\"'")
        entries.append(
            Entry(
                name=directory.name,
                blurb=_first_sentence(description, limit=300),
                detail=description,
                meta="Claude loads this",
            )
        )
    return entries


def scan_pages(root=ROOT):
    """Single-file pages that open in a browser, titled from their own <title>."""
    entries = []
    for directory in ("static", "docs"):
        for path in sorted((root / directory).glob("*.html")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            found = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
            title = " ".join(found.group(1).split()) if found else path.stem
            entries.append(
                Entry(
                    name=str(path.relative_to(root)),
                    blurb=html.unescape(title),
                    meta="opens in a browser",
                )
            )
    return entries


def _doc_opening(path):
    """The first real sentence of a topic doc, past its title and any quote."""
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    for block in body.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(("#", ">", "|", "-", "*", "!")):
            continue
        return _first_sentence(block)
    return ""


def scan_library(root=ROOT):
    """The shipped package, one line per subpackage.

    Preferring the subpackage's own docstring, falling back to the opening of
    its topic doc. Four of the eight carry no docstring, and a card reading
    "no one-line description in the source" is the honest render of that --
    but ``docs/datasets.md`` and its siblings already say it in a sentence, so
    the fallback is a second reading of the same repository, not a guess.
    """
    entries = []
    package = root / "pwb_toolbox"
    for path in sorted(package.iterdir()):
        init = path / "__init__.py"
        if not init.is_file():
            continue
        text = init.read_text(encoding="utf-8", errors="ignore")
        found = re.match(r'\s*(?:[ru]?"""|\'\'\')(.*?)(?:"""|\'\'\')', text, re.S)
        blurb = _first_sentence(found.group(1)) if found else ""
        source = "its docstring"
        if not blurb:
            blurb = _doc_opening(root / "docs" / f"{path.name}.md")
            source = f"docs/{path.name}.md" if blurb else "nothing"
        entries.append(
            Entry(
                name=f"pwb_toolbox.{path.name}",
                blurb=blurb,
                detail=source,
                meta="import this",
            )
        )
    return entries


def scan_decisions(root=ROOT):
    """Every decision on record, with the line that says what was decided."""
    entries = []
    directory = root / "docs" / "decisions"
    for path in sorted(directory.glob("*.md"), reverse=True):
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        title = ""
        heading = re.search(r"^#\s+(.+)$", text, re.M)
        if heading:
            title = heading.group(1).strip()
        date = ""
        stamp = re.match(r"(\d{4}-\d{2}-\d{2})-", path.name)
        if stamp:
            date = stamp.group(1)
        decision = ""
        found = re.search(r"\*\*Decision:?\*\*:?\s*(.+?)(?=\n\s*\n|\n\*\*)", text, re.S)
        if found:
            decision = _first_sentence(found.group(1), limit=400, sentences=2)
        if not decision:
            body = re.sub(r"^#.*$|^\*Decided.*$", "", text, flags=re.M).strip()
            decision = _first_sentence(body, limit=400, sentences=2)
        decision = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", decision)  # links to text
        decision = decision.replace("**", "").replace("*", "")
        entries.append(
            Entry(
                name=title or path.stem,
                blurb=decision,
                meta=date,
                detail=path.name,
            )
        )
    return entries


def _git(*args, root=ROOT):
    try:
        done = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=10
        )
        return done.stdout.strip() if done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def build_index(root=ROOT):
    return Index(
        commands=scan_commands(root),
        skills=scan_skills(root),
        pages=scan_pages(root),
        library=scan_library(root),
        decisions=scan_decisions(root),
        commit=_git("rev-parse", "--short", "HEAD", root=root),
        built=_git("log", "-1", "--format=%ad", "--date=format:%d %B %Y", root=root),
    )


# --------------------------------------------------------------------------
# rendering -- docs/page-style.md is the house style and this follows it
# --------------------------------------------------------------------------

STYLE = """
:root{
  --page:#fbfbfa; --card:#ffffff; --sunken:#f5f5f3;
  --ink:#141413; --ink-2:#57564f; --ink-3:#8a8880;
  --rule:#e6e5e0; --rule-2:#f0efec;
  --ai:#2a78d6;     --ai-bg:#e9f1fc;     --ai-edge:#c2dbf6;
  --person:#c4491d; --person-bg:#fdece5; --person-edge:#f7cdb9;
  --auto:#57564f;   --auto-bg:#f0efec;   --auto-edge:#dedcd5;
  --good:#00701f; --good-bg:#e3f4e6;
  --warn:#9a6200; --warn-bg:#fbf1dc;
  --bad:#b5292a;  --bad-bg:#fdeaea;
  --display:"Newsreader",Georgia,"Times New Roman",serif;
  --body:"Public Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--page); color:var(--ink);
  font-family:var(--body); font-size:16px; line-height:1.6;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:960px;margin:0 auto;padding:0 20px 96px}
h1,h2,h3{font-family:var(--display);font-weight:600;text-wrap:balance;margin:0}
h1{font-size:clamp(2rem,5vw,3rem);line-height:1.1;letter-spacing:-.015em}
h2{font-size:clamp(1.35rem,3vw,1.75rem);line-height:1.2}
h3{font-size:1.05rem;line-height:1.3}
p{margin:0}
a{color:var(--ai);text-underline-offset:2px}
a:focus-visible,input:focus-visible,button:focus-visible,[tabindex]:focus-visible{
  outline:2px solid var(--ai); outline-offset:2px; border-radius:2px;
}
.eyebrow{
  font-family:var(--mono);font-size:.7rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3);
}
header.masthead{
  border-bottom:1px solid var(--rule); padding:56px 0 28px; margin-bottom:36px;
  display:flex; flex-direction:column; gap:14px;
}
.lede{color:var(--ink-2);max-width:64ch;font-size:1.05rem}
.stamp{
  font-family:var(--mono);font-size:.72rem;color:var(--ink-3);
  display:flex;flex-wrap:wrap;gap:6px 16px;font-variant-numeric:tabular-nums;
}
section{margin-top:52px;display:flex;flex-direction:column;gap:18px}
.sec-head{display:flex;flex-direction:column;gap:6px}
.sec-note{color:var(--ink-2);max-width:66ch;font-size:.92rem}
.count{
  font-family:var(--mono);font-size:.72rem;color:var(--ink-3);
  font-variant-numeric:tabular-nums;vertical-align:.35em;margin-left:8px;
  font-weight:400;
}
/* live-state panel: the thing this page deliberately does not carry */
.derive{
  background:var(--warn-bg); border:1px solid #e8d3a4; border-left:4px solid var(--warn);
  border-radius:3px; padding:18px 20px; display:flex; flex-direction:column; gap:10px;
}
.derive h2{font-size:1.1rem}
.derive p{color:#6b4700;font-size:.92rem;max-width:66ch}
.derive ul{margin:0;padding-left:18px;color:#6b4700;font-size:.92rem;
  display:flex;flex-direction:column;gap:6px}
.derive a{color:#7c4f00;font-weight:600}
.grid{display:grid;gap:8px}
.card{
  background:var(--card); border:1px solid var(--rule); border-radius:3px;
  padding:11px 14px; display:flex; flex-direction:column; gap:4px;
  border-left:3px solid var(--auto);
}
.card.person{border-left-color:var(--person)}
.card.ai{border-left-color:var(--ai)}
.card.auto{border-left-color:var(--auto)}
.card .name{
  font-family:var(--mono); font-size:.83rem; color:var(--ink);
  word-break:break-word; font-weight:500;
}
.card .blurb{color:var(--ink-2);font-size:.92rem}
.blurb code,.dec p code{
  font-family:var(--mono);font-size:.85em;background:var(--sunken);
  padding:1px 4px;border-radius:2px;color:var(--ink);
}
.card .blurb:empty::after{
  content:"no one-line description in the source";
  color:var(--ink-3); font-style:italic;
}
.chip{
  align-self:flex-start; font-family:var(--mono); font-size:.65rem;
  letter-spacing:.06em; text-transform:uppercase; padding:2px 7px;
  border-radius:2px; border:1px solid var(--auto-edge);
  background:var(--auto-bg); color:var(--auto);
}
.chip.person{background:var(--person-bg);border-color:var(--person-edge);color:var(--person)}
.chip.ai{background:var(--ai-bg);border-color:var(--ai-edge);color:var(--ai)}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center}
.legend span{display:flex;align-items:center;gap:7px;font-size:.82rem;color:var(--ink-2)}
.swatch{width:11px;height:11px;border-radius:2px;flex:none}
/* decisions */
.filter{
  display:flex;gap:10px;align-items:center;flex-wrap:wrap;
  position:sticky;top:0;background:var(--page);padding:10px 0;z-index:5;
  border-bottom:1px solid var(--rule-2);
}
.filter input{
  flex:1 1 260px; min-width:0; font-family:var(--body); font-size:.92rem;
  padding:9px 12px; border:1px solid var(--rule); border-radius:3px;
  background:var(--card); color:var(--ink);
}
.filter input::placeholder{color:var(--ink-3)}
.hits{font-family:var(--mono);font-size:.72rem;color:var(--ink-3);
  font-variant-numeric:tabular-nums;white-space:nowrap}
.dec{
  display:grid; grid-template-columns:92px 1fr; gap:4px 18px;
  padding:14px 0; border-bottom:1px solid var(--rule-2);
}
.dec .date{
  font-family:var(--mono);font-size:.74rem;color:var(--ink-3);
  font-variant-numeric:tabular-nums;padding-top:3px;
}
.dec h3{font-size:.98rem}
.dec p{color:var(--ink-2);font-size:.9rem;grid-column:2}
.empty{color:var(--ink-3);font-style:italic;padding:20px 0}
footer{
  margin-top:64px;padding-top:20px;border-top:1px solid var(--rule);
  color:var(--ink-3);font-size:.85rem;display:flex;flex-direction:column;gap:8px;
}
footer code{font-family:var(--mono);font-size:.8rem;color:var(--ink-2)}
@media (max-width:560px){
  .dec{grid-template-columns:1fr;gap:4px}
  .dec p{grid-column:1}
  header.masthead{padding-top:36px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def _inline(text):
    """Escape prose, then let its backticks become code the way markdown does.

    Every blurb is lifted from markdown, so it arrives full of `paths` and
    `identifiers`. Escaping happens first and backticks are untouched by it,
    so this cannot smuggle markup in from a document.
    """
    escaped = html.escape(text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def _cards(entries, tone):
    out = []
    for entry in entries:
        out.append(
            f'<div class="card {tone}" data-search="'
            f'{html.escape((entry.name + " " + entry.blurb).lower(), quote=True)}">'
            f'<span class="name">{html.escape(entry.name)}</span>'
            f'<span class="blurb">{_inline(entry.blurb)}</span>'
            f"</div>"
        )
    return "\n".join(out)


def _section(title, note, entries, tone, anchor):
    if not entries:
        return ""
    return f"""
<section id="{anchor}">
  <div class="sec-head">
    <h2>{html.escape(title)}<span class="count">{len(entries)}</span></h2>
    <p class="sec-note">{note}</p>
  </div>
  <div class="grid">
{_cards(entries, tone)}
  </div>
</section>"""


def render(index):
    counts = index.counts()
    decisions = "\n".join(
        f'<article class="dec" data-search="'
        f'{html.escape((d.name + " " + d.blurb + " " + d.meta).lower(), quote=True)}">'
        f'<span class="date">{html.escape(d.meta)}</span>'
        f"<h3>{html.escape(d.name)}</h3>"
        f"<p>{_inline(d.blurb)}</p>"
        f"</article>"
        for d in index.decisions
    )
    stamp = " ".join(
        part
        for part in (
            f"built from {index.commit}" if index.commit else "",
            index.built,
        )
        if part
    )
    return f"""<title>Desk Index</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,500;6..72,600&family=Public+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>{STYLE}</style>

<div class="wrap">
<header class="masthead">
  <span class="eyebrow">pwb-toolbox</span>
  <h1>Desk Index</h1>
  <p class="lede">Everything this desk has, what it is for, and every decision
  on record &mdash; so none of it has to be remembered between sittings.</p>
  <div class="stamp"><span>{html.escape(stamp)}</span>
  <span>{counts['commands']} commands &middot; {counts['skills']} skills &middot;
  {counts['pages']} pages &middot; {counts['decisions']} decisions</span></div>
</header>

<div class="derive">
  <h2>What is <em>not</em> on this page</h2>
  <p>Open pull requests, whether CI is green, what <code>main</code> points at,
  and any count of them. Those were written down for months and were wrong
  within hours every time, so this desk keeps them nowhere and derives them
  when asked. A dashboard that carried them anyway was retired on 29 August
  2026, and that record turns down a static rebuild by name &mdash; this page
  is the durable half only.</p>
  <ul>
    <li><strong>Where things stand right now</strong> &mdash; ask any session.
    It opens with the branch, the working tree, open pull requests and their
    CI, gathered at the moment you ask.</li>
    <li><strong>What is waiting on you</strong> &mdash; the
    <a href="https://claude.ai/code/artifact/a9da0f16-1b7f-4658-a21f-70271be5c413">Action
    Ledger</a>, where ticking a box costs nothing and the tick survives.</li>
    <li><strong>The pull requests themselves</strong> &mdash;
    <a href="https://github.com/jay79-boop/pwb-toolbox/pulls">on GitHub</a>,
    which is the only copy that is never stale.</li>
  </ul>
</div>

<section>
  <div class="legend">
    <span><span class="swatch" style="background:var(--person)"></span>you type it</span>
    <span><span class="swatch" style="background:var(--ai)"></span>Claude loads it without being asked</span>
    <span><span class="swatch" style="background:var(--auto)"></span>you open it, or import it</span>
  </div>
</section>
{_section(
    "Commands you can run",
    "Lifted from the <code>## Commands</code> block of <code>CLAUDE.md</code>, "
    "which is where they are already written in your own words.",
    index.commands, "person", "commands")}
{_section(
    "What Claude loads by itself",
    "You never see these fire. Each one carries a procedure worth not retyping, "
    "and the text below is the description that decides whether it loads.",
    index.skills, "ai", "skills")}
{_section(
    "Pages that open in a browser",
    "Single files, no build step. Titles are read from each page itself.",
    index.pages, "auto", "pages")}
{_section(
    "The shipped library",
    "One line per subpackage, read from its own docstring.",
    index.library, "auto", "library")}

<section id="decisions">
  <div class="sec-head">
    <h2>Every decision on record<span class="count">{counts['decisions']}</span></h2>
    <p class="sec-note">One file per decision, newest first. A later correction
    is a new entry that supersedes an old one &mdash; nothing here is ever
    rewritten, so an entry may have been overtaken.</p>
  </div>
  <div class="filter">
    <input id="q" type="search" placeholder="Filter decisions &mdash; try noise floor, spend, routine, timezone"
           aria-label="Filter decisions">
    <span class="hits" id="hits">{counts['decisions']} shown</span>
  </div>
  <div id="decs">
{decisions}
  </div>
  <p class="empty" id="none" hidden>Nothing matches that.</p>
</section>

<footer>
  <span>Generated by <code>python tools/front_door.py build</code>. Every line
  above is read from the repository &mdash; nothing on this page is typed into
  it, so re-running the command is the only way to change it.</span>
  <span>House style: <code>docs/page-style.md</code>. The rule about what may
  not be recorded: <code>CLAUDE.md</code>, under <em>The ledger</em>.</span>
</footer>
</div>

<script>
(function () {{
  var q = document.getElementById('q');
  var hits = document.getElementById('hits');
  var none = document.getElementById('none');
  var rows = Array.prototype.slice.call(
    document.querySelectorAll('#decs .dec'));
  function apply() {{
    var term = q.value.trim().toLowerCase();
    var shown = 0;
    rows.forEach(function (row) {{
      var match = !term || row.dataset.search.indexOf(term) !== -1;
      row.hidden = !match;
      if (match) shown++;
    }});
    hits.textContent = shown + ' shown';
    none.hidden = shown !== 0;
  }}
  q.addEventListener('input', apply);
  apply();
}})();
</script>
"""


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="render the page")
    build.add_argument(
        "--out",
        default=str(ROOT / "docs" / "desk-index.html"),
        help="where to write the page (default: docs/desk-index.html)",
    )

    check = sub.add_parser("check", help="print what the page would claim, as JSON")
    check.add_argument("--json", action="store_true", help="machine-readable output")

    args = parser.parse_args(argv)
    index = build_index()

    if args.command == "build":
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(index), encoding="utf-8")
        counts = index.counts()
        print(f"Wrote {out}")
        print(
            "  "
            + ", ".join(f"{value} {key}" for key, value in counts.items())
            + f", built from {index.commit or 'an unknown commit'}"
        )
        return 0

    counts = index.counts()
    if args.json:
        print(json.dumps({"commit": index.commit, "counts": counts}, indent=2))
    else:
        for key, value in counts.items():
            print(f"{value:>4}  {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
