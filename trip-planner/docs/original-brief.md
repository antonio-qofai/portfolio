Replit Prompt — Working Version (Backend + Offline + Rubric)
Build a fully functional (NOT design-only) offline web app called **Survey → Itinerary Agent**. Do NOT create a frontend-only planner. Use a Python backend (FastAPI) and return stable JSON from the backend.

## Rubric Requirements (must be explicit in the app)
**C1 One job:** Pick best destination + runner-up + simple 3–5 day itinerary from survey.csv. Include a “not today” sentence.

**C2 Static dataset:** Must run fully offline using local files only:
- `survey.csv`
- `destinations.json`

**C3 Stable I/O:** Always return JSON with `status` in `{ "ok", "needs_info", "error" }`.

**C4 5 tests:** Implement exactly 5 pass/fail tests (below).

**C5 Offline demo:** No web search, no logins, no external APIs.

---

## Data schemas (match these)
### survey.csv
Required fields (case-insensitive):
- `name`
- `departure_city` OR `home_airport` OR `origin`
- availability: either `start_date` + `end_date` (YYYY-MM-DD) OR `available_dates` (`YYYY-MM-DD..YYYY-MM-DD;...`)
- `budget` (numeric; may include `$` or commas—must parse)
- `top_interests` (comma-separated)

Optional:
- `budget_flex_pct`, `trip_length_days_min`, `trip_length_days_max`
- `max_travel_hours`, `direct_flight_only`
- `passport_ready`, `climate_preference`, `avoid_crowds`, `safety_priority`
- `accessibility_needs`, `dietary_restrictions`, `must_have`, `dealbreakers`, `notes`

### destinations.json (match EXACTLY)
List of objects with:
- `id`, `name`, `country_or_region`
- `climate_tags` (list), `activity_tags` (list)
- `estimated_cost_per_person_usd` (number)
- `notes` (string)

Do NOT assume flight/time data exists.

---

## Critical bug fixes (must implement)
1) **Robust budget parsing**
   - Parse budgets like `"$1,500"` and `"1500"` into floats/ints safely.
   - If budget missing/unparseable, return `status="needs_info"` with `missing_fields`.

2) **Trip-length logic must not hard fail on 4 days**
   - Default target is 4 days, BUT if no 4-day overlap:
     - Try 3 days, then 2 days (down to 2).
     - If overlap exists at shorter length, return `status="ok"` AND add a warning:
       `"No 4-day overlap; using 3-day overlap instead."`
     - If no overlap even at 2 days, return `status="error"` with suggested fixes.

3) **Runner-up must always be returned**
   - If ≥2 feasible destinations: return distinct `runner_up`
   - If only 1 feasible: still return a runner-up as “closest alternative” and explain why it failed feasibility.

4) **Always show meaningful suggested_fixes on errors**
   - e.g., adjust dates, shorten trip, raise budget, relax constraints, reduce group size.

---

## Planner pipeline (planner.py)
- Load both files.
- Validate required fields; on missing return `needs_info`.
- Compute group overlap window (consider `available_dates` or start/end).
- Decide trip length using fallback (4→3→2).
- Filter destinations:
  - If any `passport_ready=false`, restrict to `country_or_region == "USA"`.
  - Budget feasibility: compare `estimated_cost_per_person_usd` vs allowable group budget (min budget + flex; otherwise 20% tolerance).
- Score:
  - Interest match: overlap group interests vs `activity_tags`
  - Climate match: overlap vs `climate_tags` if present
  - Budget fit: under budget scores higher
  - Crowd avoidance heuristic: if avoid_crowds true for majority, lightly penalize very popular destinations (hardcode list) and document as assumption
- Output stable JSON with breakdown, itinerary, warnings, fixes.

---

## API + UI (must create)
FastAPI backend in `main.py`:
- `GET /api/health` -> `{ "ok": true }`
- `POST /api/plan` -> returns planner JSON
- `GET /` -> a tiny HTML page with a “Generate Plan” button that calls `/api/plan` and pretty prints returned JSON.

---

## Tests (exactly 5)
Use pytest in `tests/test_planner.py` calling planner function directly:
1) No overlap even at 2 days -> error + message mentions overlap
2) Budget mismatch -> error or needs_info + suggested_fixes includes budget guidance
3) Too-tight constraints -> error + limiting_factors list non-empty
4) Missing required fields -> needs_info + missing_fields per person
5) Runner-up returned when 2+ feasible

---

## Project structure
- `main.py`
- `planner.py`
- `templates/index.html`
- `tests/test_planner.py`
- `requirements.txt` including fastapi uvicorn pandas python-dateutil pytest

---

## Final requirement
When generated:
- `uvicorn main:app --host 0.0.0.0 --port 8000` runs successfully
- UI loads at `/`
- `/api/plan` returns JSON using local files
- `pytest` passes

