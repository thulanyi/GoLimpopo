import os
import sys
from urllib.parse import urlparse


def is_enabled(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def require_non_empty(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def require_https_url(name):
    value = require_non_empty(name)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError(f"{name} must be a valid https URL")
    return value


def main():
    require_non_empty("SECRET_KEY")
    require_non_empty("MAGAYISA_ADMIN_EMAIL")
    require_non_empty("MAGAYISA_ADMIN_PASSWORD")
    require_non_empty("MAGAYISA_POSTGRES_DSN")
    require_non_empty("MAGAYISA_REDIS_URL")
    require_https_url("MAGAYISA_PUBLIC_BASE_URL")

    if is_enabled(os.environ.get("PAYFAST_SANDBOX", "1")):
        raise RuntimeError("PAYFAST_SANDBOX must be 0 for production")

    if not is_enabled(os.environ.get("MAGAYISA_SESSION_COOKIE_SECURE", "0")):
        raise RuntimeError("MAGAYISA_SESSION_COOKIE_SECURE must be enabled in production")

    print("Production preflight passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Preflight failed: {exc}")
        sys.exit(1)
