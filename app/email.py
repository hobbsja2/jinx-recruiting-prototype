import base64
import os
import smtplib
import json
from email.message import EmailMessage
from typing import Optional

import httpx


def _parse_addrs(value) -> list[str]:
    """Normalize a comma/semicolon-separated string (or list) into an address list."""
    if not value:
        return []
    if isinstance(value, str):
        parts = value.replace(";", ",").split(",")
    else:
        parts = list(value)
    return [p.strip() for p in parts if p and p.strip()]


def _send_via_sendgrid(subject: str, body: str, recipient: str, sender: Optional[str], cc: Optional[list] = None, attachments: Optional[list] = None) -> bool:
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        return False
    from_addr = sender or os.environ.get("SENDGRID_FROM") or os.environ.get("SMTP_USER")
    if not from_addr:
        return False
    url = "https://api.sendgrid.com/v3/mail/send"
    personalization = {"to": [{"email": recipient}]}
    if cc:
        personalization["cc"] = [{"email": a} for a in cc]
    payload = {
        "personalizations": [personalization],
        "from": {"email": from_addr},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    if attachments:
        payload["attachments"] = [{
            "content": base64.b64encode(content).decode("ascii"),
            "filename": filename,
            "type": mimetype,
            "disposition": "attachment",
        } for filename, content, mimetype in attachments]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=20)
        return resp.status_code in (200, 202)
    except Exception as exc:
        print(f"SendGrid send failed: {type(exc).__name__}: {exc}")
        return False


def _send_via_graph(subject: str, body: str, recipient: str, sender: Optional[str], cc: Optional[list] = None, attachments: Optional[list] = None) -> bool:
    cc_recipients = [{"emailAddress": {"address": a}} for a in (cc or [])]
    graph_attachments = [{
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": filename,
        "contentType": mimetype,
        "contentBytes": base64.b64encode(content).decode("ascii"),
    } for filename, content, mimetype in (attachments or [])]
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
                "ccRecipients": cc_recipients,
                "attachments": graph_attachments,
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
                "ccRecipients": cc_recipients,
                "attachments": graph_attachments,
                "from": {"emailAddress": {"address": send_as}},
            },
            "saveToSentItems": "true",
        }
        resp = httpx.post(url, headers=headers, json=message, timeout=20)
        return resp.status_code in (200, 202)
    except Exception as exc:
        print(f"Microsoft Graph (client credentials) failed: {type(exc).__name__}: {exc}")
        return False


def send_email(subject: str, body: str, recipient: str, sender: Optional[str] = None, cc=None, attachments: Optional[list] = None) -> bool:
    """Send an email using the first available provider: SendGrid, Microsoft Graph, then SMTP.

    `cc` may be a comma/semicolon-separated string or a list of addresses.
    `attachments` is an optional list of (filename, content_bytes, mimetype) tuples.
    Returns True on success, False on failure.
    """
    cc_list = _parse_addrs(cc)
    attachments = attachments or []
    # Try SendGrid if configured
    if os.environ.get("SENDGRID_API_KEY"):
        ok = _send_via_sendgrid(subject, body, recipient, sender, cc_list, attachments)
        if ok:
            return True
    # Try Microsoft Graph if configured
    if os.environ.get("MS_GRAPH_TOKEN"):
        ok = _send_via_graph(subject, body, recipient, sender, cc_list, attachments)
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
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg.set_content(body)
    for filename, content, mimetype in attachments:
        maintype, _, subtype = mimetype.partition("/")
        msg.add_attachment(content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=filename)

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
