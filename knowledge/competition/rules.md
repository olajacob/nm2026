# NM i AI 2026 — competition rules (summary)

## Tracks

Three independent scored tracks (see official brief for authoritative rules):

1. **Astar** — `nmai2026/astar/` — terrain / simulation output.
2. **Tripletex** — `nmai2026/tripletex/` — ERP API agent (`agent.py`, `server.py`, `POST /solve`).
3. **NorgesGruppen** — `nmai2026/norgesgruppen/` — detection **`run.py`** + zip submit.

## Submissions

- **Tripletex:** hosted agent + platform **`session_token`** / proxy; local test **`python3 test_sandbox.py`**.
- **NorgesGruppen:** zip upload; constraints in **`knowledge/norgesgruppen/sandbox.md`**.
- **Repo:** team fork / push per organiser instructions (`data.json` lists example **`github.com/olajacob/nm2026`**).

## Deadlines & limits

- **Deadline** moves per round — check **`nmai2026/tripletex/data.json`** → **`deadline`** and platform UI.
- **Python:** repo targets **3.12** for general code; **NorgesGruppen sandbox** runs **3.11** (see **`norgesgruppen/sandbox.md`**).

## Where scores live

- **`nmai2026/tripletex/data.json`** — team snapshot: **`leaderboard`**, **`tasks`**, **`backlog`**, **`notes`**.
- Refresh **`knowledge/competition/leaderboard.md`** when the public scoreboard changes.
