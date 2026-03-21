# Tripletex-agent (NM i AI 2026)

## Kjøre agenten

```bash
cd tripletex
python agent.py
# eller: uvicorn agent:app --host 0.0.0.0 --port 8080
```

Standardport: `8080` (overstyr med `PORT`).

## Utvikler-dashboard

Dashboard leser `data.json` (leaderboard, tasks, notater). Kjør **egen prosess** (standard port 9999):

```bash
python server.py
```

Port overstyres med `DASHBOARD_PORT` (unngår kollisjon med `PORT` for agenten).

Oppdater `data.json` manuelt når du vil reflektere ny status; siden auto-refresher hvert 10. sekund.
