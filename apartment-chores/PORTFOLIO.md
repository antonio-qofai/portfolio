Assigns chores in a shared apartment on a rotation that balances every chore across roommates
over a quarter, sends a weekly digest, and privately nudges anyone overdue. Claude Sonnet 5
reads the landlord's emails into proposed cleaner visits through structured output, and plain
code verifies each one. The quoted sentence must appear in the email, the date must fall in
range, and any named weekday must match the date. A visit whose date fails a check becomes an
undated request for a person to fill in, and nothing takes effect until a roommate confirms
it. All configuration lives in Airtable. Runs on GitHub Actions, with over 400 tests.

Python, Airtable API, Gmail SMTP/IMAP, Claude API, GitHub Actions.