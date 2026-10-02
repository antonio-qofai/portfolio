Built during my internship at QofAI, this agent writes the proposal and status check-in
decks QofAI brings to a client engagement. It pulls a project's data from the company's
internal platform over MCP, or parses an uploaded PRD or research paper, and maps it onto a
typed slide spec. Then it renders an HTML deck through Claude and runs it through a stack of
guards before a person sees it. The guards check that every needed value is either sourced
or visibly marked missing, that no number, date or name changes during style edits, that
nothing clips or overlaps on the slide, and that commercial figures come from the source and
not from the model. Reviewers work in a hosted studio where they toggle bullets, request
edits in plain language with undo, fill in missing values and export to PDF. It has about
2,400 tests. This copy is sanitized: clients, people and deal figures are replaced with
fictional ones.

Python, Claude API, MCP, Flask, Playwright, pypdf and python-docx, SQLite, Railway.
