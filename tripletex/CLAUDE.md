# Tripletex-agent — handoff for assistenter (Claude m.fl.)

Dette dokumentet utdyper **`SYSTEM_PROMPT`** i `agent.py` for **leverandørfaktura** (`POST /supplierInvoice`) og relaterte API-er. **Sannhetskilde for agent-atferd er fortsatt `agent.py`** — oppdater begge steder hvis du endrer flyt.

---

## 1. Hvorfor denne guiden finnes

### Observert problem

- **`POST /supplierInvoice`** med kun  
  `invoiceNumber`, `invoiceDate`, `supplier: { id }`  
  ga i live-test **HTTP 500** med Tripletex **code 1000** og ofte **tom `message`**.
- Det oppfører seg som **manglende påkrevd data** / server-side feilhåndtering, ikke en ren **422** med feltnavn.

### Konklusjon i prompten

- **Første vellykkede forsøk** bør inkludere **`amountCurrency`** (totalt **inkl. MVA**) og **`currency`** (typisk NOK → **`{ "id": 1 }`**).
- **Hvis fortsatt 500:** **én retry** med samme body **pluss** **`invoiceDueDate`**.

---

## 2. Primær flyt: registrer mottatt leverandørfaktura

| Steg | Handling |
|------|----------|
| A | **Leverandør:** `GET /customer` (f.eks. `organizationNumber`) med `fields` som inkluderer **`isCustomer`** → ev. **`POST /customer`** med `isSupplier: true`, `isCustomer: false`, + navn/orgnr/e-post fra oppgaven. |
| A′ | **Tenant-quirk:** Etter **`POST`** (eller hvis liste-GET viser `isCustomer: true`): **`GET /customer/{id}?fields=id,isCustomer,isSupplier`** → ved behov **`PUT /customer/{id}`** `{"isCustomer": false}` → valgfritt **nytt `GET`** for å verifisere (viktig for graders). Stol ikke på **`POST`**-respons alene. |
| B | **Kostnadskonto:** **Én** `GET /ledger/account?number=NNNN` for oppgitt konto (f.eks. 7300) — til **oppfølging** etter faktura er opprettet; ikke masse-GET av kontoer. |
| C | **`POST /supplierInvoice`** med standard body (se §3). |
| D | Oppgave krever godkjenning: **`PUT /supplierInvoice/{id}/:approve`** via **`tripletex_put_action`** (ikke `:book` i standard v2). |

**Aldri** `end_turn` etter bare leverandør + ledger-GET uten å ha kjørt **`POST /supplierInvoice`** (og evt. **`:approve`**).

**Ikke** bruk **`tripletex_post_voucher`** / manuell **`/ledger/voucher`** for denne flyten med mindre oppgaven eksplisitt krever det eller API-et ikke tilbyr `/supplierInvoice`.

---

## 3. Kanonisk body for `POST /supplierInvoice`

```json
{
  "invoiceNumber": "INV-…",
  "invoiceDate": "YYYY-MM-DD",
  "supplier": { "id": "<supplier_id>" },
  "amountCurrency": "<beløp_inkl_MVA>",
  "currency": { "id": 1 }
}
```

- **`amountCurrency`:** Bruk beløpet oppgaven gir som **TTC / inkl. MVA / «inklusive»** — mapp til **én** totalsum i selskapets valuta.
- **`currency.id: 1`:** Forventet **NOK** i typisk norsk tenant; ved annen valuta må `id` avklares (OpenAPI / `GET /currency`).

### Ved fortsatt HTTP 500

1. **Retry én gang** med **samme felter** **pluss**  
   `"invoiceDueDate": "YYYY-MM-DD"`  
   (hvis oppgaven ikke sier forfall: f.eks. **14 dager etter** `invoiceDate` som heuristikk).

### Felter å **unngå** i første omgang (kjente 422 / støy)

- **`comment`** på create-body (felt eksisterer ikke / avvist).
- **`account`** på **`orderLines[]`**, og **`orderLines`** som inkluderer **`account`**.

*Merk:* Dette er **ikke** det samme som «aldri `orderLines`» — kun at **problemvarianten med `account` på linjer** er utelukket i prompten.

---

## 4. Andre `supplierInvoice`-endepunkter (kontekst)

| Behov | Verktøy / metode |
|-------|------------------|
| Liste fakturaer | **`GET /supplierInvoice`** krever **`invoiceDateFrom`** og **`invoiceDateTo`** (samme mønster som **`GET /invoice`**). |
| Godkjenning | **`PUT /supplierInvoice/{id}/:approve`** (`tripletex_put_action`). |
| Registrer betaling | **`POST /supplierInvoice/{id}/:addPayment`** med body **`{}`** og **query-params** per OpenAPI (`tripletex_post`) — se `SYSTEM_PROMPT` punkt om utbetalinger/CSV. |

---

## 5. Relasjon til `agent.py`

- Seksjonen **«Register supplier invoice»** i **`SYSTEM_PROMPT`** speiler §2–3 over.
- **`tripletex_post`**-beskrivelsen nevner eksplisitt **`invoiceNumber`**, **`invoiceDate`**, **`supplier`**, **`amountCurrency`**, **`currency`** for `/supplierInvoice`.

Ved endringer: **oppdater `agent.py` og dette dokumentet** så assistenter som leser repo-filer får samme bilde.

---

## 6. Begrensninger

- **Code 1000** med tom melding er **vanskelig å feilsøke** uten tenant-spesifikk logging; retry med **`invoiceDueDate`** er en **heuristikk**.
- Multivaluta-oppgaver kan kreve annet enn **`currency: {id: 1}`** — valider mot oppgave og API.
