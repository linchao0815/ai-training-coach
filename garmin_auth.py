"""
Shared Garmin Connect authentication for fetch_garmin_data.py and
garmin_runner.py, extracted 2026-09 so both scripts don't each maintain
their own login/session-cache logic (see
docs/superpowers/specs/2026-09-01-garmin-workout-sync-design.md).
"""

import os
import sys

try:
    import garminconnect
except ImportError:
    garminconnect = None  # callers that only need TOKEN_STORE can still import this module

TOKEN_STORE = os.path.expanduser("~/.garminconnect")

# .env is gitignored at the repo root (see .gitignore) and, per this project's
# established convention (see wiki/WIKI.md's note on .mcp.json's ${VAR}
# expansion), is NOT auto-loaded by anything else in this repo -- shells,
# .mcp.json, etc. all read the real process environment. This loader is a
# deliberately tiny, dependency-free fallback (no python-dotenv) so
# GARMIN_EMAIL/GARMIN_PASSWORD can live in that same .env file instead of
# having to be set in every shell session by hand. It only fills in keys not
# already present in os.environ, so an explicitly-set env var always wins.
_DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _load_dotenv_defaults(path=_DOTENV_PATH):
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _mfa_prompt() -> str:
    return input("Enter Garmin Connect MFA / one-time code: ").strip()


def get_client():
    """Return a logged-in garminconnect.Garmin client.

    Resumes a cached session under TOKEN_STORE if present, otherwise falls
    back to GARMIN_EMAIL/GARMIN_PASSWORD (read from the real environment, or
    from a gitignored .env file at the repo root via _load_dotenv_defaults())
    -- plus an interactive MFA prompt if the account has it enabled -- and
    caches the new session for next time.
    """
    _load_dotenv_defaults()

    if garminconnect is None:
        sys.exit(
            "garminconnect is not installed. Install the `garminconnect[workout]` extra, "
            "or run the calling script with uv so its PEP 723 inline dependency is "
            "installed automatically (e.g. `uv run fetch_garmin_data.py`)."
        )

    email = os.environ.get("GARMIN_EMAIL")
    password = os.environ.get("GARMIN_PASSWORD")
    if not os.path.isdir(TOKEN_STORE) and not (email and password):
        sys.exit(
            "No cached Garmin session found at ~/.garminconnect/ and "
            "GARMIN_EMAIL/GARMIN_PASSWORD are not set. Set them and re-run, e.g.:\n"
            '  $env:GARMIN_EMAIL="you@example.com"; $env:GARMIN_PASSWORD="..."\n'
            "or put GARMIN_EMAIL=...\\nGARMIN_PASSWORD=... in a .env file next to "
            "this script (gitignored)."
        )

    client = garminconnect.Garmin(email=email, password=password, prompt_mfa=_mfa_prompt)
    try:
        client.login(TOKEN_STORE)
    except garminconnect.GarminConnectAuthenticationError as exc:
        sys.exit(f"Garmin Connect authentication failed: {exc}")
    return client
