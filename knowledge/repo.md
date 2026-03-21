# Repository — domain & procedural

## Domain

- Monorepo **`nmai2026/`** with three **independent** competition tracks: `astar/`, `tripletex/`, `norgesgruppen/`.
- Target runtime: **Python 3.12** (see each track’s `Dockerfile` / local env).

## Procedural

### Layout

```
nmai2026/
├── knowledge/          ← this knowledge base
├── astar/
├── tripletex/
└── norgesgruppen/
```

### Tripletex — local run (summary)

- Install: `cd tripletex && pip install -r requirements.txt`
- Server: `uvicorn agent:app --host 0.0.0.0 --port 8080` (or `python agent.py`)
- Env: `ANTHROPIC_API_KEY` (Claude); optional `API_KEY` + `Authorization: Bearer …` on `/solve`
- Sandbox smoke (no LLM): `TRIPLETEX_SESSION_TOKEN`, optional `TRIPLETEX_BASE_URL` → `python smoke_sandbox.py`

### Secrets

- Do not commit tokens. Use environment variables or local-only config ignored by git.
