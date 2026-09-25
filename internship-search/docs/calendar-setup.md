# Google Calendar setup

One-time setup, roughly fifteen minutes. You have to do this yourself because it
authorises access to your Google account. After it is done, syncing is a single
command and the token refreshes on its own.

Nothing here costs money. The Calendar API is free at the volume this uses.

## What you are creating

A Google Cloud project that exists only to hold an OAuth client ID. That client ID
lets this script ask your permission to write to your calendar. You are the only
user, and the app stays in testing mode, which is fine and expected.

## Steps

1. Go to the Google Cloud Console and create a new project. Name it something like
   `internship-watcher`. https://console.cloud.google.com/projectcreate

2. Enable the Calendar API for that project. Search "Google Calendar API" in the
   console, open it, and press Enable.
   https://console.cloud.google.com/apis/library/calendar-json.googleapis.com

3. Configure the OAuth consent screen. Choose External, since a personal Gmail
   account cannot use Internal. Fill in an app name and your own email for both the
   support and developer contact fields. Skip the scopes page.

   Then open the Audience tab in the left sidebar and press Publish app. Google
   moved test users onto that tab, and leaving the app in Testing mode blocks even
   the project owner with `Error 403: access_denied` until an address is added to
   the allowlist. Testing mode also expires refresh tokens after seven days, which
   means reauthorising weekly. Publishing avoids both. There is no review to pass
   here; the only user is you and the only scope is your own calendar.
   https://console.cloud.google.com/auth/audience

4. Create the credentials. Go to Credentials, then Create Credentials, then OAuth
   client ID. Choose Desktop app as the application type. Name it anything.

5. Download the JSON and save it in the repo root as exactly `credentials.json`.
   It is already in `.gitignore` and must never be committed.

6. Run the sync.

       .venv/bin/python -m tools.sync_calendar

   Use the interpreter inside `.venv`. Plain `python` does not exist on macOS, and
   the system `python3` does not have the Google libraries installed.

   A browser window opens asking you to authorise. Google will warn that the app is
   unverified, which is expected for a personal app with one user. Choose Advanced,
   then Continue.

That is it. The token is cached in `.google-token.json` and refreshes silently, so
you will not see the browser again unless you revoke access or delete that file.

## What it creates

A separate calendar named "Internship Deadlines". Nothing is ever written to your
primary calendar, so you can hide or delete the whole thing in one action without
touching your own events.

Each application window becomes an all-day event marked free rather than busy, with
a popup reminder one day before it opens. Titles are prefixed `[expected]` or
`[unconfirmed]` so you can see at a glance which dates are real and which are
inferred from prior cycles. None of them are company-confirmed.

## Changing the dates

Edit `sources/cycle_windows.toml`, then run the sync again. Events are matched by
their `key` field and patched in place, so running it repeatedly updates events
rather than creating duplicates.

The one thing that breaks this: changing a `key` after its event exists. The sync
cannot tell that the renamed window is the same one, so it creates a second event
and orphans the first. Change the dates and label freely, leave the key alone.

Editing an event inside Google Calendar does not work either. The next sync
overwrites it. Change the TOML.

## Checking without authorising

    python -m tools.sync_calendar --dry-run

Lists every window, flags which are open today, and touches nothing. Works without
any of the setup above.
