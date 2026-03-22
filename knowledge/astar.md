# Astar Island — domain & procedural

## Domain

- **Task**: predict Norse civilisation **terrain** after a **50-year** simulation.
- **Output shape**: **40×40** grid, **6** terrain classes → per-cell class probabilities (W×H×6).
- **Client stub**: `astar/astar_client.py` — replace placeholder uniform prediction with model / simulator integration once the competition API is wired.

## Procedural

- Run: `python nmai2026/astar/astar_client.py` (needs `ASTAR_TOKEN` in env / `.env`).
- **Strategy / algorithm / budget:** [astar/strategy.md](astar/strategy.md) — this track is **simulation + prediction**, not graph A*.
- Competition server contract: follow official Astar / NM i AI 2026 materials when published.
