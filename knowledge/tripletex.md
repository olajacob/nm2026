# Tripletex track — domain & procedural

> **Note:** Some sessions say `TRIPLETEX.md`; on case-insensitive volumes that is the same path as this file.

## Domain

### Agent surface

- **POST `/solve`** body: `prompt`, `files` (optional attachments), `tripletex_credentials`: `{ base_url, session_token }`.
- Response: always **`{"status": "completed"}`** (including partial failure — competition-oriented).
- Optional **`API_KEY`**: if set, `/solve` expects `Authorization: Bearer <API_KEY>`.
- **File logging (mirrors agent `stdout` diagnostics):** each **`/solve`** writes the same lines as the console to **`tripletex/logs/last_solve.log`** (always overwritten — handy for Cursor/AI review) and **`tripletex/logs/solve_<UTC>_<task-label>.log`** (archive). Override directory with **`AGENT_LOG_DIR`**; disable with **`AGENT_LOG_DISABLE=1`**.
- **CSV / plain-text attachments:** `run_agent` decodes **`content_base64`** ( **`text/csv`**, **`text/plain`**, **`application/csv`**, or **`.csv`** filename) and appends **`=== File: {filename} ===`** + raw text to the user message. Decode failures append **`=== File: … (decode error: …) ===`**. Cap: **`ATTACHED_TEXT_MAX_CHARS`** (default **120000**). Without this, bank CSVs were invisible and the model could **`end_turn`** with no API calls.

### Bank reconciliation (bankavstemming)

Mirror of **`agent.py` `SYSTEM_PROMPT`** — CSV from **`=== File:`** blocks → Tripletex payments.

1. Parse CSV: **date**, **description**, **amount**, **KID** / reference (column names vary).
2. **GET `/invoice`**: **`invoiceDateFrom`**, **`invoiceDateTo`** (e.g. `2000-01-01` … `2099-12-31`), **`fields=id,invoiceNumber,amountExcludingVat,kid,customer`**; add **`kid`** query if CSV has KID. If **`fields`** **400**s, shrink **`fields`** and **GET `/invoice/{id}`** for details / outstanding.
3. **GET `/invoice/paymentType?fields=id`** → **`paymentTypeId`** (first **`values[].id`** if task silent). **Never** **`fields=name`**.
4. Match rows to invoices by **KID**, **amount** (bank cash **incl. VAT** vs invoice math), **invoice number** in text.
5. Each innbetaling: **`GET /invoice/{id}`** when needed for **`amountOutstanding`** / **`amountCurrencyOutstanding`**; **`tripletex_put_action`** **PUT `/invoice/{id}/:payment`** with **`paymentDate`**, **`paymentTypeId`**, **`paidAmount`** (Tripletex outstanding for full pay — **not** recomputed **FCY × rate**).
6. **Delbetaling:** **`paidAmount`** = CSV row amount; **one PUT per bank line** — no duplicate same amount, no ex-VAT + VAT split for one line.
7. **Utbetalinger:** **GET `/supplierInvoice`** (**`invoiceDateFrom`** + **`invoiceDateTo`** required), match, then **`tripletex_post`** **POST `/supplierInvoice/{invoiceId}/:addPayment`** with **`{}`** body + query **`params`** (OpenAPI: **`paymentType`**, **`amount`**, **`paymentDate`**, **`partialPayment`**, …) — **not** customer **PUT `/invoice/.../:payment`**.

### GET /invoice (list)

- Listing **`GET /invoice`** requires **`invoiceDateFrom`** and **`invoiceDateTo`** (`YYYY-MM-DD`) **even with** **`customerId`** / pagination. Example: `invoiceDateFrom=2000-01-01&invoiceDateTo=2099-12-31&customerId=X&fields=id,invoiceNumber,invoiceDate,amountExcludingVat`.
- **Do not** use **`fields=`** for **`isPaid`**, **`dueDate`**, **`amountIncludingVat`**, or **`paid`** on this list — not valid for the list DTO in live testing; stick to **id**, **invoiceNumber**, **invoiceDate**, **amountExcludingVat**.

### Tripletex API v2 auth

- **HTTP Basic**: username **`0`**, password = **session token** (employee’s company; non-accountant flow).
- **`base_url`** should include API prefix, e.g. `https://{tenant}.tripletex.dev/v2` — paths are appended: `/employee`, `/order/123/:invoice`, etc.
- Path segments like **`/:invoice`** are **actions** (not generic CRUD body shapes).

### POST /department

- **POST `/department`** with body **`{ "name": "..." }`** (confirmed).
- **Multiple** departments: **one POST per name** — separate requests, not one body with an array.

### HTTP 403 mid-session

- **403** / “Invalid or expired token” from the competition **proxy** is **infrastructure**: token expired mid-run. **Not** fixed by changing JSON in the agent.
- **Policy**: do **not** stop the tool loop after **one** 403 — finish planned work (e.g. all department POSTs); later calls may still succeed. **Fresh** `session_token` needed for a clean submission; if this happens often, flag **organisers** (Slack).

### POST /employee

1. **GET `/department`** with e.g. `?fields=id,name` to pick **`department.id`**.
2. **POST `/employee`**: **`firstName`**, **`lastName`**, **`email`**, optional **`dateOfBirth`** (`YYYY-MM-DD`), required **`userType`**: `"STANDARD"`, required **`department: {id}`**. There is **no** `employmentDetails` on this body — do not nest startDate here.
3. **Employment:** **POST `/employee/employment`** with **only** **`employee: {id}`** and **`startDate`** first — on many tenants, **`division`**, **`isMainEmployer`**, or **`taxDeductionCode`** on that **POST** returns **404**. After **200**, **GET** the employment row and **PUT** **`division`** / other fields as needed (`agent.py` **retries once** with minimal body if **POST** returns **404** with a non-minimal body).

### POST /customer (customer or supplier)

- Body shape: `name`, **`email`** and **`organizationNumber`** whenever the prompt mentions them, **`phoneNumber`** if mentioned.
- **Flags:** **Supplier-only** → **`isSupplier: true`**, **`isCustomer: false`**. If **`POST /customer`** still returns **`isCustomer: true`**, **`PUT /customer/{id}`** **`{isCustomer: false}`** (tenant often ignores **`false`** on create — **→** `agent.py` **SYSTEM_PROMPT**). **Customer-only** → **`isCustomer: true`**, **`isSupplier: false`** unless the prompt also names a supplier role. **Both** only when the task **explicitly** requires customer **and** supplier.
- **CRITICAL:** Never omit **`email`** or **`organizationNumber`** when they appear in the prompt (checks fail).

### Register supplier invoice (leverandørfaktura)

- **Task pattern:** register a **received** supplier invoice (amount **inkl. MVA** / **TTC**, invoice number, cost account such as **7300**). **Do not** stop at **`POST /customer`** — you must **`POST /supplierInvoice`** (and **`PUT …/:approve`** if the task asks to approve / attest / bokføre in that sense).
- **Flow:** **`GET /customer?organizationNumber=…`** → reuse **`values[].id`** or **`POST /customer`** with **`isSupplier: true`**, **`isCustomer: false`**. **One** **`GET /ledger/account?number=…`** per **stated** GL code — avoid scanning the chart. **`tripletex_post`** **`POST /supplierInvoice`**: **`invoiceDate`** and **`supplier: {id}`** are required; other body fields (**`invoiceNumber`**, **`amountCurrency`**, **`currency`**, **`comment`** / **`invoiceComment`**, **orderLines** / lines if Swagger requires) follow **[v2-docs](https://tripletex.no/v2-docs/)**. **Do not** use **`/ledger/voucher`** for this flow unless **`/supplierInvoice`** create is not available on the tenant.
- **Approve:** **`tripletex_put_action`** **`PUT /supplierInvoice/{invoiceId}/:approve`** (OpenAPI is **PUT**, optional **`comment`** query). There is no standard **`/:book`** path — use **`:approve`**.

### POST /product

- **Before POST:** **`GET /product`** with query **`name`** (substring match) and optionally **`productNumber`**; **`fields=id,name,number`**. If the product already exists, **reuse** **`id`** — **no** **`POST /product`**.
- Body: **`name`**, optional **`number`** (if task gives a product number), **`priceExcludingVatCurrency`** (never `price` — **422**), **`vatType: {id}`** — **outgoing** sales code for the product **default**; **not** **incoming** / **fradrag** (**id 1**). If the **invoice** has **different VAT % per line**, set **`vatType: {id}`** on **each** **`orderLines[]`** row as well (product default alone can be wrong). **Norwegian outgoing shortcut** (competition): **25% → 3**, **15% → 31**, **12% → 32**, **0% → 6** — avoid a full **`GET /ledger/vatType`** unless the rate is unusual. **`execute_tool`** maps optional **`vatRatePercent`** / **`vatPercent`** on a line to **`vatType`** for those four rates. **Travel** **`/travelExpense/cost`** has **other** **vatType** rules — see below.
- If **422** **`Produktnummeret X er i bruk`** or **`Produktnavnet X er allerede registrert`**: **GET /product** and **reuse** existing **`id`** — **do not** waste calls on new POSTs with tweaked names/numbers. Only if GET finds no row and the error was duplicate **number**, retry **POST** **without** **`number`** — **→** **ERRORS.md**, `agent.py`.

### Company bank account (before **customer invoices** only)

- Use **1920** setup **only** when the task leads to **`PUT /order/{id}/:invoice`** (or explicit faktura-bank / outgoing-invoice setup). **Skip** for **project-only** work (**Festpreis** / **`fixedprice`**, PM, rates), **timesheet**, **travel**, **payroll**, etc. — extra **`GET/PUT` 1920** hurts efficiency.
- **`Company`** has **no** kontonummer in the API. **Account 1920** (competition invoice bank — **`isInvoiceAccount: true`**) **always exists**; set **`bankAccountNumber`** there. **Do not** **`POST`** **1921**.

**Step 1 — account 1920**

- **`GET /ledger/account`** with **`number=1920`**, **`fields=id,number,bankAccountNumber`** — note **`id`**.

**Step 2 — set kontonummer**

- **`PUT /ledger/account/{id}`** with body **only**:

```json
{"bankAccountNumber": "86011117947"}
```

- **11 digits**, valid Norwegian; **86011117947** **confirmed**. **Do not** use **`1503.40.12345`**, **`12345678901`**, **`15034012345`**. **Only one** ledger row may use a given **`bankAccountNumber`** — no reuse / duplicates across accounts.

### Invoice creation (correct flow)

0. **Invoice tasks:** **MANDATORY** bank setup (**GET** **`number=1920`** → **`PUT`** **`{bankAccountNumber}`** only — see above), then:
1. **POST `/customer`** → `customer_id` (or GET if reusing existing).
2. **`GET /product`** (name / productNumber) → **`product_id`** if found; else **`POST /product`** (`priceExcludingVatCurrency`, etc.).
3. **POST `/order`**: `customer`, **`orderDate`**, **`deliveryDate`** (required — **422** if missing; usually same as `orderDate`), **`orderLines`** (`product`, `count`, **`unitPriceExcludingVatCurrency`**, **`vatType`** per line when the task states VAT per line) → `order_id`.
4. **PUT `/order/{order_id}/:invoice`**: must include **`invoiceDate`** (and **`invoiceDueDate`** if the API requires it) via query **params** or JSON **body** — **422** if `invoiceDate` is null. Use **`tripletex_put_action`**.  
   If **422** mentions **bankkontonummer**, rerun **GET** **`number=1920`** → **`PUT`** **`bankAccountNumber`** as above, then retry **`:invoice`**. Other **«Faktura kan ikke opprettes…»** causes may still be sandbox/setup (see **ERRORS.md**).
5. **Send invoice** (when the task says **send** / **e-post** / **enviar** / **envie** / **envoyer** / **senden** / …): **`PUT /order/.../:invoice` does not send** — call **`tripletex_put_action`** **`PUT /invoice/{id}/:send`** with query **`sendType`** (**required**): **`EMAIL`**, **`EHF`**, **`MANUAL`**, **`PAPER`**, … (OpenAPI). Use **`id`** from the **`:invoice`** response **`value.id`**. For **`EMAIL`**, optional **`overrideEmailAddress`**; ensure customer **`email`** / **`invoiceEmail`** exists or set in task.

### POST /project

- **Required:** **`startDate`** (`YYYY-MM-DD`) — **422** if omitted; if the prompt has no date, use **today’s** date in that format.
- Typical body: **`name`**, **`customer: {id}`**, **`projectManager: {id}`** (lookup with **`GET /employee`** and email query if the task names a person), **`startDate`**, optional **`endDate`**.

### Timesheet entry

- Resolve ids: **`GET /employee?email=…`**, **`GET /project?name=…`**, **`GET /activity?name=…`** (with **`fields`** as needed).
- **POST `/timesheet/entry`**: **`project`**, **`activity`**, **`employee`**, **`date`** (`YYYY-MM-DD`), **`hours`**.

### Project hourly rate

- **GET `/project/hourlyRates`** (e.g. **`projectId`**) → rate row id; **PUT `/project/hourlyRates/{id}`** with **`fixedRate`** (add required query filters per Swagger if needed).

### Invoice from a project

- **There is no** **`PUT /project/{id}/:invoice`** in Tripletex v2 OpenAPI (**404**). Invoicing uses **customer orders**: **`POST /order`** may include **`project: {id}`**, plus **`customer`**, **`orderDate`**, **`deliveryDate`**, **`orderLines`**, then **PUT `/order/{orderId}/:invoice`**. Optionally **POST `/project/orderline`** [BETA].

### Other actions

- **Credit note** (cancel / credit the **sale** — product return, not a bounced transfer): **PUT** `/invoice/{id}/:createCreditNote` (`tripletex_put_action`; params per Swagger).
- **Register payment (customer):** **GET** `/invoice` (date range) → id; **GET `/invoice/{id}`** and use **`amountOutstanding`** / **`amountCurrencyOutstanding`** for **`paidAmount`** on **full** settlement (**foreign-currency** invoice → **`amountCurrencyOutstanding`** = Tripletex NOK rest — **do not** **`FCY × payment exchange rate`**). **`GET /currency`** only for **new** FCY invoices, not payment-only flows. **GET** `/invoice/paymentType?fields=id` → **`paymentTypeId`**; **PUT** `/invoice/{id}/:payment` via **`tripletex_put_action`**. Partial / several innbetalinger → **one PUT per line** with that line’s amount; **never** duplicate or split ex-VAT + VAT.
- **Register payment (supplier / leverandør):** **GET** `/supplierInvoice` (**`invoiceDateFrom`** + **`invoiceDateTo`** required, like **`/invoice`**) → match **utbetaling**; **`tripletex_post`** **POST** `/supplierInvoice/{invoiceId}/:addPayment` with **body** **`{}`** and **params** (**`paymentType`**, **`amount`**, **`paymentDate`**, **`partialPayment`** when needed — OpenAPI).
- **Reverse payment / bank return** (betaling returnert — restore **outstanding** on the **same** invoice): **do not** use **`:createCreditNote`** (that negates the **invoice charge**). **GET** `/invoice/{id}` with **`postings`** (e.g. **`fields=id,invoiceNumber,postings`** or full document) — payment lines are **negative** amounts; read **`voucher.id`** from that posting. **PUT** `/ledger/voucher/{voucherId}/:reverse?date=YYYY-MM-DD` via **`tripletex_put_action`** (`date` required per OpenAPI). Re-check **`GET /invoice/{id}`** for outstanding if needed.

### Payroll (lønn)

- **Create payroll transactions with `POST /salary/transaction`** (not `/salary` or `/payroll`).
- **`dateOfBirth` before employment:** some tenants require **`Employee.dateOfBirth`** before **`POST /employee/employment`**. **`GET /employee/{id}?fields=id,dateOfBirth`** — if **null**, **`PUT /employee/{id}`** with **`dateOfBirth`** from the prompt or **`1990-01-01`** when the task omits it — avoids **422** *«employee.dateOfBirth»* / *«Feltet må fylles ut»*.
- **Employment and `division` (virksomhet):** often needed for **`POST /salary/transaction`**. **`GET /company/divisions`** is often **403** in competition — do **not** use it for lookup.
- **After `POST /employee/employment`:** **`GET /employee/employment/{id}?fields=id,division`**. If **`division`** is null, **`PUT /employee/employment/{id}`** with **`{"division": {"id": 1}}`**; on **403** try **`id` 2**, then **3**; **log** attempts. On **422** *«Virksomheten kan ikke endres»*, **stop** — do **not** try other division ids; **continue** to **`POST /salary/transaction`** unless that fails on **virksomhet** / **arbeidsforhold**.
- **Before `POST /salary/transaction`:** ensure **`dateOfBirth`** / employment as above; **`GET /employee/employment?employeeId={id}&fields=id,startDate,division`**. If no employment, **`POST /employee/employment`** with **only** **`employee`** + **`startDate`** (e.g. **`2026-01-01`** if unspecified), then **GET**+**PUT** for **`division`** / **`taxDeductionCode`** / **`isMainEmployer`** when the tenant allows — **no** auto-**division** on **POST** in **`execute_tool`**. If **`POST /salary/transaction`** returns **«ikke knyttet mot en virksomhet»**, further **`PUT`** **`division`** for that employment may be **blocked** for the rest of the **`/solve`** (see **`agent.py`**).
- Use **`POST /salary/transaction`** with query **`generateTaxDeduction=true`** and body: **`date`** (first day of month), **`year`**, **`month`**, **`payslips[]`** with **`employee`** + **`specifications[]`**. Each specification line with **`amount`** must include **`count`** and **`rate`** (non-null) — e.g. **`count: 1`**, **`rate`** = **`amount`** for fixed monthly lines; **422** *«Kan ikke være null»* if missing. The agent **`execute_tool`** may auto-fill **`count`/`rate`** from **`amount`** when omitted.
- **Before posting**, resolve salary type ids via **`GET /salary/type`**:
  - base salary: type name contains **fastlønn** or **grunnlønn**
  - bonus/additions: type name contains **bonus** or **tillegg**
- **`/salary/payslip`** and **`/salary/compilation`** are read-only for this creation flow.

### Travel expense

- **POST `/travelExpense`**: **`employee`**, **`title`**, and **`travelDetails`** object — put **`departureDate`**, **`returnDate`**, **`destination`**, **`purpose`**, **`departureFrom`**, **`isDayTrip`**, **`isForeignTravel`** inside **`travelDetails`**, not at the top level.
- **POST `/travelExpense/perDiemCompensation`**: only valid when the report is **`type` TRAVEL** (not expense-report flow). Body: **`travelExpense`**, **`location`**, **`count`** (days), **`rate`**, **`amount`** (= count × rate), **`overnightAccommodation`** (e.g. **`"NONE"`**).
- **Cost categories (`/travelExpense/costCategory`)**  
  - **`GET /travelExpense/costCategory`** (list) often returns rows with **`id` only** — **no** usable **name** on the list DTO in live testing.  
  - **`GET /travelExpense/costCategory/{id}`** returns the full **`TravelCostCategory`** (**`displayName`**, **`description`**, linked **`vatType`**, etc.).  
  - **Agent runtime:** a list **`tripletex_get`** automatically **fetches each id** and **merges** details into **`values[]`**; responses are **cached in-process** for the **`/solve`** session (fewer duplicate GETs). The model should still **remember** which **`costCategory.id`** belongs to which line type for all **`POST /travelExpense/cost`** calls in that run.  
  - **Heuristic labels** (Norwegian, match on **displayName** / **description**): **Transport** or **Fly** → flights; **Transport** → taxi/ground; **Overnatting** → accommodation; **Diett** → diett/per-diem-related cost lines when the task fits.
- **POST `/travelExpense/cost`** (per line): **`amountCurrencyIncVat`** (not `amountCurrencyInclVAT`), **`amountNOKInclVAT`**, **`comments`** (not `description`), **`paymentType`** (not `paymentCurrency`), plus **`vatType`**, **`currency`**, **`costCategory`**, **`date`**, **`travelExpense`**. **`GET /travelExpense/paymentType?fields=id`** only — **`name`** is **not** a valid **`fields`** value on **TravelPaymentTypeDTO** (**400**). **`TripletexAPI.get`** normalizes **`fields`** to **`id`** if needed. Resolve **currency** via **`GET /currency`** when needed.  
- **`vatType` on costs:** typically **`{id: 1}`** for domestic **25%** VAT on paid services; **`{id: 0}`** (no VAT) for **per diem** / **diett** lines when applicable — **do not** assume **`1`** on every line. Confirm with **`GET /ledger/vatType`** when unsure; use category **`vatType`** from the enriched category when it matches the line.

### Custom dimensions & ledger

- **POST** **`/ledger/accountingDimensionName`**: body **`{"dimensionName": "…"}`** — **not** **`name`** or **`displayName`** (confirmed).
- **POST** **`/ledger/accountingDimensionValue`**: body **`{"displayName": "…"}`** — **not** **`value`** or **`name`** (confirmed). Use the returned **value** **`id`** as **`Z`** below (not the dimension-name id).
- **Bilag med dimensjon (Task ~06):** bruk **to ulike kontonumre** — aldri debet og kredit på **samme** `account.id`. På **debetlinjen**: **`freeAccountingDimension1: {id: <verdi-id>}`** (OpenAPI); du kan også sende **`accountingDimensionValues`** i verktøyet — **`agent.py`** mapper til **`freeAccountingDimension1`** (inline **POST /ledger/voucher** avviser **`accountingDimensionValues`**). Lokal sjekk: `python tripletex/test_sandbox.py --dimension-voucher`.
- **Manual voucher (bilag) — field names (Swagger [v2-docs](https://tripletex.no/v2-docs/), schema **Voucher**):**
  - The only valid collection property is **`postings`** (**plural**, array of **Posting**). **Not** **`posting`**, **not** **`rows`** on the voucher.
  - **422** *«postings: Kan ikke være null»* → **`postings`** was **omitted** or **null**. **Always** send **`postings`** as an array: **`[]`** for an empty shell, or **`[...]`** with full lines if the tenant accepts one-shot create.
  - Each **Posting** supports **`row`** (line index; typically **1-based** **`1, 2, 3…`**).
- **Voucher create + ledger finalize** (implemented by **`tripletex_post_voucher`** / **`post_voucher_two_step`** in `agent.py` — **always** use this tool for journals; **`tripletex_post`** on **`/ledger/voucher`** is rejected):
  1. **Try one-step first:** **POST `/ledger/voucher?sendToLedger=false`** with **`date`**, **`description`**, and the **full** **`postings`** array in the body. Many tenants require **non-empty** postings on create; an **empty** shell can return **422** *«Et bilag kan ikke registreres uten posteringer»*.
  2. If **422** *uten posteringer* on (1): retry **POST `/ledger/voucher`** with the **same** body but **no** **`sendToLedger`** query.
  3. If **422** **systemgenererte** (or one-step still fails): **fallback** — **POST** **`postings: []`** + **`sendToLedger=false`**, then for each line **POST `/ledger/voucher/{id}/postings`** with one **Posting** object. On **422**, the tool **retries** with **negated** **`amountGross`** once.
  4. To post the voucher to the ledger: **PUT `/ledger/voucher/{id}/:sendToLedger`** — the tool runs this when **`send_to_ledger`** is true (after a successful create).
- **Posting amounts:** **`amountGross`** (or **`amount`** per OpenAPI). **Debit = positive**, **credit = negative**. Do **not** use **`debit`**, **`credit`**, **`debitAmount`**, **`creditAmount`**. Lines must **balance** (sum **zero**).
- **Dimensions on a line:** prefer **`accountingDimensionValues`** on **`/ledger/voucher/.../postings`** — **`freeAccountingDimension1`** may apply on inline **Posting** per OpenAPI; follow tenant behaviour.
- **Import:** **`POST /ledger/voucher/importDocument`** — **multipart** **`file`** (+ optional **`description`**) for scanned documents — **not** a JSON substitute for manual lines.
- **GET `/ledger/account` for bilag:** When the task states **kontonummer** / **GL** / **compte** codes (**1720**, **6010**, …), use **`?number=N&fields=id,number,name`** — **one request per** **N**. **Do not** paginate the **entire** chart (`from`/`count` loops) unless you must search by **name** once — too many list calls **exhaust** the competition **proxy** and yield **403** before **`tripletex_post_voucher`** runs (**→** `agent.py` **SYSTEM_PROMPT**, **ERRORS.md**).
- **Month-end / prepaid (*clôture*, *régularisation*, *vers charges*, *periodisering*):** **Debit** the **expense** account that matches the **task** (not default **5000** salary unless the prompt says salary); **credit** prepayment / **1720**-class accounts. **Only** post vouchers the prompt asks for; match **depreciation expense** to **asset type** (**→** `agent.py` **SYSTEM_PROMPT**).
- Use **GET** **`/ledger/account`**, **`/department`** as needed for account/department ids on lines.
- **`sendToLedger`:** finalize with **PUT `/ledger/voucher/{id}/:sendToLedger`** after lines exist — **not** **`?sendToLedger=true`** on create with **`postings: []`**.
- **GET `/ledger/voucher`**: **`dateFrom`** + **`dateTo`** required; **`dateTo`** strictly after **`dateFrom`**. List **`fields`**: **`number`** (bilag) — **not** **`voucherNumber`** or **`amount`** on **`VoucherDTO`** (**400**). Prefer **`fields=id,date,description,number`** without **`postings`** for light lists; line amounts → **`GET /ledger/voucher/{id}`**. **`execute_tool`** **strips** embedded **`postings`** on list responses (**`postingsCount`** is **`null`** when **postings** were omitted). Paginate with **`from`** when **`fullResultSize`** exceeds **`values`**.
- **Revisjon / audit / «finn og korriger feil»:** do **not** stop at **GET** only — book **correction vouchers** via **`tripletex_post_voucher`** per **`agent.py` SYSTEM_PROMPT** (**Ledger audit / error correction**).
- If **`POST /ledger/voucher/{id}/postings`** returns **404**, re-check the tenant’s **OpenAPI** / Swagger — the bundled `openapi.json` snapshot may omit this sub-resource.
- **OpenAPI:** **`tripletex/openapi.json`**; refresh: `curl -o tripletex/openapi.json '<base_url>/openapi.json'`. Live: [sandbox OpenAPI](https://kkpqfuj-amager.tripletex.dev/v2/openapi.json).

### Agent tools (conceptual)

- **`tripletex_get` / `tripletex_post` / `tripletex_put` / `tripletex_delete`**, **`tripletex_put_action`**, **`tripletex_post_voucher`** (ledger journal: **one-step inline postings** first, **two-step** fallback).

### Scoring

- Correctness + efficiency — **avoid 4xx**.

## Procedural

### Quick local run

From **`nmai2026/`**:

```bash
pip install -r tripletex/requirements.txt
export ANTHROPIC_API_KEY="sk-ant-api03-…"
python tripletex/agent.py
```

- **`PORT`** overrides **8080**. **`POST /solve`** needs `tripletex_credentials`. **`LOG_TOOL_INPUT_CHARS`** / **`LOG_TOOL_RESULT_CHARS`** control console previews.
- **Solve log files:** **`tripletex/logs/last_solve.log`** + per-request **`solve_*_.log`** (see **Agent surface**); env **`AGENT_LOG_DIR`**, **`AGENT_LOG_DISABLE`**.
- **Task identification in logs:** each **`POST /solve`** prints **`TASK / RUN`** at the top. Set **`task_id`** in the JSON body (optional), or HTTP header **`X-Task-Id`**, or environment **`TASK_ID`** / **`NM_TASK_ID`** before starting the server — useful when rerunning NM tasks locally (e.g. «Task 06»).

### Cloudflare quick tunnel

Terminal 1: `python tripletex/agent.py`. Terminal 2: `npx cloudflared tunnel --url http://localhost:8080`. Copy the **`trycloudflare.com`** URL for submission.

**Failures:** imports → `requirements.txt`; **401** → token/base_url; **422** → read body; timeouts → **`ANTHROPIC_API_KEY`**.

- **Smoke:** `tripletex/smoke_sandbox.py`
- **Docker:** `tripletex/Dockerfile`
- **Auth docs:** [Tripletex tokens](https://developer.tripletex.no/docs/documentation/authentication-and-tokens)
