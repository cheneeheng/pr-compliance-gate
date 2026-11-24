---
schema: nightshift-rule/v1
decided: 2026-11-24
source: "nightshift rule r-14fba9db"
---

# Workspace untrack branch switch

Before switching branches while the `.agents_workspace/` folder is being untracked, confirm the workspace is committed in its own repo or that the target branch no longer tracks those paths, and verify restored files against the workspace commit, because switching to `main` overwrote and then removed the ignored `DECISION_LOG.md`.
