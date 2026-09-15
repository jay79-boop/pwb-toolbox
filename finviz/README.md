# finviz/

Holds `tools/finviz_scan.py`'s config (`watchlist.txt`) and, when a path
under here is chosen, its saved output. **The tool's own default for saved
research files is your Desktop, not this folder** — see
`default_desktop_dir()` in `tools/finviz_scan.py` — so this directory stays
small unless you explicitly `--out finviz/...` something.

Everything here is gitignored except this README: `watchlist.txt` is
personal (what you're tracking), and everything else is regenerable by
re-running the tool. This fork is public.

| File | What it is |
| --- | --- |
| `watchlist.txt` | your tracked tickers, one per line (`watchlist add/remove/list`) |
| `*.csv` | screener results, if saved here instead of Desktop |
| `*.json` | one ticker's fundamentals/news/insider trades, if saved here |
| `*.txt` (other) | a watchlist `check` report, if saved here |

`tools/finviz_scan.py --help` (or `menu`) for how to produce these.
`docs/layout.md` has the full feature list, including the optional daily
watchlist check (`tools/install_finviz_watchlist_task.ps1`).
