# finviz/

Default save location for `tools/finviz_scan.py` output (`--out` on
`screener`/`lookup`, or the menu's "save to CSV" prompt). Nothing writes
here automatically -- the tool only saves when asked to.

Everything else here is gitignored: it's regenerable by re-running the
tool, and this fork is public.

| File | What it is |
| --- | --- |
| `*.csv` | screener results (`screener --out ...`, or the menu's save prompt) |
| `*.json` | one ticker's fundamentals/news/insider trades (`lookup --out ...`) |

`tools/finviz_scan.py --help` (or `menu`) for how to produce these.
