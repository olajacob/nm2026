# Tripletex tasks 01–30 (registry)

**Live scores & `task_registry` JSON:** `nmai2026/tripletex/data.json` (not `dashboard/` — path is **`tripletex/data.json`**).  
**API quirks:** [`api-quirks.md`](api-quirks.md) · **Strategy / efficiency:** [`scoring.md`](scoring.md)

---

## Confirmed / inferred patterns (log + score snapshot)

Scores/tries match a **2026-03-22** sync; refresh from **`data.json`** after new submits.

| Task | Score | Tries | Pattern (working hypothesis) | Key endpoints / tools |
|------|-------|-------|------------------------------|------------------------|
| 01 | 1.50 | 9 | Employee creation | `GET /employee?email=` → `POST /employee`, employment |
| 02 | 2.00 | 6 | Order → invoice | `POST /order`, `PUT /:invoice`, optional `:send` |
| 03 | 2.00 | 6 | Strong — brief-specific | — |
| 04 | 2.00 | 8 | Strong — brief-specific | — |
| 05 | 1.33 | 6 | Partial | — |
| 06 | 1.20 | 7 | Dimension + **bilag** | `POST` dimension name/value → `tripletex_post_voucher` + **`freeAccountingDimension*`** |
| 07 | 2.00 | 8 | Strong | — |
| 08 | 2.00 | 8 | Travel / **reise** | `POST /travelExpense` + **`/travelExpense/cost`** |
| 09 | 2.67 | 7 | FCY / valuta / payment + maybe voucher | `PUT /:payment` + **`amountOutstanding`**, FX journal **8060/8160** if asked |
| 10 | 2.50 | 6 | Mixed flow | varies |
| 11 | — | 8 | FCY / **valutadifferanse** / invoice payment | `PUT /:payment` + voucher FX; **`paidAmount` from API** |
| 12 | — | 8 | Voucher / **bilag** | `tripletex_post_voucher` |
| 13 | 0.50 | 6 | Partial — refine with **`task_id` logs** | — |
| 14 | 4.00 | 10 | Max score on snapshot | — |
| 15 | 2.80 | 9 | Improving | — |
| 16 | 2.40 | 7 | Timesheet + invoice + **email** | `POST /timesheet/entry`, `:invoice`, **`PUT /invoice/.../:send`** |
| 17 | 1.38 | 9 | Partial | — |
| 18 | 4.00 | 6 | Travel expense reimbursement | `POST /travelExpense` + cost |
| 19 | 2.05 | 7 | Employee from **PDF** | PDF + `GET /employee` + employment + salary |
| 20 | 0.60 | 5 | **Hauptbuch** / cost analysis + **project** | `GET /ledger/voucher` + **`GET /ledger/posting/{id}`** → **`POST /project`** |
| 21 | 2.14 | 8 | Accounting mix | — |
| 22 | — | 5 | **Receipt PDF** / kvittering | PDF + `tripletex_post_voucher` (**7140**/6860 travel; **6540**/6800 IT; net+2710+2740) |
| 23 | 0.60 | 6 | **Supplier invoice** | `POST /supplierInvoice` or voucher fallback |
| 24 | 2.25 | 4 | Ledger / voucher | `tripletex_post_voucher` |
| 25 | 4.29 | 5 | **Fixed price** project | `POST /project` **`isFixedPrice`**, order, `:invoice` |
| 26 | 3.50 | 5 | Project + timesheet | `POST /project`, `POST /timesheet/entry` |
| 27 | 5.00 | 7 | **FCY** project invoice / payment | EUR invoice + **`:payment`** + outstanding |
| 28 | 0.60 | 5 | **Cost analysis** + project | `GET /ledger/voucher` + posting detail |
| 29 | 1.64 | 7 | Full lifecycle (e.g. FR) | project + hours + supplier + order + invoice + send |
| 30 | 1.80 | 8 | **Avskrivning** / month-end / tax | `tripletex_post_voucher` — **6020**/12xx/**8300** per prompt |

---

## `data.json` `task_registry` (extra detail)

- **06:** Kostsenter-style dimension + **30250** on **7000** — wrong **7000/7000** or empty-shell voucher **422**; see **`ERRORS.md`** / mitigations in **`agent.py`**.
- **20:** DE cost-increase text → must **finish** with ranked GL + **`POST /project`**, not GET-only.
- **22:** **6010** wrong for travel — use **7140**/6860; hardware **6540**/6800.
- **23:** **`POST /supplierInvoice`** **500/1000** → voucher path documented in **api-quirks.md**.

---

## Gap vs leader (illustrative — **“Ave Christus Rex”**)

Use platform leaderboard to refresh numbers; table is **heuristic priority**.

| Task | Our score | Leader (ex.) | Gap | Priority |
|------|-----------|--------------|-----|----------|
| 11 | 0 | 4.00 | 4.00 | **CRITICAL** |
| 12 | 0 | 4.00 | 4.00 | **CRITICAL** |
| 22 | 0 | — | ? | **HIGH** |
| 09 | 2.67 | 4.00 | 1.33 | **HIGH** |
| 13 | 0.50 | 2.40 | 1.90 | **HIGH** |
| 17 | 1.38 | 3.50 | 2.12 | **HIGH** |
| 06 | 1.20 | 1.67 | 0.47 | MEDIUM |
| 28 | 0.60 | 1.50 | 0.90 | MEDIUM |
| 23 | 0.60 | 0.60 | 0.00 | LOW |

---

## Null-score focus

**11**, **12**, **22** historically **—** on scoreboard — tie logs with **`task_id`** to grader rows; backlog **b2**, **b4** in **`data.json`**.

### Task 12 — logs

Local **`solve_*.log`** files rarely include **`task_id`**, so **`grep` “task 12”** does **not** map prompts to grader task **12**. Treat **12** as **manual voucher / bilag** (same family as **06**/**24**): balance, dimensions, **no** **1920** on voucher lines.

### Tasks 13 / 17

**Patterns TBD** until **`POST /solve`** sends **`task_id`** / **`X-Task-Id`** — use platform task text + finish all required **writes** (no **GET-only** **`end_turn`**).

---

## Last solve log (hint)

`tripletex/logs/last_solve.log` — German **timesheet** example: **`GET /employee?email=`** → project/activity resolve → **`POST /timesheet/entry`** → **1920** GET/PUT before further invoice steps.
