"""Runtime configuration — Phase 13.2.

Small env-driven settings for auth-related behaviour. Not a full
pydantic-settings object — the project has no such dependency and these
are three values.

  COOKIE_SECURE   — Secure flag on session cookies. Default false for
                    local http://localhost development. Must be true in
                    production; HTTPS is required at the proxy layer.
  APP_ENV         — "development" (default) or "production". Controls
                    whether FastAPI exposes /docs, /redoc and
                    /openapi.json. In production these return 404.
  IS_PRODUCTION   — derived from APP_ENV.

Values are read once at import time. Changing them requires restarting
the API process. This matches the container model (env passed by compose).
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


COOKIE_SECURE: bool = _env_bool("COOKIE_SECURE", default=False)
APP_ENV: str = (os.environ.get("APP_ENV", "development") or "development").lower()
IS_PRODUCTION: bool = APP_ENV == "production"
