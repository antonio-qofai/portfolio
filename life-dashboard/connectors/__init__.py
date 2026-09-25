"""One module per source. Each exposes `fetch(config: dict) -> list[Item]`.

Adding a source means adding a module here and registering it below; the
pipeline and page do not change.
"""

from connectors import ai_daily_brief, chores, gcal, gmail, job_search, nyt, weather

REGISTRY = {
    "weather": weather.fetch,
    "calendar": gcal.fetch,
    "email": gmail.fetch,
    "chores": chores.fetch,
    "job_search": job_search.fetch,
    "nyt": nyt.fetch,
    "ai_daily_brief": ai_daily_brief.fetch,
}
