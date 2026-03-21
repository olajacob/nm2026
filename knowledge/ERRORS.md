# Error log — NM i AI 2026

**Rules**

- **Deterministic** (wrong types, schema, logic, wrong HTTP/API usage): log, **conclude**, move conclusion to the relevant `knowledge/*.md` and reference it here.
- **Infrastructure** (timeout, rate limit, flaky network, port bind): log **only**; no root-cause conclusion until a pattern repeats.

Newest entries at the **top**.

---

## Log

### 2026-03-22 — **403 proxy token:** full **`GET /ledger/account`** chart scan before bilag

- **Symptom**: Many **`tripletex_get`** calls with **`from`/`count`** over **`/ledger/account`**, then **403** *Invalid or expired proxy token*; **`tripletex_post_voucher`** never persists.
- **Type**: **Infrastructure** (token budget) + **deterministic** (avoidable call pattern).
- **Conclusion**: Resolve each stated GL number with **`GET /ledger/account?number=N&fields=id,number,name`** — **not** full-chart pagination. **`agent.py` SYSTEM_PROMPT** + **`tripletex_get`** tool text updated — **→** [tripletex.md](tripletex.md) **Custom dimensions & ledger**.

### 2026-03-22 — **Ledger voucher:** empty shell **422** *«…uten posteringer»* — **one-step first**

- **Symptom**: **`tripletex_post_voucher`** used **POST /ledger/voucher?sendToLedger=false** with **`postings: []`**, then **`/postings`** sub-resource — tenant returns **422** *«Et bilag kan ikke registreres uten posteringer»* (must not create a shell **without** lines).
- **Type**: Deterministic (tenant validation).
- **Conclusion**: **`post_voucher_two_step`** now **POST**s the **full** **`postings`** array on **`/ledger/voucher`** first (`?sendToLedger=false`), then retries **without** the **`sendToLedger`** query if needed, then falls back to shell + **`/postings`** on **systemgenererte** / remaining **422** — **→** `agent.py`, [tripletex.md](tripletex.md), **SYSTEM_PROMPT**.

### 2026-03-21 — **«Crie e envie»** / **send invoice** — **0/7** despite **200** API

- **Symptom**: Tripletex run completes **POST /order** + **`PUT /order/{id}/:invoice`** with **200**; platform shows **Task 0/7**, all checks failed. Log shows *Faktura må sendes manuelt* on the order / invoice is created but not transmitted.
- **Type**: Deterministic (task wording + missing API step).
- **Conclusion**: **`PUT /order/.../:invoice`** **creates** the invoice document; **sending** is a separate action: **`PUT /invoice/{invoiceId}/:send`** with **required** query **`sendType`** (**EMAIL**, **EHF**, **MANUAL**, …). Portuguese **«envie»**, English **«send»**, etc. require this step — **concluded.** **→** `agent.py` **SYSTEM_PROMPT** (Create invoice step 5), **`tripletex_put_action`** tool text, [tripletex.md](tripletex.md).

### 2026-03-22 — **Multi-rate invoice:** order lines inherit **product** **vatType**

- **Symptom**: Task states **different VAT % per invoice line**; **`POST /order`** uses **`orderLines`** **without** **`vatType`**; invoice lines show wrong **vatType** (e.g. **0%** from product master on a line that should match another rate) — grader / checks fail despite **200** API responses.
- **Type**: Deterministic (OpenAPI **OrderLine** supports **`vatType`**; omission falls back to product default).
- **Conclusion**: When the prompt gives VAT **per line**, set **`vatType: {id}`** on **each** **`orderLines[]`** row. **Norwegian outgoing** competition shortcut: **25% → 3**, **15% → 31**, **12% → 32**, **0% → 6** — **no** full **`GET /ledger/vatType`** unless the rate is non-standard or **POST /order** **422**. **`execute_tool`** maps **`vatRatePercent`** / **`vatPercent`** on a line for those four rates. **→** `agent.py` **`_enrich_order_post_body`**, **SYSTEM_PROMPT**, [tripletex.md](tripletex.md) **POST /product** / **Invoice creation**.

### 2026-03-20 — Payroll: **`employee.dateOfBirth`** + **`Virksomheten kan ikke endres`**

- **Symptom 1**: **`POST /employee/employment`** → **422** **`employee.dateOfBirth`** / *«Feltet må fylles ut»* when the employee exists but **`dateOfBirth`** is **null** on **`GET /employee/{id}`**.
- **Symptom 2**: **`PUT /employee/employment/{id}`** **`{division: {id: 1}}`** → **422** *«Virksomheten kan ikke endres»* — tenant does not allow changing **virksomhet** on that row.
- **Type**: Deterministic (API / tenant rules).
- **Conclusion**:
  - **Before** first **`POST /employee/employment`** for an existing employee: **`GET /employee/{id}?fields=dateOfBirth`**; if **null**, **`PUT /employee/{id}`** **`dateOfBirth`** (prompt or **`1990-01-01`** if task silent) — **one** placeholder, **concluded.**
  - **422** *«Virksomheten kan ikke endres»*: **do not** retry **division** 2/3 — **concluded.** Proceed to **`POST /salary/transaction`**; reassess only if that POST fails on **division** / **arbeidsforhold**.
- **→** `agent.py` **SYSTEM_PROMPT** (Create employee Step 3–4, Run payroll Step 0), [tripletex.md](tripletex.md) **Payroll**.

### 2026-03-21 — Payroll: **`division` on POST employment** + lock after salary **virksomhet** error

- **Symptom**: **`PUT`** **`division`** on existing employment → **422** *«Virksomheten kan ikke endres»*; **`POST /salary/transaction`** → *«Arbeidsforholdet er ikke knyttet mot en virksomhet»*; wasted **`PUT`** retries.
- **Type**: Deterministic (tenant rules).
- **Conclusion**: Prefer **`division: {id: 1}`** on **`POST /employee/employment`** (OpenAPI **Employment**); **`execute_tool`** injects when missing. On salary **422** with **virksomhet** message, lock **all** employment ids for that employee (no further **`PUT`** **division**) — **concluded.** **→** `agent.py` **`_enrich_employment_post_body`**, **`_lock_employments_for_employees`**.

### 2026-03-21 — Payroll: **`specifications.count` / `rate`** + runtime **division** lock

- **Symptom 1**: **`POST /salary/transaction`** → **422** on **`payslips.specifications`** — *«Kan ikke være null»* for **`count`** / **`rate`** when only **`amount`** was sent.
- **Symptom 2**: After **422** *«Virksomheten kan ikke endres»* on **`PUT /employee/employment/{id}`** **`division`**, the model still issued **`PUT`** with **`division` 2** and **3** — wasted calls.
- **Type**: Deterministic (OpenAPI **SalarySpecification**) + agent behaviour.
- **Conclusion**:
  - Include **`count`** and **`rate`** on each line (e.g. **`count: 1`**, **`rate`** = **`amount`** for fixed pay); **`execute_tool`** auto-enriches from **`amount`** when possible — **concluded.**
  - **`PUT`** **`division`** blocked after first **422** *«Virksomheten kan ikke endres»* for that **employment id** (per `/solve`) — **concluded.** **→** `agent.py` **`execute_tool`**, **SYSTEM_PROMPT** Run payroll.

### 2026-03-20 — **`tripletex_post_voucher`** + **`sendToLedger: true`** → 422 *«…uten posteringer»*

- **Symptom**: **`POST /ledger/voucher?sendToLedger=true`** (or default) with **`postings: []`**, then lines would follow → **422** `postings` / *«Et bilag kan ikke registreres uten posteringer»*.
- **Type**: Deterministic (Tripletex validates ledger posting before lines are attached).
- **Conclusion**: Create shell with **`?sendToLedger=false`**, **POST** each line to **`/ledger/voucher/{id}/postings`**, then **`PUT /ledger/voucher/{id}/:sendToLedger`**. Implemented in **`post_voucher_two_step`** — **concluded.**
- **→** `agent.py`, [tripletex.md](tripletex.md) **Custom dimensions & ledger**.

### 2026-03-20 — **GET/PUT 1920** on **project Festpreis** only (no **`:invoice`**)

- **Symptom**: Task is **only** fixed price on a **project** (e.g. German **Festpreis**, **`isFixedPrice`** + **`fixedprice`**) + customer + PM; agent still runs **`GET /ledger/account`** **`1920`** + **`PUT` bankAccountNumber** first.
- **Type**: Deterministic (wasted calls / efficiency).
- **Conclusion**: **1920** is **only** for outgoing **invoice** creation (**`PUT /order/.../:invoice`** or explicit invoice-bank setup). **Skip** for pure **project / timesheet / travel / payroll** tasks — **concluded.** **→** `agent.py` **SYSTEM_PROMPT** (**WHEN TO SKIP**), [tripletex.md](tripletex.md) **Company bank account**.

### 2026-03-20 — “Payment returned by bank” → wrong **`:createCreditNote`**

- **Symptom**: Task asks to **reverse** a customer payment / bank **returned** the transfer so the **invoice** shows **outstanding** again; agent calls **`PUT /invoice/{id}/:createCreditNote`** → new **credit note** invoice (negative amounts) instead of reopening the **original** invoice.
- **Type**: Deterministic (wrong business action).
- **Conclusion**: **`:createCreditNote`** credits the **sale**. For **failed inbound payment**, undo the **payment voucher**: **`GET /invoice/{id}`** (**`postings`**) → **`voucher.id`** on the **payment** posting (typically **negative** line) → **`PUT /ledger/voucher/{voucherId}/:reverse`** with query **`date`** — **concluded.** ([sandbox OpenAPI](https://kkpqfuj-amager.tripletex.dev/v2/openapi.json): **`LedgerVoucherReverse_reverse`**; **Invoice** **`postings`** description.)
- **→** [tripletex.md](tripletex.md) **Other actions**, `agent.py` **SYSTEM_PROMPT** + **`tripletex_put_action`** + **`tripletex_get`**.

### 2026-03-19 — GET /travelExpense/paymentType: invalid **fields** **name**

- **Symptom**: **400** *«Illegal field in fields filter: name … does not match a field in the model: TravelPaymentTypeDTO»* on **`GET /travelExpense/paymentType?fields=id,name`**.
- **Type**: Deterministic (same pattern as **`GET /invoice/paymentType`** — **name** not on this list DTO).
- **Conclusion**: Use **`GET /travelExpense/paymentType?fields=id`** only; **`TripletexAPI.get`** strips invalid **`fields`** for this path — **concluded.**
- **→** [tripletex.md](tripletex.md) **Travel expense**, `agent.py` **`_sanitize_tripletex_get_params`**, **SYSTEM_PROMPT**, `tripletex_get` / `tripletex_post`.

### 2026-03-19 — POST /ledger/voucher «postings: Kan ikke være null» — Swagger truth

- **Symptom**: **422** validation *«postings: Kan ikke være null»* even when the client “intended” to send lines — often **`postings`** key **missing** from JSON (or shell create stripped it entirely).
- **Investigation** ([Tripletex API v2 Swagger](https://tripletex.no/v2-docs/) / **`openapi.json`** **Voucher**):
  1. **`posting`** (singular) and **`rows`** are **not** **Voucher** properties — **wrong**.
  2. The correct field is **`postings`** (array of **Posting**).
  3. **`POST /ledger/voucher/importDocument`** is **multipart/form-data** **`file`** (+ optional **`description`**) — for document upload, **not** JSON journal bodies.
Each **Posting** includes **`row`** (integer ≥ 0 in spec); use **1-based** line numbers **`1, 2, 3…`** when not set.
- **Working pattern**:
  - **Never omit** **`postings`**: use **`tripletex_post_voucher`** (shell **`postings: []`**, deretter **POST `/ledger/voucher/{id}/postings`** per linje). **`tripletex_post`** på **`/ledger/voucher`** er **blokkert** i `agent.py`.
  - Hvis en tenant skulle godta **én** **POST** med **`postings`: […]**, må **`postings`** fortsatt være satt og hver linje ha **`row`** — i praksis bruk **verktøyet** over.
- **→** [tripletex.md](tripletex.md) **Custom dimensions & ledger**, `agent.py` **`post_voucher_two_step`** / **`tripletex_post_voucher`**, **SYSTEM_PROMPT** Create ledger voucher. (**`tripletex_post`** must **not** be used on **`/ledger/voucher`**.)

### 2026-03-19 — GET /travelExpense/costCategory list is id-only; vatType on travel costs

- **Symptom**: **`GET /travelExpense/costCategory`** returns **`values`** with **`id`** only — cannot pick **Transport** / **Overnatting** / **Diett** / **Fly** from the list alone; wrong **costCategory** or wrong **vatType** on **`POST /travelExpense/cost`**.
- **Type**: Deterministic (list DTO vs detail DTO) + task logic (**vatType**).
- **Conclusion**:
  - Use **`GET /travelExpense/costCategory/{id}`** for full **displayName** / **description** (and category **vatType** hint). **`tripletex_get`** on the **list** path **auto-enriches** and **caches** per **`/solve`** in **`agent.py`**.
  - Map categories before posting costs (flights → **Transport**/**Fly**, taxi/ground → **Transport**, hotel → **Overnatting**, diett → **Diett** as appropriate); **reuse** chosen **`costCategory.id`** for all lines in-session (**working memory**).
  - **vatType**: often **`1`** (25%) for domestic VAT costs; **`0`** (no VAT) for **per diem** / **diett** lines — **not** **`1`** for everything — **concluded.**
- **→** [tripletex.md](tripletex.md) **Travel expense**, `agent.py` **`TripletexAPI.get`** + **SYSTEM_PROMPT** + `tripletex_get` / `tripletex_post`.

### 2026-03-19 — POST /ledger/voucher «Posteringene er systemgenererte»

- **Symptom**: **POST `/ledger/voucher`** with a **non-empty** **`postings`** array → error that postings are system-generated / not accepted on create.
- **Type**: Deterministic (API flow).
- **Conclusion**: **POST** voucher shell **`{date, description, postings: []}`** (**`postings` key required** — see **«postings: Kan ikke være null»** log), then **POST `/ledger/voucher/{id}/postings`** per line with **`amountGross`**, **`row`**, **`accountingDimensionValues`** as needed. **`importDocument`** = multipart **file** only — **concluded.**
- **→** [tripletex.md](tripletex.md) **Custom dimensions & ledger**, `agent.py` **SYSTEM_PROMPT**, **`tripletex_post_voucher`**. Superseded shell shape: **omit** **`postings`** entirely (**wrong**).

### Pinned conclusions (Tripletex, 2026-03-21)

- **Payroll / `POST /salary/transaction`:** employment usually needs **`division`** (virksomhet). Prefer **`division: {id: 1}`** on **`POST /employee/employment`** (injected in **`execute_tool`** when missing). Some tenants **lock** later **PUT** division (**422** *«Virksomheten kan ikke endres»*) — **do not** retry other ids. **`GET /company/divisions`** often **403** — **concluded.**
- **Payroll / `dateOfBirth`:** before **`POST /employee/employment`**, **`GET /employee/{id}?fields=dateOfBirth`**; if **null**, **`PUT /employee/{id}`** **`dateOfBirth`** (prompt or **`1990-01-01`**) — **concluded.**
- **Employment `division`:** **`GET /employee/employment/{id}?fields=id,division`**; if **null** and **PUT** allowed: **`PUT /employee/employment/{id}`** **`{"division": {"id": 1}}`** — **403** → **`2`**, **`3`**; **422** *«Virksomheten…»* → **stop** **PUT**s — **concluded.**
- **Before payroll:** **`GET /employee/employment?employeeId=X&fields=id,startDate,division`**; if none, **`POST /employee/employment`** with **`division: {id: 1}`** (auto) + **`startDate`**, **`isMainEmployer`**, **`taxDeductionCode`**: **`loennFraHovedarbeidsgiver`** — **concluded.**
- **Invoice bank account:** **`GET /ledger/account?number=1920&fields=id,number,bankAccountNumber`** ( **`1920` always exists** — faster than listing bank accounts) → **`PUT /ledger/account/{id}`** **`{bankAccountNumber: \"86011117947\"}`** — **do not** **`POST`** new **1921** — **concluded.**
- **Account 1920** (invoice bank) **already** has **`isInvoiceAccount: true`** — competition fix is **only** to set **`bankAccountNumber`** — **concluded.**
- **Uniqueness:** each **`bankAccountNumber`** may belong to **at most one** ledger account — **concluded.**
- **`POST /ledger/accountingDimensionName`:** request field is **`dimensionName`**, **not** **`name`** / **`displayName`** — **concluded.**
- **`POST /ledger/accountingDimensionValue`:** request field is **`displayName`**, **not** **`value`** / **`name`** — **concluded.**
- **Manual ledger voucher:** use **`tripletex_post_voucher`** (two-step: shell **`postings: []`**, then one **`POST …/postings`** per line with **`row`**, **`amountGross`**, etc.). **Never** **`tripletex_post`** on **`/ledger/voucher`**. Swagger field **`postings`** only — **not** **`posting`** / **`rows`**. **concluded.**
- **Posting line dimensions:** **`accountingDimensionValues: [{"id": Z}]`** (**dimension value** id). **`freeAccountingDimension1`** is **wrong** for **`/postings`** sub-resource — **concluded.**
- **Travel cost categories:** list **`GET /travelExpense/costCategory`** → **id-only**; **detail** **`GET /travelExpense/costCategory/{id}`** (or rely on **auto-enriched** list in **`agent.py`**). **Reuse** resolved **`costCategory.id`** for all **`POST /travelExpense/cost`** in the run — **concluded.**
- **Travel cost `vatType`:** **`{id: 1}`** typical for domestic **25%**; **`{id: 0}`** for **per diem** / **diett** — **do not** default **1** on every line — **concluded.**
- **Ledger account 1920:** always present in chart — **`GET`** by **`number=1920`** directly — **concluded.**
- **`POST /product`:** always check **`GET /product`** first — **name** and **number** must be **unique** — **concluded.**
- **`POST /product` (detail):** use **`GET /product`** with **`name`** / **`productNumber`** before create; on **422** *«Produktnummeret … er i bruk»* or *«Produktnavnet … er allerede registrert»*, **reuse** existing product **`id`** — if duplicate **number** and GET finds nothing: one **POST** **without** **`number`** (do not invent a substitute).
- **Bank-return / reverse payment:** **`GET /invoice/{id}`** (**`postings`**) → **`PUT /ledger/voucher/{paymentVoucherId}/:reverse?date=…`** — **not** **`:createCreditNote`** — **concluded.**

### 2026-03-21 — POST /product duplicate number: omit `number`, do not invent

- **Symptom**: **`POST /product`** with task-given **`number`** → **422** *«Produktnummeret X er i bruk»*.
- **Type**: Deterministic (number collision / product already exists).
- **Conclusion**: **Prefer** **`GET /product`** (`name` / **`productNumber`**) and **reuse** **`id`**. **Do not** chain wasted POSTs. If a new POST is still needed for duplicate **number** only: **omit** **`number`** on one retry — **concluded.**
- **→** [tripletex.md](tripletex.md), `agent.py` (`SYSTEM_PROMPT` **Create product**, `tripletex_get`, `tripletex_post`).

### 2026-03-21 — POST /product duplicate name: Produktnavnet er allerede registrert

- **Symptom**: **`POST /product`** → **422** *«Produktnavnet … er allerede registrert»*.
- **Type**: Deterministic (product already exists).
- **Conclusion**: **`GET /product?name=...`** (and **`productNumber`** if relevant) → **reuse** returned **`id`** — **concluded.**
- **→** [tripletex.md](tripletex.md), `agent.py` (`SYSTEM_PROMPT`, `tripletex_get` / `tripletex_post`).

### 2026-03-22 — Invoice bank: update account **1920**, not **POST** **1921**

- **Symptom**: **`POST /ledger/account`** **1921** / **Driftskonto** did not stop **422** *«…bankkontonummer»* on **`PUT /order/:invoice`**; wrong ledger row for invoice settlement.
- **Type**: Deterministic (competition chart — invoice account is **1920** **Bankinnskudd**).
- **Conclusion**: **`GET /ledger/account?number=1920&fields=id,number,bankAccountNumber`** → **`PUT /ledger/account/{id}`** **`{bankAccountNumber: \"86011117947\"}`** — **concluded.** (Earlier **`isBankAccount=true`** scan still valid if **`number`** query is unavailable.) Supersedes **«Bank account setup: exact POST»** (**1921**).
- **→** [tripletex.md](tripletex.md), `agent.py` (`SYSTEM_PROMPT`, `tripletex_get`, `tripletex_put`, `tripletex_post` description).

### 2026-03-21 — Bank account: `isInvoiceAccount` required for invoice creation

- **Context**: Competition **1920** **Bankinnskudd** already has **`isInvoiceAccount: true`**; the gap was **empty** **`bankAccountNumber`**.
- **Conclusion**: **PUT** **`bankAccountNumber`** on that row — see **pinned conclusions** and **2026-03-22** — **concluded.**
- **→** [tripletex.md](tripletex.md), `agent.py` (`SYSTEM_PROMPT` **MANDATORY SETUP BEFORE INVOICE TASKS**).

### 2026-03-21 — GET /invoice/paymentType fields

- **Symptom**: `GET /invoice/paymentType?fields=id,name` triggers 4xx because `name` is not a valid field.
- **Type**: Deterministic (invalid field selection).
- **Conclusion**: For payment type lookup use **`GET /invoice/paymentType?fields=id`** only; `name` does not exist on this response in the competition API — **concluded.**
- **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT.

### 2026-03-22 — Salary transaction: «Arbeidsforholdet er ikke knyttet mot en virksomhet»

- **Symptom**: **`POST /salary/transaction`** fails because employment is not tied to a **division** (virksomhet); **`GET /company/divisions`** returns **403**, so dynamic division lookup is blocked.
- **Type**: Deterministic (schema / permissions).
- **Conclusion**: After **`POST /employee/employment`**, **`GET /employee/employment/{id}?fields=id,division`**; if **`division`** null, **`PUT /employee/employment/{id}`** **`{"division": {"id": 1}}`** — on **403**, **`2`**, **`3`**; **log** each try — **if** **422** *«Virksomheten kan ikke endres»*, **stop** division retries (see **2026-03-20** payroll log). Before **`POST /salary/transaction`**, ensure **`dateOfBirth`** on employee if required; **`GET /employee/employment?employeeId=X&fields=id,startDate,division`**; create employment if missing, then **`PUT`** **`division`** only when allowed. Prefer **`division.id: 1`** when **PUT** succeeds — **concluded.**
- **→** [tripletex.md](tripletex.md), `agent.py` **SYSTEM_PROMPT** (Create employee Step 4, Run payroll).

### 2026-03-21 — Payroll endpoint and salary type lookup

- **Symptom**: Payroll attempts used `/salary` or `/payroll` and failed/underperformed checks.
- **Type**: Deterministic (wrong endpoint flow).
- **Conclusion**:
  - Payroll creation endpoint is **`POST /salary/transaction`** (not `/salary` or `/payroll`) — **concluded.**
  - Resolve salary type ids with **`GET /salary/type`** before creating transactions (base: fastlønn/grunnlønn; bonus: bonus/tillegg) — **concluded.**
- **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + `tripletex_post`.

### 2026-03-21 — Bank account setup: exact `POST /ledger/account` body (confirmed)

- **Superseded (2026-03-22)** — competition **invoice** bank is **1920** **Bankinnskudd**; use **`PUT /ledger/account/{id}`** per log **«Invoice bank: update account 1920»**. Kept for history:
- **Symptom**: Extra **422** attempts when invoice prep used wrong kontonummer / missing **currency** / wrong GL **number**.
- **Type**: Deterministic (payload).
- **Historical note**: **`POST /ledger/account`** **1921** was tried in competition; **correct** fix is **PUT** **1920** — see **pinned conclusions** and **2026-03-22** entry.
- **Still valid:** **`bankAccountNumber`**: **exactly 11 digits**; **`86011117947`** **confirmed**; **do not** use **`1503.40.12345`**, **`12345678901`**, **`15034012345`**.
- **→** [tripletex.md](tripletex.md), `agent.py`.

### 2026-03-20 — Company bank account required before invoice creation (competition)

- **Symptom**: **`PUT /order/{id}/:invoice`** → **422** *«Faktura kan ikke opprettes før selskapet har registrert et bankkontonummer»*. Fresh proxy accounts often have **no** bank registered.
- **Type**: **Deterministic** setup step (not random API flakiness). **`Company`** object has **no** bank field in OpenAPI.
- **Conclusion**: **Superseded** — use **`GET` → `PUT /ledger/account/{id}`** on **1920** with **`86011117947`** and **`currency` {id:1}** (see **2026-03-22** entry). Old trial values (**1503…**) **fail**.
- **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + `tripletex_post`.

### 2026-03-20 — HTTP 403 mid-session: do not stop; continue planned calls

- **Symptom**: **403** (`Invalid or expired token`) on one Tripletex call during `/solve`; agent ends before finishing all planned steps (e.g. only one of three **POST /department** calls).
- **Type**: **Infrastructure** — competition **proxy** / **session_token** expiry; **not** an agent JSON logic bug.
- **Handling**: **Log only** for root cause; operator supplies **fresh** token. **Agent**: must **not** give up after a single 403 — **continue** remaining planned API calls (token may still work). **Frequent** 403 → contact **organisers** (e.g. Slack); not fully fixable in code.
- **→** [tripletex.md](tripletex.md), `agent.py` (`SYSTEM_PROMPT`, `execute_tool` logging, `run_agent` follow-up nudge).

### 2026-03-19 — TravelExpense structure & /travelExpense/cost field names

- **Symptom**: **422** or wrong data when **departureDate** / **returnDate** are top-level on **POST /travelExpense**, or when using **amountCurrencyInclVAT** / **description** / **paymentCurrency** on **POST /travelExpense/cost**.
- **Type**: Deterministic (schema).
- **Conclusion**:
  - Trip fields live under **`travelDetails`** on **POST /travelExpense** — **concluded.**
  - **POST /travelExpense/cost**: use **`amountCurrencyIncVat`**, **`amountNOKInclVAT`**, **`comments`**, **`paymentType`** — **concluded.**
- **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + `tripletex_post`.

### 2026-03-19 — POST /ledger/voucher posting fields (OpenAPI)

- **Symptom**: 422 or wrong journal lines when using `debit` / `credit` / `debitAmount` / `creditAmount` on postings.
- **Type**: Deterministic (schema).
- **Conclusion**: Posting objects use **`amountGross`** (or **`amount`**) — **positive = debit**, **negative = credit**; sum must be **zero**. **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + `tripletex_post`.

### 2026-03-19 — PUT /order/:invoice 422 «Faktura kan ikke opprettes før selskapet har registrert…»

- **Symptom**: Order + **:invoice** med gyldige datoer, men **422** `VALIDATION_ERROR` med norsk melding om at faktura ikke kan opprettes før selskapet har registrert (resten av setningen varierer).
- **Type**: **Tripletex tenant / selskapskonfigurasjon** (ikke feltnavn på ordre/faktura-dato) — **unless** meldingen eksplisitt sier **bankkontonummer**.
- **Handling**: Hvis teksten nevner **bankkontonummer** → **`GET /ledger/account`** → **`PUT /ledger/account/{id}`** på **1920** med **`bankAccountNumber`**, **`currency` {id:1}**, flags (se **pinned conclusions** / **2026-03-22**) *før* **:invoice**. For **andre** varianter av sitatet (uten bank): **tenant/setup** — ikke spam **:invoice**. **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT.

### 2026-03-19 — Ledger voucher field names + GET voucher dates + 403 token

- **Symptom**: Wrong posting keys; **GET /ledger/voucher** without range; **403** invalid/expired session mid-submission.
- **Type**: Deterministic (JSON) + **infrastructure** (403).
- **Conclusion**:
  - **POST /ledger/voucher**: **Superseded** — correct posting field is **`amountGross`** (**positive = debit**, **negative = credit**); not `debit`/`credit`/`debitAmount`/`creditAmount`. See newer log entry same date.
  - **GET /ledger/voucher**: requires **`dateFrom`** and **`dateTo`**; **`dateTo`** must be **strictly after** **`dateFrom`** — **concluded.**
  - **403** mid-session (**invalid or expired token**): **competition proxy / session** — **log only** (refresh `session_token`; not an agent logic bug). Agent should **still run remaining planned calls** — see newer log entry *HTTP 403 mid-session: do not stop; continue planned calls*.
- **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + tools.

### 2026-03-19 — Invoice payment once (incl. VAT) + dimension/ledger task gap

- **Symptom**: Double **PUT /invoice/{id}/:payment** (e.g. ex-VAT + VAT split); agent **end_turn** with no tools on dimension/ledger-style tasks (**0/13** checks).
- **Type**: Deterministic (payment math) + agent behaviour.
- **Conclusion**:
  - **PUT** `/invoice/:payment`: correct — use **query params**; **`paidAmount`** = **`amountExcludingVat × 1.25`** when VAT is **25%** — **one call only** — **concluded.**
  - **Custom dimensions + ledger entries**: **Superseded** — use **`dimensionName`** / **`displayName`** as above; on **posting** lines use **`accountingDimensionValues: [{"id": Z}]`** (not **`freeAccountingDimension1`** on **`/postings`**) — see **pinned conclusions** and [tripletex.md](tripletex.md).
- **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + tools.

### 2026-03-19 — Project :invoice 404 + timesheet entry

- **Symptom**: **404** on **PUT /project/{id}/:invoice**; timesheet tasks need stable POST body.
- **Type**: Deterministic (API surface).
- **Conclusion**:
  - PUT **`/project/.../:invoice`** — **not** in sandbox OpenAPI (**404**). Invoice from project → **POST /order** with **`project: {id}`** + **PUT /order/{id}/:invoice** — **concluded** ([openapi](https://kkpqfuj-amager.tripletex.dev/v2/openapi.json): Order has **`project`**; only **`/order/{id}/:invoice`**).
  - **POST /timesheet/entry** works with **`project.id`**, **`activity.id`**, **`employee.id`**, **`date`**, **`hours`** — **concluded.**
- **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + tools.

### 2026-03-19 — GET /invoice fields + invoice payment method (Swagger)

- **Symptom**: Invalid **`fields`** on invoice list; agent trial-and-error on payment (POST guesses).
- **Type**: Deterministic.
- **Conclusion**:
  - GET /invoice (list): only use confirmed **`fields`** **`id`**, **`invoiceNumber`**, **`invoiceDate`**, **`amountExcludingVat`** — **concluded.** Do not request **`isPaid`**, **`dueDate`**, **`amountIncludingVat`**, **`paid`**.
  - Invoice payment: **PUT** **`/invoice/{id}/:payment`** with query **`paymentDate`**, **`paymentTypeId`**, **`paidAmount`** (optional **`paidAmountCurrency`**) — **concluded** per [sandbox OpenAPI](https://kkpqfuj-amager.tripletex.dev/v2/openapi.json) (`InvoicePayment_payment`). **Not POST.**
- **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + tools.

### 2026-03-19 — Order + invoice-from-order required dates (live run)

- **Symptom**: **422** on **POST /order** — `deliveryDate` “Kan ikke være null”. **422** on **PUT /order/{id}/:invoice** — `invoiceDate` “Kan ikke være null”.
- **Type**: Deterministic (API contract).
- **Conclusion**: Always send **`deliveryDate`** on **POST /order** (default = **orderDate**). Always send **`invoiceDate`** (and **`invoiceDueDate`** if needed) for **`:invoice`**. **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + tools.
- **Note**: Separate **422** about invoice creation blocked until company has registered something (truncated Norwegian) = **sandbox/company settings**, not fixed by adding fields alone.

### 2026-03-19 — POST /project missing startDate → 422

- **Symptom**: **422** on **POST /project** when **`startDate`** omitted.
- **Type**: Deterministic (API contract).
- **Conclusion**: POST /project: **`startDate`** is required — use **today** (current date `YYYY-MM-DD`) if not specified in prompt — **concluded.** **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + `tripletex_post`.

### 2026-03-19 — POST /employee required fields & employment split

- **Symptom**: **422** or failed create when `userType` / `department` missing, or when nesting employment on `/employee`.
- **Type**: Deterministic (API contract).
- **Conclusion**:
  - POST /employee: requires **`userType="STANDARD"`** and **`department.id`** — **concluded.**
  - POST /employee: **`employmentDetails`** field does not exist — use **POST `/employee/employment`** separately — **concluded.**
  - POST /employee/employment: use for **`startDate`**, **`isMainEmployer`**, **`taxDeductionCode`** — **concluded.**
- **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + `tripletex_post`.

### 2026-03-19 — POST /product used `price` instead of `priceExcludingVatCurrency`

- **Symptom**: **422** or check failure on product create.
- **Type**: Deterministic (wrong JSON key).
- **Conclusion**: POST /product: price field is **`priceExcludingVatCurrency`** not **`price`** — **concluded.** **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + `tripletex_post`.

### 2026-03-19 — POST /customer missing email despite prompt

- **Symptom**: Automated checks fail after supplier/customer creation; body had name, isSupplier, organizationNumber but no **email** though task included it.
- **Type**: Deterministic (agent omission).
- **Conclusion**: Always map prompt-stated **email** (any language) into **`email`** on POST /customer. **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + `tripletex_post`.

### 2026-03-19 — GET /invoice without date range → 422

- **Symptom**: `422` when listing/searching invoices without query params.
- **Type**: Deterministic (API contract).
- **Conclusion**: **`invoiceDateFrom`** + **`invoiceDateTo`** required on collection **`GET /invoice`**. **→** [tripletex.md](tripletex.md), `agent.py` SYSTEM_PROMPT + `tripletex_get` tool text.

### 2026-03-19 — Tripletex sandbox probe with invalid token

- **Symptom**: `401 Unauthorized` on `/v2/employee` and `/v2/token/session/>whoAmI`.
- **Type**: Expected with dummy token (deterministic).
- **Conclusion**: Auth format is working; need real `session_token`. **→** documented in [tripletex.md](tripletex.md) (Basic auth `0` + session token).

### 2026-03-19 — Local `/solve` without `ANTHROPIC_API_KEY`

- **Symptom**: Behaviour depends on environment; full agent loop requires Claude.
- **Type**: Configuration / infrastructure until keys present.
- **Conclusion**: None — ensure `ANTHROPIC_API_KEY` for E2E agent tests.

### 2026-03-19 — Uvicorn port already in use

- **Symptom**: `bind … address already in use`.
- **Type**: Infrastructure / operator error.
- **Conclusion**: None — kill prior `uvicorn` or change `PORT`.
