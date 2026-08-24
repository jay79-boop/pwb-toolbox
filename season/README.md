# season/

Working directory for `tools/season_scan.py` — the seasonality lab.

Everything else here is gitignored: the data is fetched public market
history, but `universe.txt` names what the owner watches and the report is
derived from it, and this fork is public.

| File | What it is |
| --- | --- |
| `data/*.csv` | daily closes per ticker (`fetch` writes them) |
| `season-report.html` | the visual report — heatmap, average-year paths, now-window, folklore verdicts |
| `season.json` | the scan for other tools (pre-trade pack, night lab) |
| `tradingview-watchlist.txt` | sectioned watchlist, import-ready |
| `universe.txt` | optional: one ticker per line, added to the default universe |

Protocol lives in `docs/season-scan.md`.
