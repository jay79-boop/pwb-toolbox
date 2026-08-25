# House style for pages and artifacts

Every page built in this repo — a generated report, a dashboard, a published
Artifact — looks like this unless the owner says otherwise. It is written down
because it was asked for once, and asking twice for the same thing is the
failure this file exists to prevent.

## The three standing rules

1. **Light.** Committed, not theme-aware. No `prefers-color-scheme` block, no
   `data-theme` stamps. The owner reads these on a phone in daylight and on a
   desktop at night and wants the same page both times.
2. **Colour-coded.** The page's main distinction gets a colour system, applied
   consistently, and every colour is validated rather than eyeballed.
3. **Derived, never typed.** Any number on a page comes from the data the page
   is about. A page carrying its own counts is a second copy of the source, and
   the two stop agreeing on the first edit.

Because the page commits to light, **every surface and every ink must be
stated**. An Artifact composites over a ground the host paints in the viewer's
theme, so a transparent `body` silently borrows a dark background and the page
becomes unreadable. `body { background: var(--page) }` is not optional.

## Colour: validate, do not eyeball

The `dataviz` skill ships a validator. Run it on any categorical set before
using it:

```bash
node <dataviz-skill>/scripts/validate_palette.js "#hex,#hex,..." --mode light --pairs all
```

Use `--pairs all` when the colours can appear anywhere relative to each other,
which is the normal case for a document. This is not ceremony — building
`docs/one-person-ai-company.html` it caught two sets that looked fine and were
not: a hand-picked violet/blue pair at ΔE 3.4, and a teal/green pair at ΔE 11.2
that had passed as *saturated hues* and collided once they were darkened for
text contrast. **Validate the values you are actually shipping**, not the ones
you started from.

Two mitigations the validator will tell you about, and both are legitimate:

- **CVD ΔE in the 6–8 band** is legal only with secondary encoding. A permanent
  direct label counts.
- **Contrast below 3:1** requires relief. Use the hue for rails, rules and
  fills, and a separate darker `-ink` token for any text — never the fill hue
  on text.

Status colours (good / warning / critical) are reserved. They never double as a
category, and they always ship with a glyph **and** a word, so meaning never
rests on hue alone.

## The token set

Lifted from `tools/ai_company.py`, which is the worked example. Copy it and
rename the domain tokens; keep the structure.

```css
:root{
  --page:#fbfbfa; --card:#ffffff; --sunken:#f5f5f3;
  --ink:#141413; --ink-2:#57564f; --ink-3:#8a8880;
  --rule:#e6e5e0; --rule-2:#f0efec;
  /* the primary distinction — the thing the page is arguing about */
  --ai:#2a78d6;     --ai-bg:#e9f1fc;     --ai-edge:#c2dbf6;
  --person:#c4491d; --person-bg:#fdece5; --person-edge:#f7cdb9;
  --auto:#57564f;   --auto-bg:#f0efec;   --auto-edge:#dedcd5;
  /* status — reserved, never a category */
  --good:#00701f; --good-bg:#e3f4e6;
  --warn:#9a6200; --warn-bg:#fbf1dc;
  --bad:#b5292a;  --bad-bg:#fdeaea;
}
```

Blue / warm-red / grey is the validated primary triple (all-pairs light: CVD
ΔE 16.6, normal ΔE 21.2). Grey fails the validator's chroma floor on purpose —
it is the slot that recedes, not a category competing for attention.

## Type

Three faces, three jobs. Linked from Google Fonts — the only font host an
Artifact's CSP admits — each with a real fallback stack so the page still
opens from `file://` with no network.

| Role | Face | Used for |
|---|---|---|
| Display | **Newsreader** | `h1`–`h3`. Says "document of record", not "dashboard" |
| Body | **Public Sans** | Running text, at 65–68ch |
| Data | **JetBrains Mono** | Numbers, IDs, chips, eyebrows, step references |

Every column of digits gets `font-variant-numeric: tabular-nums`. Headings get
`text-wrap: balance`. Do not reach for Inter or Space Grotesk — they are the
current AI-default faces and read as unconsidered.

## Layout

- Sibling groups are laid out with flex/grid and `gap`, never per-element
  margins that collapse or double.
- Wide content — tables, diagrams, code — scrolls inside its own
  `overflow-x:auto` container with `tabindex="0"` and a visible focus ring. The
  page body never scrolls sideways. Check it at 390px.
- Structural devices encode something true. A numbered marker is for an actual
  sequence; a shared colour means the two things really are the same thing.
  `docs/one-person-ai-company.html` gives marketing and finance one colour
  because the loop closes when finance's cash becomes marketing's budget —
  that is information, not decoration.

## Before publishing

Render it and look at it. The validator checks colour, not layout.

```bash
python3 - <<'PY'
from playwright.sync_api import sync_playwright
import pathlib
url = "file://" + str(pathlib.Path("docs/<page>.html").resolve())
with sync_playwright() as pw:
    b = pw.chromium.launch(
        executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
        args=["--no-sandbox"])
    for w, theme in ((1200, "light"), (1200, "dark"), (390, "light")):
        pg = b.new_page(viewport={"width": w, "height": 1100}, color_scheme=theme)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(url); pg.wait_for_timeout(800)
        pg.screenshot(path=f"/tmp/shot-{w}-{theme}.png")
        print(w, theme, errs or "no js errors",
              pg.evaluate("document.documentElement.scrollWidth"
                          " - document.documentElement.clientWidth"))
    b.close()
PY
```

The dark run is not there to check a dark theme — there isn't one. It is there
to prove the light page still holds when the host paints a dark ground.

Playwright's pinned browser here is older than the pip package expects, so pass
`executable_path` rather than running `playwright install`.

## Tests

A test on a generated page asserts **what the page claims**, not how it is
marked up. `tests/test_ai_company.py::test_page_renders_from_the_blueprint`
checks that every role and every backlog item in the data appears in the
output; an earlier cut pinned a CSS class name and broke on a restyle that
changed nothing about the page's meaning.
