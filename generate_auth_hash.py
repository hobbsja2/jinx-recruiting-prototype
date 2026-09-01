import base64
import hashlib
import secrets
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: python generate_auth_hash.py <username>")

username = sys.argv[1].strip()
if not username:
    raise SystemExit("Username must not be empty")

password = input(f"Password for {username}: ")
if not password:
    raise SystemExit("Password must not be empty")

salt = secrets.token_bytes(16)
rounds = 200_000
derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)

salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
derived_b64 = base64.urlsafe_b64encode(derived).decode("ascii").rstrip("=")
print(f"{username}:pbkdf2_sha256${rounds}${salt_b64}${derived_b64}")
