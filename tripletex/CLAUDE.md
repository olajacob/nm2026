# Tripletex agent — Claude Code handoff

**Sannhetskilde for oppførsel:** `agent.py` (`SYSTEM_PROMPT`, `execute_tool`, sanitizers, voucher-routing).  
**Strukturert minne (last kun det du trenger):** [`../knowledge/INDEX.md`](../knowledge/INDEX.md)

| Trenger du … | Åpne |
|--------------|------|
| API-422, supplierInvoice 500, GET-fields, voucher-MVA | [`../knowledge/tripletex/api-quirks.md`](../knowledge/tripletex/api-quirks.md) |
| Oppgaver 01–30 / mønstre | [`../knowledge/tripletex/task-registry.md`](../knowledge/tripletex/task-registry.md) |
| Effektivitet / proxy / logging | [`../knowledge/tripletex/scoring.md`](../knowledge/tripletex/scoring.md) |
| Lange flyter (bank CSV, ordre→faktura) | [`../knowledge/tripletex.md`](../knowledge/tripletex.md) |
| Gjentatte feil | [`../knowledge/ERRORS.md`](../knowledge/ERRORS.md) |

---

## Kritiske konvensjoner

1. **Endringer i flyt** → oppdater **`agent.py`** først; deretter ev. **`knowledge/tripletex/api-quirks.md`** (kort sannhet) og **`tripletex.md`** (narrativ).
2. **`POST /supplierInvoice`:** NM-sandbox returnerer ofte **HTTP 500 / code 1000** — se **api-quirks.md** for kanonisk body, én retry med **`invoiceDueDate`**, og **voucher-fallback** (ingen duplikat-POST samme faktura+leverandør). **A′** leverandør: **`isCustomer`**-quirk → **GET/PUT** etter **`POST /customer`**.
3. **`tripletex_post_voucher`:** aldri **bankkonto (1920 …)** på linjer (med mindre sandbox-env); balanse **Σ amountGross = 0**; inngående MVA: foretrekk **netto + 2710 + 2740 −TTC** når **vatType + 2740** gir **422**.
4. **Faktura liste:** **`invoiceDateFrom`/`To`** alltid; **`invoiceDueDate`** i **`fields`**, ikke **`dueDate`**.
5. **Innbetaling:** **`paidAmount`** fra **`GET /invoice/{id}`** **`amountOutstanding`** — ikke ren **FCY×kurs** fra tekst.
6. **Test:** `cd tripletex && python3 test_sandbox.py --local-only` etter endringer i agenten.
7. **`task_id`** på **`POST /solve`** for sporbar **`logs/last_solve.log`** og grader-korrelasjon.

---

## Server / løsning

- **`server.py`**: **`POST /solve`**, valfritt **`API_KEY`**, logger til **`logs/last_solve.log`** + arkiv.
- **`data.json`**: lag snapshot av leaderboard/tasks når bruker ber om sync (ikke hver agentendring).

Detaljert leverandørfaktura-tabell, JSON-eksempler og punktliste som tidligere lå her finnes nå i **`knowledge/tripletex/api-quirks.md`** (kompakt) og **`knowledge/tripletex.md`** (utfyllende).
