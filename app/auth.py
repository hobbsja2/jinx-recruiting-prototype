import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import NamedTuple

# Load local .env early so environment settings (SECRET_KEY, AUTH_USERS)
# are available when this module initializes.
from dotenv import load_dotenv
load_dotenv()

SESSION_DURATION_SECONDS = int(os.environ.get("SESSION_DURATION_SECONDS", str(7 * 24 * 3600)))
SECRET_KEY = os.environ.get("SECRET_KEY", "")
AUTH_USERS = []


@dataclass
class ParsedHash:
    algorithm: str
    rounds: int
    salt: bytes
    derived: bytes


def parse_hash(hashed: str) -> ParsedHash | None:
    try:
        algorithm, rounds, salt_b64, derived_b64 = hashed.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return None
        # base64 may have had its padding stripped; restore correct padding
        def _pad(s: str) -> str:
            return s + ("=" * (-len(s) % 4))
        salt = base64.urlsafe_b64decode(_pad(salt_b64))
        derived = base64.urlsafe_b64decode(_pad(derived_b64))
        return ParsedHash(algorithm=algorithm, rounds=int(rounds), salt=salt, derived=derived)
    except Exception:
        return None


def pbkdf2_verify(password: str, hashed: str) -> bool:
    parsed = parse_hash(hashed)
    if not parsed:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), parsed.salt, parsed.rounds)
    return hmac.compare_digest(derived, parsed.derived)


SESSION_DURATION_SECONDS = int(os.environ.get("SESSION_DURATION_SECONDS", str(7 * 24 * 3600)))
SECRET_KEY = os.environ.get("SECRET_KEY", "")
AUTH_USERS = []

if SECRET_KEY:
    raw_users = os.environ.get("AUTH_USERS", "")
    for entry in raw_users.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise RuntimeError("AUTH_USERS entries must be username:hash")
        username, hashed_password = entry.split(":", 1)
        AUTH_USERS.append((username.strip(), hashed_password.strip()))


class AuthUser(NamedTuple):
    username: str


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), sha256).hexdigest()


def create_session_cookie(username: str) -> str:
    if not SECRET_KEY:
        raise RuntimeError("SECRET_KEY is required to create session cookies")
    expires = str(int(time.time()) + SESSION_DURATION_SECONDS)
    payload = f"{username}|{expires}"
    signature = _sign(payload)
    token = f"{payload}|{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(token).decode("utf-8")


def verify_session_cookie(cookie_value: str | None) -> AuthUser | None:
    if not cookie_value or not SECRET_KEY:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cookie_value.encode("utf-8")).decode("utf-8")
        username, expires, signature = decoded.rsplit("|", 2)
    except Exception:
        return None
    payload = f"{username}|{expires}"
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        if int(expires) < int(time.time()):
            return None
    except ValueError:
        return None
    for stored_username, _ in AUTH_USERS:
        if hmac.compare_digest(stored_username, username):
            return AuthUser(username=username)
    return None


def verify_password(username: str, password: str) -> bool:
    if not username or not password:
        return False
    for stored_username, stored_hash in AUTH_USERS:
        if hmac.compare_digest(stored_username, username):
            try:
                return pbkdf2_verify(password, stored_hash)
            except Exception:
                return False
    return False
