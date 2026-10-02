"""The served calendar. `ui.app` is the Flask application.

A package rather than a loose script because the hosted version imports it as
`ui.app` under gunicorn, which is the deck generator's arrangement and the one
the portal already knows how to mount.
"""
