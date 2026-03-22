# Leaderboard snapshot (team)

**Source of truth:** competition website + `nmai2026/tripletex/data.json` (`updated`, `leaderboard`, `tasks`, `notes`).  
**Last synced label:** `data.json` → **`updated`** field.

## Example snapshot (from `data.json` sync #18)

| Field | Value |
|------|--------|
| Team | Kongsberg Development Agent 🇳🇴 |
| Rank | 128 / 376 |
| Total | 58.75 |
| Tripletex | 20.3 |
| NorgesGruppen | 14.0 |
| Astar | 24.5 |
| Submissions (counter) | 206 |

## Gap analysis (Tripletex tasks)

- **No score (—):** **11**, **12**, **22** — invoice/FCY, voucher, receipt PDF (see **`tripletex/task-registry.md`** + **`data.json` backlog** **b2**, **b4**).
- **Low Tripletex:** **13** (0.50), **20** / **23** / **28** (0.60), **05**, **17**, **06**, **16**.
- **Strong Tripletex:** **27** (5.00), **25** (4.29), **14** / **18** (4.00).

## Maintenance

When the platform updates, copy new totals into **`tripletex/data.json`** and bump this file’s narrative, or replace this section with a one-liner “see `data.json`”.
