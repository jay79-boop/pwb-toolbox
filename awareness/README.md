# Observation log

`tools/awareness.py record` appends one line per observation here. The log is
gitignored on purpose, for two reasons that both matter:

- **It would collide on every branch.** Every session doing real work would
  append to the same file, which is the shape `CLAUDE.md` split the ledger to
  avoid.
- **The tool reads the working tree.** A tracked, always-growing file means the
  layer reports its own output as uncommitted work — it did, on the first run.

Nothing here is precious. Delete it and the only thing lost is the answer to
"what is changing", which the tool will say it cannot answer rather than
reporting calm.
