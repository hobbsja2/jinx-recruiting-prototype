from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import getaddresses
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import msal
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from .models import MicrosoftConnection

GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
GRAPH_SCOPES = ["https://graph.microsoft.com/Mail.Send"]
DEFAULT_SENDER = "jinxhsdrecruiting@outlook.com"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_SUBJECT_LENGTH = 998
MAX_BODY_LENGTH = 100_000


class OutlookError(RuntimeError):
    """Safe, user-displayable Outlook integration error."""


class OutlookReconnectRequired(OutlookError):
    pass


@dataclass(frozen=True)
class OutlookStatus:
    configured: bool
    connected: bool
    account_email: str = ""
    detail: str = ""


def expected_sender() -> str:
    return os.environ.get("OUTLOOK_SENDER_EMAIL", DEFAULT_SENDER).strip().lower()


def is_configured() -> bool:
    required = ("OUTLOOK_CLIENT_ID", "OUTLOOK_CLIENT_SECRET", "OUTLOOK_REDIRECT_URI", "OUTLOOK_TOKEN_ENCRYPTION_KEY")
    return all(os.environ.get(name, "").strip() for name in required)


def _connection(db: Session) -> MicrosoftConnection | None:
    return db.get(MicrosoftConnection, 1)


def _fernet() -> Fernet:
    key = os.environ.get("OUTLOOK_TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise OutlookError("Outlook token encryption is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise OutlookError("Outlook token encryption is misconfigured.") from exc


def _cache(connection: MicrosoftConnection | None = None) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if connection and connection.encrypted_cache:
        try:
            serialized = _fernet().decrypt(connection.encrypted_cache.encode("ascii")).decode("utf-8")
            cache.deserialize(serialized)
        except (InvalidToken, ValueError, UnicodeError) as exc:
            raise OutlookReconnectRequired("The saved Outlook connection cannot be read. Reconnect Outlook.") from exc
    return cache


def _client(cache: msal.SerializableTokenCache) -> msal.ConfidentialClientApplication:
    if not is_configured():
        raise OutlookError("Outlook OAuth is not configured for this environment.")
    return msal.ConfidentialClientApplication(
        os.environ["OUTLOOK_CLIENT_ID"],
        authority=os.environ.get("OUTLOOK_AUTHORITY", "https://login.microsoftonline.com/consumers"),
        client_credential=os.environ["OUTLOOK_CLIENT_SECRET"],
        token_cache=cache,
    )


def _save_cache(connection: MicrosoftConnection, cache: msal.SerializableTokenCache) -> None:
    connection.encrypted_cache = _fernet().encrypt(cache.serialize().encode("utf-8")).decode("ascii")
    connection.updated_at = datetime.utcnow()


def status(db: Session) -> OutlookStatus:
    if not is_configured():
        return OutlookStatus(False, False, detail="Azure OAuth settings are incomplete.")
    connection = _connection(db)
    if not connection:
        return OutlookStatus(True, False, detail="Connect the recruiting Outlook account.")
    if connection.account_email.lower() != expected_sender():
        return OutlookStatus(True, False, connection.account_email, "The saved Outlook account does not match the configured sender. Reconnect Outlook.")
    try:
        _cache(connection)
    except OutlookError as exc:
        return OutlookStatus(True, False, connection.account_email, str(exc))
    return OutlookStatus(True, True, connection.account_email, "Ready to send with Microsoft Graph.")


def start_authorization() -> dict:
    flow = _client(_cache()).initiate_auth_code_flow(
        scopes=GRAPH_SCOPES,
        redirect_uri=os.environ["OUTLOOK_REDIRECT_URI"],
        prompt="select_account",
    )
    if "auth_uri" not in flow:
        raise OutlookError("Microsoft did not return an authorization URL.")
    return flow


def protect_authorization_flow(flow: dict) -> str:
    """Encrypt the MSAL flow before putting it in the signed session cookie."""
    try:
        serialized = json.dumps(flow, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OutlookError("Microsoft returned an invalid authorization session.") from exc
    return _fernet().encrypt(serialized).decode("ascii")


def unprotect_authorization_flow(value: str) -> dict:
    try:
        flow = json.loads(_fernet().decrypt(value.encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutlookError("The Outlook connection expired. Start it again.") from exc
    if not isinstance(flow, dict):
        raise OutlookError("The Outlook connection expired. Start it again.")
    return flow


def complete_authorization(db: Session, flow: dict, response: dict[str, str]) -> MicrosoftConnection:
    cache = _cache()
    try:
        result = _client(cache).acquire_token_by_auth_code_flow(flow, response)
    except ValueError as exc:
        raise OutlookError("Microsoft rejected the authorization response. Start the connection again.") from exc
    if "access_token" not in result:
        code = str(result.get("error", "authorization_failed"))
        raise OutlookError(f"Microsoft authorization failed ({code}).")

    claims = result.get("id_token_claims") or {}
    email = str(claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    if email != expected_sender():
        raise OutlookError(f"Connect {expected_sender()}, not {email or 'a different Microsoft account'}.")

    accounts = _client(cache).get_accounts(username=email)
    account = next((item for item in accounts if str(item.get("username", "")).lower() == email), None)
    home_account_id = str((account or {}).get("home_account_id", ""))
    if not account or not home_account_id:
        raise OutlookError("Microsoft authorized the account but did not return a reusable account session.")

    connection = _connection(db)
    if connection is None:
        connection = MicrosoftConnection(id=1, account_email=email, home_account_id=home_account_id, encrypted_cache="")
        db.add(connection)
    else:
        connection.account_email = email
        connection.home_account_id = home_account_id
    connection.connected_at = datetime.utcnow()
    _save_cache(connection, cache)
    return connection


def disconnect(db: Session) -> None:
    connection = _connection(db)
    if connection:
        db.delete(connection)


def recipients(raw: str) -> list[str]:
    parsed: list[str] = []
    for _, address in getaddresses([raw.replace(";", ",")]):
        normalized = address.strip().lower()
        if normalized and EMAIL_RE.fullmatch(normalized) and normalized not in parsed:
            parsed.append(normalized)
    if not parsed:
        raise OutlookError("Enter at least one valid recipient email address.")
    if len(parsed) > 10:
        raise OutlookError("Send to no more than 10 recipients at a time.")
    return parsed


def _graph_error(exc: HTTPError) -> OutlookError:
    code = "GraphError"
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or code)
    except (ValueError, UnicodeError):
        pass
    if exc.code in {401, 403}:
        return OutlookReconnectRequired(f"Microsoft rejected the saved Outlook permission ({code}). Reconnect Outlook.")
    return OutlookError(f"Microsoft Graph could not accept the message ({code}, HTTP {exc.code}).")


def send_mail(db: Session, recipient_text: str, subject: str, body: str) -> str:
    connection = _connection(db)
    if not connection or connection.account_email.lower() != expected_sender():
        raise OutlookReconnectRequired("Connect the configured Outlook account before sending email.")
    cache = _cache(connection)
    client = _client(cache)
    accounts = client.get_accounts(username=connection.account_email)
    account = next(
        (
            item
            for item in accounts
            if item.get("home_account_id") == connection.home_account_id
            and str(item.get("username", "")).lower() == connection.account_email.lower()
        ),
        None,
    )
    if not account:
        raise OutlookReconnectRequired("The configured Outlook account session is missing. Reconnect Outlook.")
    result = client.acquire_token_silent(GRAPH_SCOPES, account=account)
    if not result or "access_token" not in result:
        raise OutlookReconnectRequired("Outlook authorization expired or was revoked. Reconnect Outlook.")
    if cache.has_state_changed:
        _save_cache(connection, cache)
        db.flush()

    addresses = recipients(recipient_text)
    clean_subject = subject.strip()
    if not clean_subject:
        raise OutlookError("Enter an email subject.")
    if len(clean_subject) > MAX_SUBJECT_LENGTH:
        raise OutlookError(f"Keep the email subject under {MAX_SUBJECT_LENGTH + 1} characters.")
    if not body.strip():
        raise OutlookError("Enter an email message.")
    if len(body) > MAX_BODY_LENGTH:
        raise OutlookError(f"Keep the email message under {MAX_BODY_LENGTH + 1:,} characters.")

    payload = {
        "message": {
            "subject": clean_subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": address}} for address in addresses],
        },
        "saveToSentItems": True,
    }
    request = Request(
        GRAPH_SEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {result['access_token']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=25) as response:
            if response.status != 202:
                raise OutlookError(f"Microsoft Graph returned unexpected HTTP {response.status}.")
            return response.headers.get("request-id", "")
    except HTTPError as exc:
        raise _graph_error(exc) from exc
    except (URLError, TimeoutError) as exc:
        raise OutlookError("Microsoft Graph could not be reached. Delivery is unknown; check Sent Items before retrying.") from exc
