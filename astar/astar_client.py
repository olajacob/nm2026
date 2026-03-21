"""
Astar Island — NM i AI 2026
Full client with correct API spec.

Strategy:
  1. Fetch initial_states from /rounds/{round_id} — FREE, gives full grid + settlements
  2. Build static prior tensor (ocean/mountain = 100% confident, no queries needed)
  3. Use queries ONLY on dynamic zones around initial settlements
  4. Deep-sample seed_index 0 (30 queries) for stochastic distribution
  5. Spot-check seeds 1–4 (5 queries each), blend with learned priors
  6. Submit per seed via POST /submit
"""

from __future__ import annotations

import os
import time
import requests
import numpy as np
from dotenv import load_dotenv

# ── Config ───────────────────────────────────────────────────────────────────
BASE_URL = "https://api.ainm.no/astar-island"
load_dotenv()
TOKEN = os.environ.get("ASTAR_TOKEN", "")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type":  "application/json",
}

MAP_W, MAP_H  = 40, 40
VIEWPORT_MAX  = 15
N_CLASSES     = 6
QUERY_BUDGET  = 50
N_SEEDS       = 5

# CRITICAL: Never assign 0.0 to any class.
# If ground truth has p > 0 and our q = 0, KL → infinity and nukes the cell score.
# Even "certain" ocean/mountain cells get a tiny floor — ground truth is Monte Carlo
# and may assign tiny probability to other classes.
PROB_FLOOR = 0.01   # Minimum probability per class per cell

# Internal grid value → prediction class index
GRID_TO_CLASS = {
    0:  0,   # Empty
    11: 0,   # Plains
    10: 0,   # Ocean
    1:  1,   # Settlement
    2:  2,   # Port
    3:  3,   # Ruin
    4:  4,   # Forest
    5:  5,   # Mountain
}


# ── API Calls ─────────────────────────────────────────────────────────────────

def get_round(round_id: str) -> dict:
    """Fetch round details + initial_states for all seeds. FREE (no query cost)."""
    r = requests.get(f"{BASE_URL}/rounds/{round_id}", headers=HEADERS)
    r.raise_for_status()
    return r.json()


def get_active_round() -> dict | None:
    """Return the first active round, or None."""
    r = requests.get(f"{BASE_URL}/rounds", headers=HEADERS)
    r.raise_for_status()
    rounds = r.json()
    for rnd in rounds:
        if rnd["status"] == "active":
            return rnd
    return None


def get_budget(round_id: str) -> dict:
    r = requests.get(f"{BASE_URL}/budget", headers=HEADERS)
    r.raise_for_status()
    return r.json()


def simulate(round_id: str, seed_index: int,
             vx: int, vy: int, vw: int = 15, vh: int = 15) -> dict:
    """
    Observe one stochastic simulation run through a viewport.
    Costs 1 query. Returns grid (viewport_h × viewport_w) + settlements in viewport.
    Rate limit: 5 req/sec per team.
    """
    payload = {
        "round_id":   round_id,
        "seed_index": seed_index,
        "viewport_x": vx,
        "viewport_y": vy,
        "viewport_w": vw,
        "viewport_h": vh,
    }
    for attempt in range(3):
        r = requests.post(f"{BASE_URL}/simulate", json=payload, headers=HEADERS)
        if r.status_code == 429:
            body_text = r.text.lower()
            if "budget" in body_text or "exhausted" in body_text:
                raise RuntimeError("Budget exhausted")
            wait_s = 1.0 * (attempt + 1)
            print(f"   ⏳ Rate limited — waiting {wait_s:.1f}s (attempt {attempt + 1}/3)")
            time.sleep(wait_s)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Rate limit exceeded after 3 retries")


def submit_seed(round_id: str, seed_index: int, prediction: np.ndarray) -> dict:
    """
    Submit H×W×6 prediction tensor for one seed.
    prediction[y][x][class] — each cell must sum to 1.0 ±0.01.
    Resubmitting overwrites previous. Rate limit: 2 req/sec.
    """
    prediction = validate_tensor(prediction, f"seed_{seed_index}")
    payload = {
        "round_id":   round_id,
        "seed_index": seed_index,
        "prediction": prediction.tolist(),
    }
    r = requests.post(f"{BASE_URL}/submit", json=payload, headers=HEADERS)
    r.raise_for_status()
    return r.json()


# ── Map Reconstruction (FREE) ─────────────────────────────────────────────────

def parse_initial_grid(initial_state: dict, map_h: int, map_w: int) -> np.ndarray:
    """
    Parse initial_state.grid (2D list of internal codes) into np array.
    initial_states[i].grid is the full H×W grid of terrain codes.
    """
    grid = np.array(initial_state["grid"], dtype=int)   # shape (H, W)
    assert grid.shape == (map_h, map_w), f"Unexpected grid shape: {grid.shape}"
    return grid


def is_coastal(grid: np.ndarray, x: int, y: int) -> bool:
    for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
        ny, nx = y + dy, x + dx
        if 0 <= ny < MAP_H and 0 <= nx < MAP_W and grid[ny, nx] == 10:
            return True
    return False


def build_prior_tensor(initial_grid: np.ndarray) -> np.ndarray:
    """
    Build H×W×6 prior tensor from static knowledge alone — no queries needed.

    Certainties (1.0 confidence):
      Ocean (10)    → class 0
      Mountain (5)  → class 5

    Informed priors for dynamic cells (will be updated by observations):
      Settlement (1) inland  → mostly stays settlement, can collapse or expand
      Settlement (1) coastal → high port development chance
      Port (2)               → mostly stays port, can collapse
      Forest (4)             → mostly stable
      Plains/Empty (11,0)    → mostly stays empty, small expansion chance
    """
    tensor = np.zeros((MAP_H, MAP_W, N_CLASSES))

    for y in range(MAP_H):
        for x in range(MAP_W):
            v = int(initial_grid[y, x])

            # NOTE: We never assign hard 0.0 to any class.
            # Even "certain" static cells get a tiny floor because the ground
            # truth is computed from Monte Carlo runs and may assign small
            # probability to edge-case outcomes. A single q=0 when p>0 → KL=∞.
            if v == 10:                     # Ocean — almost certainly stays class 0
                tensor[y, x, 0] = 0.95
                tensor[y, x, 1] = 0.01
                tensor[y, x, 2] = 0.01
                tensor[y, x, 3] = 0.01
                tensor[y, x, 4] = 0.01
                tensor[y, x, 5] = 0.01
            elif v == 5:                    # Mountain — almost certainly stays class 5
                tensor[y, x, 0] = 0.01
                tensor[y, x, 1] = 0.01
                tensor[y, x, 2] = 0.01
                tensor[y, x, 3] = 0.01
                tensor[y, x, 4] = 0.01
                tensor[y, x, 5] = 0.95
            elif v in (11, 0):             # Plains / Empty
                tensor[y, x, 0] = 0.87
                tensor[y, x, 1] = 0.07
                tensor[y, x, 2] = 0.01
                tensor[y, x, 3] = 0.03
                tensor[y, x, 4] = 0.01
                tensor[y, x, 5] = 0.01
            elif v == 1:                    # Settlement (inland default)
                tensor[y, x, 0] = 0.09
                tensor[y, x, 1] = 0.54
                tensor[y, x, 2] = 0.05
                tensor[y, x, 3] = 0.29
                tensor[y, x, 4] = 0.02
                tensor[y, x, 5] = 0.01
            elif v == 2:                    # Port
                tensor[y, x, 0] = 0.04
                tensor[y, x, 1] = 0.09
                tensor[y, x, 2] = 0.59
                tensor[y, x, 3] = 0.25
                tensor[y, x, 4] = 0.02
                tensor[y, x, 5] = 0.01
            elif v == 4:                    # Forest
                tensor[y, x, 0] = 0.10
                tensor[y, x, 1] = 0.01
                tensor[y, x, 2] = 0.01
                tensor[y, x, 3] = 0.01
                tensor[y, x, 4] = 0.86
                tensor[y, x, 5] = 0.01
            else:
                tensor[y, x, :] = 1.0 / N_CLASSES

    # Adjust coastal settlements: higher port probability
    for y in range(MAP_H):
        for x in range(MAP_W):
            if initial_grid[y, x] == 1 and is_coastal(initial_grid, x, y):
                tensor[y, x, 0] = 0.07
                tensor[y, x, 1] = 0.29
                tensor[y, x, 2] = 0.40
                tensor[y, x, 3] = 0.21
                tensor[y, x, 4] = 0.02
                tensor[y, x, 5] = 0.01

    return tensor


# ── Smart Viewport Prioritization ────────────────────────────────────────────

def get_dynamic_viewports(initial_grid: np.ndarray,
                           initial_state: dict,
                           radius: int = 12) -> list[tuple[int,int]]:
    """
    Identify viewport origins that maximize coverage of dynamic cells.
    Dynamic = cells that can change over 50 years (non-ocean, non-mountain).
    Prioritizes areas around initial settlements where most action happens.
    """
    dynamic_mask = np.zeros((MAP_H, MAP_W), dtype=bool)

    # Settlements from initial_state give exact positions
    settlements = initial_state.get("settlements", [])
    for s in settlements:
        sx, sy = s["x"], s["y"]
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                ny, nx = sy + dy, sx + dx
                if 0 <= ny < MAP_H and 0 <= nx < MAP_W:
                    if initial_grid[ny, nx] not in (10, 5):
                        dynamic_mask[ny, nx] = True

    # Also mark all non-static cells as dynamic (forests, plains near land)
    for y in range(MAP_H):
        for x in range(MAP_W):
            if initial_grid[y, x] not in (10, 5):
                dynamic_mask[y, x] = True

    # Score all viewport positions by dynamic cell count
    scored = []
    step = 5
    for y in range(0, MAP_H, step):
        for x in range(0, MAP_W, step):
            # Clamp viewport to map edges
            vw = min(VIEWPORT_MAX, MAP_W - x)
            vh = min(VIEWPORT_MAX, MAP_H - y)
            score = int(dynamic_mask[y:y+vh, x:x+vw].sum())
            if score > 0:
                scored.append((score, x, y, vw, vh))
    scored.sort(reverse=True)

    # Greedy dedup: pick viewports that add new dynamic coverage
    covered = np.zeros((MAP_H, MAP_W), dtype=bool)
    chosen  = []
    for score, x, y, vw, vh in scored:
        new = dynamic_mask[y:y+vh, x:x+vw] & ~covered[y:y+vh, x:x+vw]
        if new.sum() > 0:
            chosen.append((x, y, vw, vh))
            covered[y:y+vh, x:x+vw] = True
        if covered[dynamic_mask].all():
            break

    n_settlements = len(settlements)
    n_dynamic     = int(dynamic_mask.sum())
    print(f"   📍 {n_settlements} settlements | "
          f"🎯 {n_dynamic} dynamic cells | "
          f"🗺️  {len(chosen)} viewports for full coverage")
    return chosen


# ── Observation & Tensor Update ───────────────────────────────────────────────

def run_observations(round_id: str, seed_index: int,
                     viewports: list[tuple[int,int,int,int]],
                     max_queries: int, query_log: list,
                     settlement_stats: dict) -> tuple[np.ndarray, np.ndarray]:
    """
    Execute queries and accumulate class counts per cell.
    Also collects settlement stats (population, food, etc.) for richer priors.
    Returns (counts H×W×6, sample_count H×W).
    """
    counts       = np.zeros((MAP_H, MAP_W, N_CLASSES))
    sample_count = np.zeros((MAP_H, MAP_W))
    this_seed_q  = 0

    for vx, vy, vw, vh in viewports:
        if len(query_log) >= QUERY_BUDGET or this_seed_q >= max_queries:
            break

        try:
            result = simulate(round_id, seed_index, vx, vy, vw, vh)
        except RuntimeError as e:
            msg = str(e)
            if "Budget exhausted" in msg:
                print("   🛑 Budget exhausted — stopping observations for this run")
                break
            raise
        query_log.append({"seed_index": seed_index, "vx": vx, "vy": vy})
        this_seed_q += 1
        time.sleep(0.21)  # Stay under 5 req/sec

        # Parse grid (viewport region only)
        grid = result.get("grid", [])
        for dy, row in enumerate(grid):
            for dx, code in enumerate(row):
                gy, gx = vy + dy, vx + dx
                if gy >= MAP_H or gx >= MAP_W:
                    continue
                cls = GRID_TO_CLASS.get(int(code), 0)
                counts[gy, gx, cls] += 1
                sample_count[gy, gx] += 1

        # Collect settlement stats (population, food → survival signal)
        for s in result.get("settlements", []):
            key = (s["x"], s["y"])
            if key not in settlement_stats:
                settlement_stats[key] = []
            settlement_stats[key].append({
                "alive":      s.get("alive", True),
                "population": s.get("population", 0),
                "food":       s.get("food", 0),
            })

        remaining = QUERY_BUDGET - len(query_log)
        print(f"   [{this_seed_q}/{max_queries}] vp=({vx},{vy},{vw}×{vh}) "
              f"| budget left: {remaining}")

    return counts, sample_count


def finalize_tensor(base_tensor: np.ndarray,
                    counts: np.ndarray,
                    sample_count: np.ndarray,
                    initial_grid: np.ndarray,
                    settlement_stats: dict) -> np.ndarray:
    """
    Merge empirical observations with static priors.
      - Ocean / Mountain: always static, never overridden
      - Observed cells: blend empirical freq with prior
        (weight = min(1.0, n_obs/3) → full trust at 3+ observations)
      - Settlements with low avg food/population: boost ruin probability
      - Unobserved dynamic cells: use base prior unchanged
    """
    tensor = base_tensor.copy()

    for y in range(MAP_H):
        for x in range(MAP_W):
            code  = int(initial_grid[y, x])
            total = sample_count[y, x]

            if code in (10, 5):
                continue   # Static — never touch

            if total > 0:
                empirical = counts[y, x, :] / total
                weight    = min(1.0, total / 3.0)
                tensor[y, x, :] = (weight * empirical
                                   + (1 - weight) * base_tensor[y, x, :])

            # Settlement survival signal: if observed alive rarely → boost ruin
            key = (x, y)
            if key in settlement_stats and code in (1, 2):
                observations = settlement_stats[key]
                alive_rate   = sum(1 for o in observations if o["alive"]) / len(observations)
                avg_food     = np.mean([o["food"] for o in observations])
                if alive_rate < 0.4 or avg_food < 0.2:
                    # Likely to collapse — shift probability toward ruin
                    tensor[y, x, 3] = min(0.80, tensor[y, x, 3] * 1.5)

    # CRITICAL: Enforce probability floor before normalization.
    # A single q=0 where ground truth p>0 sends KL(p||q) to infinity.
    # 0.01 floor costs almost nothing in score but prevents catastrophic blowups.
    tensor = np.maximum(tensor, PROB_FLOOR)

    # Normalize all cells to sum to 1.0
    row_sums = tensor.sum(axis=2, keepdims=True)
    return tensor / row_sums


# ── Main Round Loop ───────────────────────────────────────────────────────────

def run_round(round_id: str | None = None):
    """
    Full round execution.

    Budget allocation (50 queries, 5 seeds):
      seed_index 0: 30 queries — repeat dynamic viewports for frequency distribution
      seed_index 1–4: 5 queries each — top-priority viewports only
    """
    # Find active round if not given
    if round_id is None:
        rnd = get_active_round()
        if rnd is None:
            print("No active round")
            return
        round_id = rnd["id"]

    print(f"\n🚀 Round {round_id}")

    # Fetch round data (initial_states for all seeds) — FREE
    round_data = get_round(round_id)
    map_h      = round_data["map_height"]
    map_w      = round_data["map_width"]
    n_seeds    = round_data["seeds_count"]
    initial_states = round_data["initial_states"]

    assert map_h == MAP_H and map_w == MAP_W, \
        f"Map size mismatch: expected {MAP_H}×{MAP_W}, got {map_h}×{map_w}"

    budget = get_budget(round_id)
    queries_used = budget["queries_used"]
    queries_max = budget["queries_max"]
    queries_remaining = queries_max - queries_used
    print(f"   Budget: {budget['queries_used']}/{budget['queries_max']} used "
          f"({queries_remaining} remaining)\n")

    n_other_seeds = max(1, n_seeds - 1)
    remaining = max(0, queries_remaining)
    seed_0_queries = max(0, min(30, remaining - n_other_seeds * 5))
    other_seed_queries = max(0, (remaining - seed_0_queries) // n_other_seeds)
    if queries_used >= 30:
        seed_0_queries = 0
    print(f"   Allocation: seed_0={seed_0_queries}, other_seeds={other_seed_queries} each")

    query_log = []

    for seed_index in range(n_seeds):
        print(f"\n{'='*55}")
        print(f"🌍 Seed {seed_index} / {n_seeds - 1}")

        initial_state = initial_states[seed_index]
        initial_grid  = parse_initial_grid(initial_state, map_h, map_w)

        # Build prior tensor from static knowledge — FREE
        base_tensor = build_prior_tensor(initial_grid)

        # Identify dynamic viewports
        dynamic_vp = get_dynamic_viewports(initial_grid, initial_state)

        # Allocate queries
        if seed_index == 0:
            max_q = seed_0_queries
            # Repeat top viewports to get stochastic distribution per cell
            viewports = (dynamic_vp * 6)[:max_q]
        else:
            max_q = other_seed_queries
            viewports = dynamic_vp[:max_q]

        # Run observations
        print(f"   🔍 Querying {max_q} viewports...")
        settlement_stats = {}
        counts, sample_count = run_observations(
            round_id, seed_index, viewports, max_q, query_log, settlement_stats
        )

        # Merge with priors
        final_tensor = finalize_tensor(
            base_tensor, counts, sample_count, initial_grid, settlement_stats
        )

        observed = int((sample_count > 0).sum())
        print(f"   ✅ {observed} cells observed | "
              f"{map_w * map_h - observed} from prior | "
              f"budget used: {queries_used + len(query_log)}/{queries_max}")

        # Submit this seed immediately (don't wait for all seeds)
        print(f"   📤 Submitting seed {seed_index}...")
        result = submit_seed(round_id, seed_index, final_tensor)
        print(f"   ✅ {result['status']}")
        time.sleep(0.6)   # Stay under submit rate limit (2 req/sec)

    print(f"\n🏁 Round complete | Total queries this run: {len(query_log)} | "
          f"overall used: {queries_used + len(query_log)}/{queries_max}")


# ── Validation ────────────────────────────────────────────────────────────────

def validate_tensor(tensor: np.ndarray, label: str = "") -> np.ndarray:
    assert tensor.shape == (MAP_H, MAP_W, N_CLASSES), \
        f"[{label}] Wrong shape: {tensor.shape}"
    # Apply floor and renormalize — eliminates all float precision issues
    tensor = np.maximum(tensor, PROB_FLOOR)
    tensor = tensor / tensor.sum(axis=2, keepdims=True)
    max_err = abs(tensor.sum(axis=2) - 1.0).max()
    assert max_err < 0.01, f"[{label}] Sum error: {max_err:.4f}"
    print(f"   ✅ [{label}] min_prob={tensor.min():.4f}  max_sum_err={max_err:.2e}")
    return tensor  # Return the fixed tensor


if __name__ == "__main__":
    try:
        run_round()   # Auto-detects active round
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403):
            print("Check ASTAR_TOKEN in .env")
        else:
            print(f"Request failed: {e}")
    except requests.RequestException as e:
        msg = str(e)
        if "401" in msg or "403" in msg:
            print("Check ASTAR_TOKEN in .env")
        else:
            print(f"Request failed: {e}")