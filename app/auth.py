"""Access code for the costly features (analyst chat, full database URI).

Everything else stays public. The code is checked against a salted PBKDF2 hash from the environment, and a correct code earns a
signed session cookie, so there are no accounts, no database and no new dependency (standard library only).

Environment
  ACCESS_CODE_HASH  ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``. Unset: the gate is off and everything is open.
  SESSION_SECRET    32+ random characters that sign the cookie. Required when ACCESS_CODE_HASH is set.

Make both:  uv run python -m app.auth hash      (asks for the code, prints the ACCESS_CODE_HASH line)
            uv run python -m app.auth secret    (prints a SESSION_SECRET line)
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import getpass
import hashlib
import hmac
import logging
import os
import secrets
import sys
import time

with contextlib.suppress(ImportError):  # the CLI below must also run without the optional `app` extra
    from fastapi import HTTPException, Request

log = logging.getLogger("s2cd.app")
COOKIE = "s2c_session"
MAX_AGE = 7 * 24 * 3600  # seconds; also the age after which a signed cookie is refused
ITERATIONS = 600_000  # OWASP 2023 guidance for PBKDF2-HMAC-SHA256
SCHEME = "pbkdf2_sha256"
DENIED = "This feature needs the access code."


# ---- Hash of the access code ---------------------------------------------------------------------------------------


def hash_code(code: str, *, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, iterations)
    return "$".join((SCHEME, str(iterations), base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))


def verify_code(code: str, stored: str) -> bool:
    """Constant-time check of ``code`` against a ``hash_code`` string; a malformed ``stored`` never matches."""
    try:
        scheme, iterations, salt, digest = stored.split("$")
        expected = base64.b64decode(digest, validate=True)
        got = hashlib.pbkdf2_hmac("sha256", code.encode(), base64.b64decode(salt, validate=True), int(iterations), len(expected))
    except ValueError:  # includes binascii.Error and a wrong number of fields
        return False
    return scheme == SCHEME and hmac.compare_digest(got, expected)


# ---- Session cookie: "<issued-at>.<HMAC-SHA256 of it>" -------------------------------------------------------------


def _sign(issued: str) -> str:
    return hmac.new(os.environ["SESSION_SECRET"].encode(), f"s2c1.{issued}".encode(), hashlib.sha256).hexdigest()


def make_session(now: float | None = None) -> str:
    issued = str(int(now if now is not None else time.time()))
    return f"{issued}.{_sign(issued)}"


def session_valid(value: str | None, now: float | None = None) -> bool:
    issued, _, sig = (value or "").partition(".")
    if not (issued.isascii() and issued.isdigit()) or not hmac.compare_digest(sig.encode(), _sign(issued).encode()):
        return False
    age = (now if now is not None else time.time()) - int(issued)
    return -60 <= age <= MAX_AGE  # a minute of clock skew is tolerated, a future-dated cookie is not


# ---- Gate ----------------------------------------------------------------------------------------------------------


def enabled() -> bool:
    return bool(os.environ.get("ACCESS_CODE_HASH"))


def authenticated(request: Request) -> bool:
    return not enabled() or session_valid(request.cookies.get(COOKIE))


def require_access(request: Request) -> None:
    """FastAPI dependency for the gated endpoints: 401 without a valid session cookie (no-op when the gate is off)."""
    if not authenticated(request):
        raise HTTPException(401, DENIED)


def check_config() -> None:
    """Call at start-up: warn when the gate is off, refuse to start on a half-configured gate."""
    if not enabled():
        log.warning("ACCESS_CODE_HASH is not set: authentication is DISABLED and every endpoint is open.")
        return
    hint = "make one with `python -m app.auth hash`"
    parts = os.environ["ACCESS_CODE_HASH"].split("$")
    if len(parts) != 4 or parts[0] != SCHEME or not parts[1].isdigit():
        raise RuntimeError(f"ACCESS_CODE_HASH must look like {SCHEME}$<iterations>$<salt_b64>$<hash_b64>; {hint}.")
    try:
        [base64.b64decode(p, validate=True) for p in parts[2:]]
    except binascii.Error:
        raise RuntimeError(f"ACCESS_CODE_HASH has an invalid base64 part; {hint}.") from None
    if len(os.environ.get("SESSION_SECRET", "")) < 32:
        raise RuntimeError("SESSION_SECRET must have 32+ characters when ACCESS_CODE_HASH is set (`python -m app.auth secret`).")


def cookie_kwargs() -> dict:
    return {"max_age": MAX_AGE, "path": "/api", "httponly": True, "secure": True, "samesite": "strict"}


# ---- CLI -----------------------------------------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if argv == ["hash"]:
        code = getpass.getpass("Access code: ")
        if code != getpass.getpass("Repeat: "):
            print("The two entries differ.", file=sys.stderr)
            return 1
        if len(code) < 8:
            print("Use at least 8 characters.", file=sys.stderr)
            return 1
        print(f"ACCESS_CODE_HASH='{hash_code(code)}'")
        return 0
    if argv == ["secret"]:
        print(f"SESSION_SECRET={secrets.token_urlsafe(48)}")
        return 0
    print("usage: python -m app.auth hash | secret", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
