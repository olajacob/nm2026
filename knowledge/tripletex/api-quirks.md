# Tripletex API — quirks & workarounds

**Canonical behaviour:** `nmai2026/tripletex/agent.py` (`SYSTEM_PROMPT`, `execute_tool`, `_apply_tripletex_get_sanitizers`, `post_voucher_two_step`). This file is a **checklist**; the code wins on conflict.

**Handoff:** [`../INDEX.md`](../INDEX.md) · [`../../tripletex/CLAUDE.md`](../../tripletex/CLAUDE.md) · long narrative [`../tripletex.md`](../tripletex.md)

---

## Authentication

- **HTTP Basic:** username **`"0"`**, password = **session token** (from **`/solve`** payload **`tripletex_credentials`** — fresh per submission).
- **Do not** reuse tokens across submissions / teams.
- **Sandbox token** expiry (e.g. **2026-03-31**) — confirm on platform; treat as **rotating**.
- **403** *Invalid or expired* (competition **proxy** / Tripletex): **cannot recover** in-run with a new JSON trick — **new token / new submission** from the platform.

---

## Bank account (**konto 1920**)

- **Competition invoice bank:** **`GET /ledger/account?number=1920&fields=id,number,bankAccountNumber`** → **`PUT /ledger/account/{id}`** with **`{"bankAccountNumber": "86011117947"}`** only (11 digits — **`COMPETITION_BANK_ACCOUNT`** in `agent.py`).
- **`SYSTEM_PROMPT`:** run this **only** when the task leads to **`PUT /order/{id}/:invoice`** (or explicit outgoing-invoice bank setup) — **skip** for pure project / timesheet / travel / payroll / voucher-only tasks (efficiency).
- **`requireReconciliation: true`**, **`isBankAccount: true`** — **never** post **1920** on **manual** **`tripletex_post_voucher`** lines (tool blocks unless env override).
- **FCY gain/loss journals:** use **8060** (gevinst) / **8160** (tap) with **1500** / **2900** — **not** **1920**.

---

## VAT types (`vatType` ids) — Norwegian outgoing shortcut

| Rate / role | id |
|-------------|-----|
| 25% **outgoing** (MVA utgående) | **3** |
| 15% outgoing | **31** |
| 12% outgoing | **32** |
| 0% outgoing | **6** |
| **Inngående** (incoming / fradrag on expense) | **1** |
| No VAT | **0** |

- On **`orderLines[]`**, set **`vatType: {id}`** **per line** when the prompt gives **different VAT %** per line — product default alone can be wrong.
- **Incoming VAT on receipts (manual bilag):** often **net + 2710 + 2740 −TTC** without **`vatType`** on the expense line — see **`SYSTEM_PROMPT`** Task 22 block.

---

## Customer / supplier quirk

- **`POST /customer`** with **`isCustomer: false`**, **`isSupplier: true`** → response may still show **`isCustomer: true`** (tenant quirk).
- **`agent.py`** still recommends **`GET /customer/{id}?fields=id,isCustomer,isSupplier`** then **`PUT /customer/{id}`** with **`{isCustomer: false, isSupplier: true}`** (both flags).
- **Even after PUT**, **`isCustomer`** may **remain true** — **accept** and continue when **`isSupplier: true`** for supplier flows; grader may or may not care.

---

## Employee

- **Before `POST /employee`:** **`GET /employee?email=<exact>&fields=id,firstName,lastName`** (omit **`email`** key entirely to page — empty string is rejected by **`execute_tool`**).
- **`POST /employee/employment`:** start with **minimal** **`{employee: {id}, startDate}`** — many tenants **404/422** if **`division`** / heavy fields on first POST.
- **`agent.py`:** tries **division `id` 1..12** on create; **PUT** division may hit **422** *«Virksomheten kan ikke endres»* — tool may **skip** further PUTs after minimal fallback; **GET** **`/employee/employment/{id}?fields=id,division`** before assuming failure.
- **`employmentPercentage`:** not a reliable field on employment in v2 for these flows — follow OpenAPI / task text.

---

## Invoices

- **`GET /invoice`** (list): **always** **`invoiceDateFrom`** + **`invoiceDateTo`** (e.g. **`2000-01-01`** … **`2099-12-31`**).
- **`fields`:** use **`invoiceDueDate`**, not **`dueDate`**; omit **`isPaid`**, **`amountIncludingVat`**, **`paid`** on list DTO where invalid.
- **`PUT /invoice/{id}/:payment`:** query **`paymentDate`**, **`paymentTypeId`**, **`paidAmount`**. **`paidAmount`** = **`amountOutstanding`** / **`amountCurrencyOutstanding`** from **`GET /invoice/{id}`** for **full** pay — **never** trust **FCY × rate** from prose alone (massive overpayment / guard in agent).
- **`GET /invoice/paymentType?fields=id`** → pick **`paymentTypeId`** (avoid invalid **`fields`** like bare **`name`** where stripped).

---

## Supplier invoice

- **Create body (first try):**  
  `invoiceNumber`, `invoiceDate`, `supplier: {id}`, **`amountCurrency`** (TTC / inkl. MVA), **`currency: {id: 1}`** (NOK in typical tenant).
- **HTTP 500 + code 1000** (NM sandbox common): **one retry** with same body **+** **`invoiceDueDate`** (e.g. +14 days heuristic).
- **Do not** repeat **`POST /supplierInvoice`** for same **`invoiceNumber` + supplier** after 500/1000 — use **`tripletex_post_voucher`** fallback (cost + inngående + **2400** + **`supplier`** on credit line).
- **Avoid** on create (known bad): **`comment`** quirks, **`account` on `orderLines`**, invalid keys like **`vatExemptAmount`**, **`vendorInvoiceNumber`**, **`department`** (*feltet eksisterer ikke*).
- **Approve:** **`PUT /supplierInvoice/{id}/:approve`** — **not** **`/:book`**.

---

## Voucher (`tripletex_post_voucher`) — retry order (`post_voucher_two_step`)

Tenants differ; **`agent.py`** order is:

1. **`POST /ledger/voucher?sendToLedger=false`** with **full** **`postings`**.
2. **`POST /ledger/voucher`** (no query) with **full** **`postings`**.
3. **`POST /ledger/voucher`** with top-level **`posting`** (singular) = array of lines.
4. **Hybrid:** first line **inline**, rest via **`POST /ledger/voucher/{id}/postings`** — **rotates** which line is first if **≥2** lines (some orderings 422, others pass).
5. **Last resort:** empty shell **`postings: []`** then **per-line** **`/postings`** (some tenants still **422** *«uten posteringer»* on empty shell).

**Also:**

- **Posting** uses **`freeAccountingDimension1..3`** on wire; agent maps **`accountingDimensionValues`** → free slots.
- **Balance:** **Σ `amountGross` = 0** or validation error.
- **Never 1920** on voucher lines (reconciliation / blocked).

---

## Depreciation / expense GL (avskrivning / cost)

- **Programvare / software / IT** → **6020** (often **not** **6010** — **6010** may be transport/depreciation class in many charts).
- **IT hardware / inventar** → **6020** or **6540** per receipt.
- **Kjøretøy / transport** → **6010** when chart matches.
- **Maskiner** → **6010** or **6000** per task naming — **`GET /ledger/account?number=…&fields=id,number,name`** to confirm **`name`**.

---

## Ledger / posting analysis

- **`GET /ledger/voucher/{id}`** without nested **`postings(...)`** may return **`postings`** as **`{id, url}`** stubs — **`execute_tool`** can expand nested **`fields`** for amounts.
- **Amounts:** **`GET /ledger/posting/{id}?fields=id,account,amountGross`** or nested **`postings(id,account(id,number,name),amountGross)`** on voucher detail.
- **Period comparison:** aggregate **`amountGross`** by **`account.number`** across postings.

---

## GET `fields` (other)

- **`GET /supplierInvoice`:** **`invoiceDateFrom`** + **`invoiceDateTo`** required.
- **`GET /salary/type`:** **`fields=id,name`** only.
- **`GET /activity`:** agent strips **`isInactive`**, **`activityNumber`** if needed.
- **Ledger voucher list:** invalid **`dateTo`** strings may be **clamped** (e.g. invalid day).

---

## Rate limiting & proxy

- **429:** agent uses **urllib3 Retry** on **GET/PUT/DELETE** (backoff) — **POST** largely **not** auto-retried (persistent 500 on e.g. supplierInvoice).
- **Many writes in a row** can still hit limits — fix payload before blind retry.
- **403** mid-session: often **proxy token budget** — minimise **`GET /ledger/account`** **full-chart** pagination; use **`number=NNNN`**.

---

## Timeouts & tunnels (local dev)

- **ngrok free:** interstitial — callers may need header **`ngrok-skip-browser-warning: true`**.
- **cloudflared:** ~**120 s** hard timeout — long **`/solve`** may fail.
- **Competition proxy:** ~**300 s** typical budget — align with organiser docs.
- **Prefer** ngrok + bypass header for interactive debugging when applicable.

---

## Travel

- **`POST /travelExpense`:** do **not** put **`paymentType`** on shell — costs go to **`POST /travelExpense/cost`** per OpenAPI (`SYSTEM_PROMPT`).

---

## Bank return vs credit note

- **Payment returned / reverse payment:** **`PUT /ledger/voucher/{paymentVoucherId}/:reverse`** with **`params.date`** — **not** **`:createCreditNote`**.
- **Credit the sale (Gutschrift / kreditnota):** **`PUT /invoice/{id}/:createCreditNote`**.
