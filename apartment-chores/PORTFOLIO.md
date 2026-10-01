A rotation scheduler for a shared apartment. Every roommate does every chore the same number
of times over a quarter. It emails a weekly digest and sends private nudges when a chore is
overdue, and it reads the landlord's emails with an LLM to pick up cleaner visits. Proposed
dates stay pending until a person confirms them. The whole setup (chores, people, rules) lives
in Airtable rows, so pointing it at a different apartment means editing data, not code. It runs
on GitHub Actions, with 389 tests.

Python, Airtable API, Gmail SMTP/IMAP, Claude API, GitHub Actions.
