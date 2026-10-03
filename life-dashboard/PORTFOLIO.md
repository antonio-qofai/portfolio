A personal morning brief that builds one local page at 6 AM and emails a digest at 7. Live
sources: Open-Meteo weather, five Google calendars including Canvas due dates, two Gmail
inboxes, chore and internship reports from my other agents, NYT, and the AI Daily Brief. Claude
Haiku 4.5 triages email in batches of 10. Claude Opus 5.5 rates each upcoming event's lead
time, then ranks up to seven Pressing actions using my feedback. Code rejects non-read-only
Google scopes, fetches email metadata and never bodies, and falls back to rules when a model
call fails. Work meetings reach only the lead-time call, never the ranking. 126 offline tests.

Python 3.12, uv, launchd, Gmail and Google Calendar APIs, Open-Meteo, NYT API, Claude API
(Haiku 4.5, Opus 5.5), Tailscale.
