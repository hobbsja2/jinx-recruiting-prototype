import os
import smtplib
import json
from email.message import EmailMessage
from typing import Optional

import httpx


def _send_via_sendgrid(subject: str, body: str, recipient: str, sender: Optional[str]) -> bool:
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        return False
    from_addr = sender or os.environ.get("SENDGRID_FROM") or os.environ.get("SMTP_USER")
    if not from_addr:
        return False
    url = "https://api.sendgrid.com/v3/mail/send"
    payload = {
        "personalizations": [{"to": [{"email": recipient}]}],
        "from": {"email": from_addr},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=20)
        return resp.status_code in (200, 202)
    except Exception as exc:
        print(f"SendGrid send failed: {type(exc).__name__}: {exc}")
        return False


def _send_via_graph(subject: str, body: str, recipient: str, sender: Optional[str]) -> bool:
    # Prefer an explicit token if provided in the env (delegated flow)
    token = os.environ.get("MS_GRAPH_TOKEN")
    if token:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = "https://graph.microsoft.com/v1.0/me/sendMail"
        message = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
            },
            "saveToSentItems": "true",
        }
        try:
            resp = httpx.post(url, headers=headers, json=message, timeout=20)
            return resp.status_code in (200, 202)
        except Exception as exc:
            print(f"Microsoft Graph send failed: {type(exc).__name__}: {exc}")
            return False

    # Otherwise try client credentials flow (app-only) if configured. Requires application Mail.Send permission
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID")
    client_secret = os.environ.get("MS_GRAPH_CLIENT_SECRET")
    tenant = os.environ.get("MS_GRAPH_TENANT_ID")
    send_as = sender or os.environ.get("MS_GRAPH_SEND_AS") or os.environ.get("SMTP_USER")
    if not (client_id and client_secret and tenant and send_as):
        return False

    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    try:
        tokresp = httpx.post(token_url, data=data, timeout=20)
        tokresp.raise_for_status()
        access = tokresp.json().get("access_token")
        if not access:
            print("Graph token response missing access_token", tokresp.text)
            return False
        headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}
        # Use app-only to send as a user: POST /users/{send_as}/sendMail requires Mail.Send application permission
        url = f"https://graph.microsoft.com/v1.0/users/{send_as}/sendMail"
        message = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": recipient}}],
                "from": {"emailAddress": {"address": send_as}},
            },
            "saveToSentItems": "true",
        }
        resp = httpx.post(url, headers=headers, json=message, timeout=20)
        return resp.status_code in (200, 202)
    except Exception as exc:
        print(f"Microsoft Graph (client credentials) failed: {type(exc).__name__}: {exc}")
        return False


def send_email(subject: str, body: str, recipient: str, sender: Optional[str] = None) -> bool:
    """Send an email using the first available provider: SendGrid, Microsoft Graph, then SMTP.

    Returns True on success, False on failure.
    """
    # Try SendGrid if configured
    if os.environ.get("SENDGRID_API_KEY"):
        ok = _send_via_sendgrid(subject, body, recipient, sender)
        if ok:
            return True
    # Try Microsoft Graph if configured
    if os.environ.get("MS_GRAPH_TOKEN"):
        ok = _send_via_graph(subject, body, recipient, sender)
        if ok:
            return True

    # Fallback to SMTP
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    use_tls = os.environ.get("SMTP_USE_TLS", "1") in ("1", "true", "True")
    from_addr = sender or user
    if not host or not from_addr:
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            if use_tls:
                s.starttls()
            if user and password:
                s.login(user, password)
            s.send_message(msg)
        return True
    except Exception as exc:
        import sys, traceback
        traceback.print_exc()
        print(f"SMTP send failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
