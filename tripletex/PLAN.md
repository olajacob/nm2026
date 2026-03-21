# Plan — Tripletex-agent (tilfeldige oppgaver fra server)

Du kan ikke velge task; alt som teller er **robust bredde**, **færre 4xx**, og **sporbarhet**.

## 1. Bred agent-dekning

Status: leverandør-voucher + `customer` fra **supplier**-part (pkt 1) på plass.

- **SYSTEM_PROMPT + verktøybeskrivelser:** tydelige mønstre per oppgavetype (leverandør, kundefaktura/FCY, kreditnota, bilag, prosjekt/timer, reise, lønn, ledger-analyse) uten å gjette API-felter.
- **Runtime:** sanitizers, voucher-hjelpere, `_toolNote` etter kjente feil (500/1000, ugyldige `fields`, osv.).
- **Leverandør → bilag:** når `POST /supplierInvoice` feiler, skal voucher-fallback bruke korrekt konto + MVA; tenant kan kreve **samme kontakt-id som `customer` på debetlinjer** selv om kredit har `supplier` — se `_supplier_party_id_from_postings` + merge i `post_voucher_two_step`.

## 2. Færre dyre feil (pågår)

- **Voucher:** etter at inline **POST** feiler, prøv **hybrid** — **én** første linje i første **POST**, resten via **`/ledger/voucher/{id}/postings`** — *før* tomt skall (som ofte gir **422** *uten posteringer* på NM-tenant).
- **GET-cache (per `/solve`):** statiske stier — **`/ledger/account`**, **`/invoice/paymentType`**, **`/travelExpense/paymentType`**, **`/salary/type`**, **`/ledger/vatType`** — reduserer duplikat-kall. **Invalideres** ved vellykket **`PUT /ledger/account/{id}`**. Slå av med **`TRIPLETEX_GET_CACHE=0`**.
- **`PUT /customer/{id}` dedupe:** identisk body som allerede ga **200** i samme **`/solve`** → returnerer cachet JSON uten ny HTTP (stopper leverandør-**PUT**-løkker).
- **`GET /ledger/account` uten `number`:** konsoll-advarsel + **`_toolNote`** — skal styre modellen bort fra full kontoplan-paginering.
- **Purregebyr / 3400-quirk:** når **`GET …?number=3400`** returnerer **`name`** med **tilskudd**/**offentlig**, legges **`_toolNote`** (+ konsoll) — unngå feil kreditinntekt uten ekstra HTTP.
- Minimer fortsatt ugyldige **`fields`** (prompt + sanitizers).
- Modellen: **`end_turn`** bare når oppgaven er levert (prompt).

## 3. Sporbarhet

- Klient/server: videresend **`task_id` / `X-Task-Id` / `TASK_ID` / `NM_TASK_ID`** til `/solve` når konkurransen sender det, så `last_solve.log` og analyse matcher riktig oppgave.
- Fallback for lokale kjøringer: env **`TRIPLETEX_DEFAULT_TASK_ID`** (f.eks. `11`) når ingen av over er satt — se `examples/solve_trace_task_11.json`.

## 4. Andre spor (totalscore)

- **NorgesGruppen / Astar:** egne leveranser — vurder ROI når Tripletex-endringer gir avtagende gevinst.

---

**Rekkefølge:** 1 → målinger i logg → 2 → 3 → 4. Oppdater denne filen når noe er «ferdig nok».
