Generates the proposal and status decks QofAI presents to clients, and gives reviewers a
studio to finish them without touching HTML. Data comes from QofAI's internal platform over
MCP or from an uploaded PRD or research paper, and Claude Opus renders the deck from a typed
slide spec. Seven deterministic guards check it first. Every value must be sourced or marked
missing, no number or name may change during style passes, nothing may clip in headless
Chrome, and commercial figures are rebuilt from source.

The review studio is the center of the project. A reviewer types an edit in plain language,
Claude Opus translates it into exact text swaps on named slides, and deterministic code
applies them, so the rest of the deck stays byte-for-byte identical and a request the model
cannot pin down changes nothing. One value typed into a missing-value card fills every place
it belongs, such as a date repeated across six footers. Reviewers can switch individual
bullets on or off, enter conservative, base and optimistic commercial terms in one form,
confirm or send back flagged claims, and save formatting edits as standing preferences for
future decks (content edits never carry over). Every change is a logged revision with
one-click undo, and decks export to PDF. About 20,000 lines of application code and 2,447
tests. Sanitized with fictional clients and figures.

![The deck generator's review studio, showing a generated status deck](/qofai-deck-generator/docs/screenshots/review-studio.png)

Python, Claude API, MCP, Flask, Playwright, SQLite, Railway.
