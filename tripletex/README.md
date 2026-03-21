# Tripletex-agent (NM i AI 2026)

## Dokumentasjon for assistenter

- **[CLAUDE.md](CLAUDE.md)** — detaljert handoff om bl.a. **`POST /supplierInvoice`** (påkrevde felter, 500-retry, tenant-quirks). Speiler og utdyper `SYSTEM_PROMPT` i `agent.py`.

## Kjøre agenten

```bash
cd tripletex
python agent.py
# eller: uvicorn agent:app --host 0.0.0.0 --port 8080
```

Standardport: `8080` (overstyr med `PORT`).

**Tripletex API (NM sandbox):** base-URL er `https://kkpqfuj-amager.tripletex.dev/v2` — samme verdi som `tripletex_credentials.base_url` i `/solve`. Kall bruker stier som `/company`, `/customer`, … (full URL = base + sti).

**Første gang:** I Tripletex **Web UI** for tenanten: velg **Forgot password** med påloggings-e-posten du har fått, og sett et passord.

**API-auth:** **HTTP Basic** med brukernavn **`0`** og passord **`<session_token>`** (hele base64-strengen fra `/solve` — ikke bare UUID-en inni JSON-et).

**Ping:** `GET /company` med query kan gi **405** på noen tenants; bruk f.eks.  
`curl -s -u "0:$TOKEN" "$BASE/token/session/%3EwhoAmI" | python3 -m json.tool`.

**Manuell test — opprett leverandør:**

```bash
TOKEN="din_base64_token"   # session_token fra plattformen
BASE="https://kkpqfuj-amager.tripletex.dev/v2"

curl -s -u "0:$TOKEN" -X POST "$BASE/customer" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test Leverandør AS","isSupplier":true,"isCustomer":false,"organizationNumber":"999999999"}' \
  | python3 -m json.tool
```

Etter **POST** kan tenant fortsatt vise `isCustomer: true` — da **GET** `/customer/{id}?fields=id,isCustomer,isSupplier` og ev. **PUT** `{"isCustomer":false}` (se `SYSTEM_PROMPT` / **CLAUDE.md**).

**Manuell test — `POST /supplierInvoice`:** Bruk **`value.id`** fra leverandør-**POST** (eller fra **GET**) som `SUPPLIER_ID`. Ved **HTTP 500** (code 1000): prøv samme body pluss **`invoiceDueDate`** (se **SYSTEM_PROMPT**).

```bash
TOKEN="din_base64_token"
BASE="https://kkpqfuj-amager.tripletex.dev/v2"
SUPPLIER_ID=12345678   # erstatt med faktisk kunde-/leverandør-id

curl -s -u "0:$TOKEN" -X POST "$BASE/supplierInvoice" \
  -H "Content-Type: application/json" \
  -d "{
    \"invoiceNumber\": \"TEST-001\",
    \"invoiceDate\": \"2026-03-21\",
    \"supplier\": {\"id\": $SUPPLIER_ID},
    \"amountCurrency\": 1000,
    \"currency\": {\"id\": 1}
  }" | python3 -m json.tool
```

**Kopi-lim — leverandør + `supplierInvoice` i én økt** (bytt **`TOKEN`**; bruk **unikt** `organizationNumber` hvis du får 422 duplikat):

```bash
TOKEN="din_base64_token"
BASE="https://kkpqfuj-amager.tripletex.dev/v2"

CUST_JSON=$(curl -s -u "0:$TOKEN" -X POST "$BASE/customer" \
  -H "Content-Type: application/json" \
  -d '{"name":"Sandbox leverandør","isSupplier":true,"isCustomer":false,"organizationNumber":"999999991"}')
echo "$CUST_JSON" | python3 -m json.tool
SUPPLIER_ID=$(echo "$CUST_JSON" | python3 -c "import sys,json; v=json.load(sys.stdin).get('value') or {}; print(v.get('id') or '')")
test -n "$SUPPLIER_ID" || { echo "Mangler value.id fra POST /customer"; exit 1; }

curl -s -u "0:$TOKEN" -X POST "$BASE/supplierInvoice" \
  -H "Content-Type: application/json" \
  -d "{
    \"invoiceNumber\": \"TEST-001\",
    \"invoiceDate\": \"$(date +%F)\",
    \"supplier\": {\"id\": $SUPPLIER_ID},
    \"amountCurrency\": 1000,
    \"currency\": {\"id\": 1}
  }" | python3 -m json.tool
```

Ved **500 / code 1000** på siste kall: legg til **`invoiceDueDate`** (f.eks. 14 dager etter `invoiceDate`) og kjør **`POST /supplierInvoice`** på nytt.

## Testflyt før nye runs

1. **Tripletex-token:** `export TRIPLETEX_SESSION_TOKEN='…'` (hele base64-strengen). Valgfritt: `export TRIPLETEX_BASE_URL=…` hvis ikke standard sandbox.
2. **Rask sjekk:** `python smoke_sandbox.py` — kun `whoAmI`.
3. **Bredere sjekk:** `python test_sandbox.py` — `whoAmI`, lesbar konto **1920**, liten `customer`-liste. Valgfritt med kjørende agent:  
   `python test_sandbox.py --health-url http://127.0.0.1:8080`
4. **Ende-til-ende (koster LLM):** Fyll inn `session_token` i `examples/solve_smoke.json`, start agenten, deretter  
   `curl -s -X POST http://127.0.0.1:8080/solve -H "Content-Type: application/json" -d @examples/solve_smoke.json`  
   (legg til `Authorization: Bearer …` hvis `API_KEY` er satt på serveren).

Først når steg 2–3 er grønne (og ev. 4 ved behov), kjør «ekte» konkurranseruns mot sandbox.

## Utvikler-dashboard

Dashboard leser `data.json` (leaderboard, tasks, notater). Kjør **egen prosess** (standard port 9999):

```bash
python server.py
```

Port overstyres med `DASHBOARD_PORT` (unngår kollisjon med `PORT` for agenten).

Oppdater `data.json` manuelt når du vil reflektere ny status; siden auto-refresher hvert 10. sekund.
