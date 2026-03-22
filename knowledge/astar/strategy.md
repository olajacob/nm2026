# Astar Island — strategy (NM i AI 2026)

## What this track actually is

**Astar Island** is **not** classical graph A* pathfinding. The reference client (`nmai2026/astar/astar_client.py`) solves a **stochastic simulation prediction** task:

- **Input:** Full **40×40** initial grid + settlements from `GET /rounds/{id}` (no query cost).
- **Output:** Per seed, a **40×40×6** tensor of **class probabilities** (6 terrain classes), submitted via `POST /submit`.
- **Queries:** `POST /simulate` returns a **viewport** (up to **15×15**) of **one** Monte Carlo roll for that seed — **1 query** each. Typical budget **50** queries per round, **5** seeds.

**Scoring** is based on **KL divergence** (or similar) between your distribution and ground truth across seeds — **not** shortest path length. Heuristics like Manhattan distance or Jump Point Search **do not apply** unless you build an internal planner for *where to point the viewport* (the current client uses **greedy viewport packing**, not A*).

## How the current client works

1. **Priors (`build_prior_tensor`)**  
   Ocean / mountain get strong priors; plains, forest, settlement, port get hand-tuned multinomials; coastal settlements get higher port mass.

2. **Viewport list (`get_dynamic_viewports`)**  
   Marks all non-ocean/non-mountain cells as “dynamic”, scores **15×15** window placements on a coarse grid (**step 5**), **greedy** coverage: pick windows that add uncovered dynamic cells until covered or list exhausted.

3. **Query budget**  
   - Reads **`GET /budget`**: `queries_used`, `queries_max`.  
   - Allocates more queries to **seed 0** (up to **30** if budget allows), remainder split across other seeds.  
   - **Global** `query_log` across seeds; stops when **`len(query_log) >= budget_cap`** where **`budget_cap`** = server **remaining** queries at run start (not only a hardcoded 50).

4. **Seed 0**  
   Repeats the **same ranked viewports** many times (`dynamic_vp * 6`, truncated) to stack **independent** stochastic samples per cell → empirical frequencies.

5. **Seeds 1–4 (improved)**  
   **Rotated** viewport order: each seed starts at a different offset in the ranked list so **limited** budgets are not all spent on the **identical** top-5 windows for every seed → **wider spatial coverage** of the ensemble.

6. **Merge (`finalize_tensor`)**  
   Blends observations with priors; ocean/mountain never overwritten; **PROB_FLOOR** (0.01) on all classes to avoid **KL → ∞** when truth has tiny mass on a class.

## Query budget rules (summary)

| Source        | Rule |
|---------------|------|
| Server        | `queries_max - queries_used` = max queries **this run** |
| Client loop   | Stop when global query count hits **`budget_cap`** |
| Rate limits   | ~**0.21 s** between simulates (~5/s); ~**0.6 s** after submit (~2/s) |
| Seed split    | seed 0 prioritized; others share remainder evenly |

## What likely separates top teams (~53 vs ~24.5 Astar)

Speculative but consistent with the API:

- **Stronger priors** tuned to the real simulator (collapse/expansion of settlements, ruin rates, forest stability).
- **Smarter viewport placement** (e.g. weight by distance to settlements, choke points, coast) rather than uniform dynamic coverage.
- **More effective use of repeated samples** on high-entropy cells only; avoid wasting queries on ocean/mountain.
- **Cross-seed** sharing of structural information where rules allow (same initial grid; only rolls differ).
- **Calibration** of probability masses (floor, smoothing) to optimize expected KL.

## Changelog (repo)

- **2026-03-22:** `viewports_for_seed()` — **rotated** viewports for seeds **≥ 1**; **`budget_cap`** from server **remaining**; docstring clarifies this is **not** graph A*.

## See also

- [../astar.md](../astar.md) — short stub + run command  
- Client: `nmai2026/astar/astar_client.py`
