# NM i AI 2026 — workspace

Monorepo layout for the three independent tracks of [NM i AI 2026](https://nmiai2026.no/).

## Requirements

- **Python 3.12** for all Python code in this repository.

## Knowledge base

Design notes, API contracts, runbooks, and an error log live in **`knowledge/`**. Start from [`knowledge/INDEX.md`](knowledge/INDEX.md).

## Layout

| Path | Purpose |
|------|---------|
| `knowledge/` | INDEX + per-track notes + `ERRORS.md` |
| `astar/` | Astar Island track — terrain prediction client |
| `tripletex/` | Tripletex track — agent + container packaging |
| `norgesgruppen/` | NorgesGruppen track — solutions and assets (reserved) |

## Getting started

Each track may add its own `requirements.txt` or packaging; see subfolders. The Tripletex track includes a `Dockerfile` for submission-style packaging.
