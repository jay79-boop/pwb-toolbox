# night_lab/

Working directory for `tools/night_lab.py` — the overnight stress lab.

Everything else in this directory is gitignored. The queue, the nightly
reports and the staged proposals are all derived from the speculative desk's
trade records, which are personal, and this fork is public.

| File | What it is |
| --- | --- |
| `queue.jsonl` | tonight's jobs, checkpointed after each one |
| `verdict.json` | the morning one-screen; `broke` gates whether it speaks |
| `report-YYYY-MM-DD.md` | the full write-up for a night |
| `proposals.jsonl` | findings awaiting your approval — never auto-applied |

Protocol lives in `docs/night-lab.md`.
