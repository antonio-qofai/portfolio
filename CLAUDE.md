# Portfolio repo: standing rules

This repo is public. It is a published, sanitized copy of projects that live in their own
private repos. Treat everything here as visible to employers.

## Where edits go

- Project code (every folder except `tools/`) is generated. Never edit it here. The next sync
  deletes the folder and rebuilds it from the source repo, so any change made here is lost.
  To change a project, work in its source repo and commit there.
- Edit these files here: `README.md` (outside the generated project list), `CLAUDE.md`,
  `tools/sync.py`, `tools/rules.toml`, and `tools/overlays/`.
- The README project list between `<!-- projects:start -->` and `<!-- projects:end -->` is
  generated from each published folder's `PORTFOLIO.md`. To change a description, edit
  `PORTFOLIO.md` in the source repo. The grouping and order are the `[[readme_group]]` entries in `rules.toml`.
- Source repo paths are listed under `[sources]` in the private config for each machine. They
  are not in this repo.

## How the sync works

`tools/sync.py` builds each project in this order:

1. Export the source repo's `main` branch (or the project's `branch` in `rules.toml`). The
   checked-out branch and uncommitted edits are never published.
2. Drop excluded files.
3. Replace marked blocks and apply overlays (example files that stand in for personal ones).
4. Apply redactions.
5. Scan the result for secrets, unapproved email addresses and denied text.
6. Review: Claude reads the diff against what is already published and flags personal or
   confidential details the rules missed. If the review cannot run, nothing is published.

If both gates pass, the project is copied into its folder here, the README list is rebuilt,
and the result is committed and pushed. Any finding stops that project and nothing is
published.

`uv run --script tools/sync.py --status` shows the last result per project, with findings.
After the owner has read a review's findings and decided they are false alarms,
`sync.py NAME --approve-review` publishes that exact change. Anything newer is reviewed again.
Never run `--approve-review` without the owner's explicit decision.

A post-commit hook in each source repo runs the sync automatically. Output goes to
`~/Library/Logs/portfolio-sync.log`, and a blocked sync raises a macOS notification. Both Macs
push to this repo, so always `git pull` before editing, and let the sync script handle its
own pull and push.

## Public and private config

- `tools/rules.toml` (public): excludes, overlays, block replacements, generic redactions such
  as Airtable IDs, secret patterns, and the allowlist of fake test-fixture emails.
- `~/.config/portfolio-sync/private.toml` (private, one per machine, mode 600, never
  committed): source paths, and every redaction or denied string that names a real person,
  address, account, client or identifier.

Never put a real value in `rules.toml`, `README.md`, `CLAUDE.md`, a commit message or an
overlay. A public list of what is hidden reveals it.

## Adding or changing a project

1. Audit the source repo for sensitive content before writing any rules.
2. Add the source path and redactions to the private config. Add a `[projects.<name>]`
   section to `rules.toml`.
   For a shared monorepo, set `subdir` so only the owner's folder is exported, and
   `branch` if the publish branch is not `main`.
3. Choose a neutral folder name. Folder names are not scanned, so they must never contain a
   client or person's name.
4. Run `uv run --script tools/sync.py <name> --check` until it is clean.
5. Build into a scratch folder, grep the output by hand, and run the project's tests on the
   redacted copy. A clean scan is not proof.
6. Show the owner the published file list before the first push.

When the scan flags a new email address, approve it in `email_allow` only if it is clearly a
fake fixture. Anything real becomes a private redaction.

## QofAI projects

QofAI work is published with a cofounder's approval, on the condition that the owner reviews
it first. Those repos live on the work Mac, not the one that holds the personal projects. They
are synced by hand, with no post-commit hook, so later changes never publish without review.
Their sections in `rules.toml` set `hook = false`, which makes `--install-hooks` skip them.
Do not install hooks for them.

## Writing style for README and docs

Direct and plain. No em dashes, no exclamation points, no bold inside paragraphs, active
voice. Describe only what a project actually does today, checked against its code.
