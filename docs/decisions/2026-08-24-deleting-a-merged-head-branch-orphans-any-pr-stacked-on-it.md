# Deleting a merged head branch orphans any PR stacked on it

*Decided 2026-08-24.*

**Salvaged from three superseded ledger branches** (`claude/claude-md-state`,
`claude/fleet-lead-a-ledger`, `claude/ledger-accuracy-lead-b`), which were
closed without merging because they each rewrote a `CLAUDE.md` state block that
no longer exists. Everything else they carried was already on `main`; this was
the one fact that was not written down anywhere.

**What happened:** during the 2026-08-23 merge drain, #99 was based on #98's
head branch rather than on `main`. When #98 merged, `delete-merged-branch.yml`
deleted that head branch — correctly, by its own rules — and #99 was left
pointing at a base ref that no longer existed. **GitHub does not repair that on
its own.** The PR does not close, does not error, and does not announce
anything; it simply stops being mergeable, and the reason is not visible from
the PR page without checking the base ref by hand.

**Why the workflow is not at fault.** Its three guards (merged, same repo, not
the default branch) are all about whether the *head* branch is safe to delete.
None of them can see that some *other* open PR is using that branch as its
base, and GitHub's built-in "Automatically delete head branches" checkbox — the
setting this workflow deliberately mirrors — behaves identically. Turning the
workflow off would not fix this; it would only move the same failure to
whenever someone deleted the branch by hand.

**The rule that follows: branch off `main`, not off another PR's head.** A
stacked PR buys nothing here — this repository merges to `main` frequently
enough that the parent usually lands first anyway — and it costs a failure mode
that is silent. If a stack is genuinely needed, retarget the child onto `main`
the moment the parent merges, before the deletion job runs.

**And when a PR stops being mergeable for no visible reason, check its base ref
exists** before re-resolving conflicts or rebuilding the branch. The symptom
looks like a merge problem and is not one.
