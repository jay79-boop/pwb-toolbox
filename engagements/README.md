# engagements/

One folder per business going through the AI & automation readiness framework
(`docs/ai-readiness-framework.md`). Created and advanced by
`tools/engagement.py`; driven by the `engagement-flow` skill.

**Everything in here except this README is gitignored, deliberately.** This
fork is public, and an engagement folder holds a business's tool inventory,
process map, findings, and stakeholder feedback — confidential by default.
Do not add engagement contents to git, and do not weaken the ignore rules to
"just this one file".

A folder looks like:

```
engagements/acme-logistics/
  engagement.json            # phase state, notes, approval record
  01-audit.md … 11-golive.md # the phase deliverables
  deck.html                  # the rendered stakeholder deck
```

Lessons worth keeping beyond one engagement get promoted — sanitized — into
the skill or the playbook via `tools/engagement.py retro`. That is the only
path from this folder into the repository.
