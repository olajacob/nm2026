# Workflow conventions

## Tripletex agent (`/nmai` skill)

1. Read **`nmai2026/tripletex/logs/last_solve.log`** (and archive path in header if needed).
2. Tie failures to **API contract** vs **prompt** vs **sanitizer** vs **model**; one focused fix.
3. Edit **`agent.py`** (and **`test_sandbox.py`** when adding regression tests).
4. **Test:**  
   `cd nmai2026/tripletex && python3 test_sandbox.py --local-only`  
   Full: `python3 test_sandbox.py` (needs **`TRIPLETEX_SESSION_TOKEN`** or **`nmai2026/.env`**).
5. **Scoreboard / dashboard:** update **`nmai2026/tripletex/data.json`** only when explicitly syncing grades / backlog.
6. **Traceability:** set **`task_id`** / **`X-Task-Id`** / **`TASK_ID`** on **`POST /solve`**.

## Before submit (any track)

- Run the relevant **local** tests / smoke scripts.
- **Revert or bisect** if **`--local-only`** (or track-specific tests) regress after a change.

## Knowledge hygiene

- New recurring bug → **`knowledge/ERRORS.md`** (top entry).
- Stable fix → move summary into **`knowledge/tripletex/api-quirks.md`** (or track file) and trim the log entry.

## Cursor rules

- **`nmai2026/.cursorrules`**: points to **`knowledge/INDEX.md`** for progressive loading.
