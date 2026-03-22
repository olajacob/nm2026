# Knowledge base — NM i AI 2026

**Start each session here.** Load **only** the files that match the task (progressive disclosure). Full narrative docs remain as siblings for deep dives.

## Routing

| Context | Open |
|--------|------|
| **Tripletex API** work (auth, 1920, VAT, invoices, supplierInvoice, vouchers, 429/403) | [tripletex/api-quirks.md](tripletex/api-quirks.md) |
| **Tripletex task** investigation (01–30 patterns, gaps vs leader) | [tripletex/task-registry.md](tripletex/task-registry.md) |
| **Tripletex scoring / strategy / efficiency / infra** | [tripletex/scoring.md](tripletex/scoring.md) |
| Tripletex long-form (bank CSV, flows) | [tripletex.md](tripletex.md) |
| **NorgesGruppen** training, mAP, submissions | [norgesgruppen/model.md](norgesgruppen/model.md) |
| **NorgesGruppen** sandbox, zip, blocked imports | [norgesgruppen/sandbox.md](norgesgruppen/sandbox.md) |
| NG track limits & scoring detail | [norgesgruppen.md](norgesgruppen.md) |
| Deadlines, tracks, submission | [competition/rules.md](competition/rules.md) |
| Leaderboard snapshot & gaps | [competition/leaderboard.md](competition/leaderboard.md) |
| Astar Island | [astar.md](astar.md) |
| Repo layout, env | [repo.md](repo.md) |
| Test-before-submit, `/nmai` workflow | [conventions/workflow.md](conventions/workflow.md) |
| Recurring failures (append new) | [ERRORS.md](ERRORS.md) |

## Claude Code / Tripletex agent

- **Runtime truth:** `nmai2026/tripletex/agent.py` (`SYSTEM_PROMPT`, `execute_tool`, sanitizers).
- **Handoff stub:** `nmai2026/tripletex/CLAUDE.md` → **INDEX** + **tripletex/api-quirks.md**.

## Maintenance

- Promote stable conclusions from **ERRORS.md** into the right topic file; trim the log.
- Refresh **competition/leaderboard.md** and **`tripletex/data.json`** when the platform scoreboard changes.
