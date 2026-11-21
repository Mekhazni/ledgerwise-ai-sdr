"""Fetch only Ledgerwise-owned Gmail threads and parse replies as plain text."""
import base64
from datetime import datetime, timezone
from email.utils import getaddresses
import re
from urllib.parse import quote

from bs4 import BeautifulSoup
import httpx

from outreach_delivery import gmail_credentials, refresh_gmail_access


def plain_body(payload):
    if payload.get("filename"):
        return ""
    mime = payload.get("mimeType", "").lower()
    if mime.startswith("multipart/"):
        parts = payload.get("parts", [])
        if mime == "multipart/alternative":
            parts = sorted(parts, key=lambda part: part.get("mimeType") != "text/plain")
            return next((text for part in parts if (text := plain_body(part)).strip()), "")
        return "\n".join(text for part in parts if (text := plain_body(part)).strip())
    if mime not in {"text/plain", "text/html"}:
        return ""
    data = payload.get("body", {}).get("data", "")
    if not data:
        return ""
    text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    if mime == "text/html":
        soup = BeautifulSoup(text, "html.parser")
        for part in soup.select("script, style, blockquote, .gmail_quote"):
            part.decompose()
        text = soup.get_text("\n")
    return text


def current_reply_text(body):
    """Return the author's new text, stopping at common quoted-history markers."""
    lines = []
    for line in body.splitlines():
        if re.match(
            r"(?i)^\s*(?:on .+ wrote:|le .+ a [\u00e9\ufffd]crit\s*:|"
            r"-+\s*(?:original|forwarded) message\b|from:\s|>)", line
        ):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def clear_opt_out(body):
    for line in current_reply_text(body).splitlines():
        line = line.strip()
        if re.match(
            r"(?i)^(?:please\s+)?(?:unsubscribe(?:\s+me)?|stop(?:\s+(?:emailing|contacting|sending emails to)\s+me)?|"
            r"(?:i(?:'m| am)\s+)?not interested|remove me from (?:your|the) (?:list|mailing list))"
            r"(?:\s+please)?[.!]?(?:$|\s*[,.;!]\s+)",
            line,
        ):
            return True
    return False


def parse_reply(message, expected_thread, test_addresses, sender_email):
    if (message.get("threadId") != expected_thread or not message.get("id")
            or set(message.get("labelIds", [])) & {"SENT", "DRAFT", "SPAM", "TRASH"}):
        return None
    payload = message.get("payload", {})
    headers = {item["name"].lower(): item["value"] for item in payload.get("headers", [])}
    senders = [address.lower() for _, address in getaddresses([headers.get("from", "")]) if address]
    recipients = [address.lower() for _, address in getaddresses([headers.get("to", ""), headers.get("cc", "")]) if address]
    if (len(senders) != 1 or senders[0] not in test_addresses or sender_email not in recipients
            or not (headers.get("in-reply-to") or headers.get("references"))):
        return None
    body = plain_body(payload).strip()
    if not body:
        return None
    timestamp = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc)
    return {"gmail_message_id": message["id"], "sender": senders[0], "recipients": recipients,
            "subject": headers.get("subject", ""), "body": body, "received_at": timestamp.isoformat(),
            "message_id": headers.get("message-id"), "in_reply_to": headers.get("in-reply-to"),
            "references": headers.get("references"), "opt_out": clear_opt_out(body)}


async def sync_known_threads(threads, save_reply):
    settings = gmail_credentials()
    stats = dict(imported=0, duplicate=0, suppressed=0, skipped=0, failed=0)
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        token = await refresh_gmail_access(client, settings)
        for thread in threads:
            gmail_id = thread["gmail_thread_id"]
            addresses = {message["actual_recipient"].strip().lower() for message in thread["messages"]
                         if message.get("actual_recipient")}
            if not addresses:
                continue
            try:
                response = await client.get("https://gmail.googleapis.com/gmail/v1/users/me/threads/"
                    + quote(gmail_id, safe=""), params={"format": "full"},
                    headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                data = response.json()
                if data.get("id") != gmail_id:
                    raise ValueError("Unexpected Gmail thread")
            except httpx.HTTPStatusError as error:
                if error.response.status_code in {401, 403}:
                    raise
                stats["failed"] += 1
                continue
            except (httpx.RequestError, ValueError, AttributeError):
                stats["failed"] += 1
                continue
            messages = data.get("messages", [])
            if not isinstance(messages, list):
                stats["failed"] += 1
                continue
            sent_ids = {message.get("gmail_message_id") for message in thread["messages"]} - {None}
            reference_ids = {
                item["value"].strip() for message in messages
                if message.get("id") in sent_ids and message.get("threadId") == gmail_id
                for item in message.get("payload", {}).get("headers", [])
                if item["name"].lower() == "message-id"
            }
            for message in messages:
                if "SENT" in message.get("labelIds", []):
                    continue
                try:
                    reply = parse_reply(message, gmail_id, addresses, settings["GMAIL_SENDER_EMAIL"].lower())
                except (KeyError, TypeError, ValueError, OverflowError, OSError):
                    reply = None
                if reply is None:
                    stats["skipped"] += 1
                    continue
                refs = set(re.findall(r"<[^<>\s]+>", " ".join(
                    reply.get(key) or "" for key in ("in_reply_to", "references"))))
                if not refs.intersection(reference_ids):
                    stats["skipped"] += 1
                    continue
                outcome = await save_reply(thread["id"], gmail_id, reply)
                if outcome in {"imported", "duplicate", "suppressed"} and reply.get("message_id"):
                    reference_ids.add(reply["message_id"].strip())
                stats[outcome if outcome in stats else "failed"] += 1
    return stats
