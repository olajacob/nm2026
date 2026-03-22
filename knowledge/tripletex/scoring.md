# Tripletex — scoring, efficiency & infra

**Task patterns:** [`task-registry.md`](task-registry.md) · **API:** [`api-quirks.md`](api-quirks.md) · **Leaderboard snapshot:** [`../competition/leaderboard.md`](../competition/leaderboard.md)

---

## How scoring works (competition model — verify on official brief)

- **Field-by-field** correctness × **tier multiplier**.
- **Tier 1:** **1×** · **Tier 2:** **2×** · **Tier 3:** **3×** (highest-value tasks — exact mapping on organiser doc).
- **Efficiency bonus** at **100%** correctness (typical ingredients):
  - **Write** calls (**POST** / **PUT** / **DELETE**) vs a **reference** solution count.
  - **Zero** **4xx** errors on writes (each **4xx** logged in agent as hurting efficiency).
- **GET** calls are usually **cheap** / excluded from write-efficiency counts — still avoid **abusive** full-chart scans (**403** token death).
- **Best score per task** kept (not last-only).
- **Max submissions per task per day** — confirm on platform (often **~10**).

---

## Efficiency rules (agent + practice)

- Every **4xx** on a tool costs **efficiency** — **fix payload** before retrying the same mistake.
- **Minimise** **POST/PUT/DELETE** — **GET** to see if resource exists before create.
- **No** spam **POST** after a deterministic **422** / **500** pattern (e.g. supplierInvoice **500/1000** → voucher fallback once).
- **Don’t** paginate **`GET /ledger/account`** without need — use **`number=NNNN`**.

---

## Tier 3–style tasks (high points on snapshot)

Tasks **25**, **26**, **27** show **high** raw scores (**~3.5–5.0**) on team snapshot — **prioritise** polish + **`task_id`** logging for remaining submits.

---

## What gives most points now (team prioritisation)

1. **Fix 11** (FCY + payment / FX journal) — large gap vs leader on snapshot.
2. **Fix 12** (voucher / bilag variant).
3. **Improve 13**, **17** — partial scores.
4. **Fix 22** (receipt PDF / GL + MVA).

---

## Infrastructure & commands

| Item | Command / path |
|------|----------------|
| Agent code | `nmai2026/tripletex/agent.py` |
| HTTP API | `nmai2026/tripletex/server.py` — **`POST /solve`** |
| Run server | `uvicorn` / `python server.py` per your deploy (see repo) |
| Local tunnel | `./ngrok http 8080` — use **`ngrok-skip-browser-warning: true`** on clients if interstitial |
| Kill port | `lsof -ti :8080 \| xargs kill -9` (macOS/Linux) |
| Health | `curl http://localhost:8080/health` |
| Logs | `nmai2026/tripletex/logs/last_solve.log` + `solve_*.log` |
| Sandbox tests | `cd nmai2026/tripletex && python3 test_sandbox.py --local-only` |
| Live tests | `python3 test_sandbox.py` (+ **`TRIPLETEX_SESSION_TOKEN`** or **`nmai2026/.env`**) |

---

## Logging for correlation

- Set **`task_id`** (JSON body), **`X-Task-Id`**, or env **`TASK_ID`** / **`NM_TASK_ID`** on **`POST /solve`** so **`last_solve.log`** matches grader rows and **`data.json` `last_run_task`**.

---

## Disclaimer

Exact **tier** boundaries and **efficiency** formula are **organiser-defined** — treat the **public leaderboard** and **NM** brief as source of truth; this file is **operational** guidance aligned with **`agent.py`** behaviour.
