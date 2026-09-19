import os
import json
import imaplib
import smtplib
import email
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from email.utils import formatdate, make_msgid
from typing import Any, Optional
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("EmailMCP")

# ── Config ──────────────────────────────────────────────
# Accounts are configured via EMAIL_ACCOUNTS env var as JSON:
# [{"name": "work", "email": "you@example.com", "password": "xxx",
#   "imap_host": "imap.example.com", "imap_port": 993,
#   "smtp_host": "smtp.example.com", "smtp_port": 465}]
# imap_host / smtp_host default to AWS WorkMail (us-east-1) when omitted.

ACCOUNTS = {}

def _load_accounts():
    global ACCOUNTS
    raw = os.getenv("EMAIL_ACCOUNTS", "[]")
    try:
        accounts_list = json.loads(raw)
        for acc in accounts_list:
            name = acc.get("name", acc.get("email", "").split("@")[0])
            ACCOUNTS[name] = {
                "email": acc["email"],
                "password": acc["password"],
                "imap_host": acc.get("imap_host", "imap.mail.us-east-1.awsapps.com"),
                "imap_port": acc.get("imap_port", 993),
                "smtp_host": acc.get("smtp_host", "smtp.mail.us-east-1.awsapps.com"),
                "smtp_port": acc.get("smtp_port", 465),
            }
    except Exception as e:
        print(f"Error loading accounts: {e}")

_load_accounts()


def _get_account(account: str = "") -> dict:
    """Get account config by name. Defaults to first account."""
    if not ACCOUNTS:
        raise ValueError("No email accounts configured")
    if not account:
        return next(iter(ACCOUNTS.values()))
    if account in ACCOUNTS:
        return ACCOUNTS[account]
    # Try matching by email address
    for acc in ACCOUNTS.values():
        if acc["email"] == account:
            return acc
    raise ValueError(f"Account '{account}' not found. Available: {list(ACCOUNTS.keys())}")


def _decode_header_value(value):
    """Decode email header value."""
    if not value:
        return ""
    decoded_parts = decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def _connect_imap(acc: dict) -> imaplib.IMAP4_SSL:
    """Connect to IMAP server."""
    conn = imaplib.IMAP4_SSL(acc["imap_host"], acc["imap_port"])
    conn.login(acc["email"], acc["password"])
    return conn


def _quote_mailbox(mailbox: str) -> str:
    """IMAP 要求帶空白或特殊字元的 mailbox 名稱要加雙引號。"""
    if mailbox.startswith('"') and mailbox.endswith('"'):
        return mailbox
    if " " in mailbox or "(" in mailbox or ")" in mailbox:
        return f'"{mailbox}"'
    return mailbox


def _select(conn: imaplib.IMAP4_SSL, mailbox: str, readonly: bool = False) -> None:
    """Select a mailbox with proper quoting. Raises ValueError on failure."""
    status, data = conn.select(_quote_mailbox(mailbox), readonly=readonly)
    if status != "OK":
        detail = data[0].decode("utf-8", errors="replace") if data and data[0] else "unknown"
        raise ValueError(f"Failed to select mailbox '{mailbox}': {detail}")


SENT_MAILBOX_CANDIDATES = [
    "Sent Items",
    "Sent",
    "INBOX.Sent",
    "[Gmail]/Sent Mail",
]


def _save_to_sent(acc: dict, mime_msg) -> Optional[str]:
    """APPEND a sent message to the IMAP Sent folder so it shows up in the user's mail client.
    Returns the mailbox name on success, None on failure (never raises)."""
    try:
        conn = _connect_imap(acc)
    except Exception:
        return None
    try:
        raw = mime_msg.as_bytes()
        internaldate = imaplib.Time2Internaldate(time.time())
        for candidate in SENT_MAILBOX_CANDIDATES:
            try:
                status, _ = conn.append(
                    _quote_mailbox(candidate),
                    "(\\Seen)",
                    internaldate,
                    raw,
                )
                if status == "OK":
                    return candidate
            except Exception:
                continue
        return None
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _parse_message(msg) -> dict:
    """Parse email message to dict."""
    result = {
        "from": _decode_header_value(msg.get("From", "")),
        "to": _decode_header_value(msg.get("To", "")),
        "cc": _decode_header_value(msg.get("Cc", "")),
        "subject": _decode_header_value(msg.get("Subject", "")),
        "date": msg.get("Date", ""),
        "message_id": msg.get("Message-ID", ""),
    }

    # Get body
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    body = str(part.get_payload())
                break
            elif content_type == "text/html" and not body:
                try:
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    body = str(part.get_payload())
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
        except Exception:
            body = str(msg.get_payload())

    result["body"] = body

    # Check for attachments
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                filename = _decode_header_value(part.get_filename() or "unnamed")
                attachments.append({
                    "filename": filename,
                    "content_type": part.get_content_type(),
                    "size": len(part.get_payload(decode=True) or b""),
                })
    result["attachments"] = attachments

    return result


# ═══════════════════════════════════════════════════════
#  工具
# ═══════════════════════════════════════════════════════

@mcp.tool()
def email_list_accounts() -> list:
    """列出所有已設定的 email 帳號。"""
    return [{"name": name, "email": acc["email"]} for name, acc in ACCOUNTS.items()]


@mcp.tool()
def email_list_mailboxes(account: str = "") -> list:
    """列出帳號中的所有信箱資料夾。account 可用設定裡的名稱（如 'work'）或 email，留空用預設帳號。"""
    acc = _get_account(account)
    conn = _connect_imap(acc)
    try:
        status, mailboxes = conn.list()
        result = []
        for mb in mailboxes:
            decoded = mb.decode("utf-8", errors="replace")
            # Extract mailbox name from IMAP response
            parts = decoded.split('" ')
            if len(parts) >= 2:
                result.append(parts[-1].strip('"'))
            else:
                result.append(decoded)
        return result
    finally:
        conn.logout()


@mcp.tool()
def email_get_messages(account: str = "", mailbox: str = "INBOX", limit: int = 10) -> list:
    """取得最新的信件列表。account 留空用預設帳號。"""
    acc = _get_account(account)
    conn = _connect_imap(acc)
    try:
        _select(conn, mailbox, readonly=True)
        status, data = conn.search(None, "ALL")
        if status != "OK":
            return []

        msg_ids = data[0].split()
        if not msg_ids:
            return []

        # Get latest messages
        msg_ids = msg_ids[-limit:]
        msg_ids.reverse()

        results = []
        for msg_id in msg_ids:
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            parsed = _parse_message(msg)
            parsed["uid"] = msg_id.decode()
            # Truncate body for list view
            if len(parsed["body"]) > 200:
                parsed["body"] = parsed["body"][:200] + "..."
            results.append(parsed)

        return results
    finally:
        conn.logout()


@mcp.tool()
def email_get_message(uid: str, account: str = "", mailbox: str = "INBOX") -> dict:
    """讀取單封信件的完整內容。uid 從 email_get_messages 取得。"""
    acc = _get_account(account)
    conn = _connect_imap(acc)
    try:
        _select(conn, mailbox, readonly=True)
        status, msg_data = conn.fetch(uid.encode(), "(RFC822)")
        if status != "OK":
            return {"error": f"Failed to fetch message {uid}"}
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        parsed = _parse_message(msg)
        parsed["uid"] = uid
        return parsed
    finally:
        conn.logout()


@mcp.tool()
def email_get_unread(account: str = "", mailbox: str = "INBOX", limit: int = 20) -> list:
    """取得未讀信件。"""
    acc = _get_account(account)
    conn = _connect_imap(acc)
    try:
        _select(conn, mailbox, readonly=True)
        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            return []

        msg_ids = data[0].split()
        if not msg_ids:
            return []

        msg_ids = msg_ids[-limit:]
        msg_ids.reverse()

        results = []
        for msg_id in msg_ids:
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            parsed = _parse_message(msg)
            parsed["uid"] = msg_id.decode()
            if len(parsed["body"]) > 200:
                parsed["body"] = parsed["body"][:200] + "..."
            results.append(parsed)

        return results
    finally:
        conn.logout()


@mcp.tool()
def email_send(to: str, subject: str, body: str, account: str = "", cc: str = "", html: bool = False) -> dict:
    """寄送 email。account 留空用預設帳號。html=True 可寄 HTML 格式。"""
    acc = _get_account(account)

    msg = MIMEMultipart("alternative")
    msg["From"] = acc["email"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()

    if cc:
        msg["Cc"] = cc

    if html:
        msg.attach(MIMEText(body, "html", "utf-8"))
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL(acc["smtp_host"], acc["smtp_port"]) as smtp:
            smtp.login(acc["email"], acc["password"])
            recipients = [to]
            if cc:
                recipients.extend([addr.strip() for addr in cc.split(",")])
            smtp.send_message(msg, to_addrs=recipients)
    except Exception as e:
        return {"error": str(e)}

    saved_to = _save_to_sent(acc, msg)
    return {
        "success": True,
        "from": acc["email"],
        "to": to,
        "subject": subject,
        "saved_to_sent": saved_to,
    }


@mcp.tool()
def email_reply(uid: str, body: str, account: str = "", mailbox: str = "INBOX", reply_all: bool = False) -> dict:
    """回覆信件。uid 從 email_get_messages 取得。reply_all=True 回覆所有人。"""
    acc = _get_account(account)
    conn = _connect_imap(acc)
    try:
        _select(conn, mailbox, readonly=True)
        status, msg_data = conn.fetch(uid.encode(), "(RFC822)")
        if status != "OK":
            return {"error": f"Failed to fetch message {uid}"}
        raw = msg_data[0][1]
        original = email.message_from_bytes(raw)
    finally:
        conn.logout()

    # Build reply
    reply = MIMEMultipart("alternative")
    reply["From"] = acc["email"]

    # Reply to sender
    reply_to = original.get("Reply-To", original.get("From", ""))
    reply["To"] = reply_to

    if reply_all:
        cc_list = []
        for field in ["To", "Cc"]:
            val = original.get(field, "")
            if val:
                addrs = [a.strip() for a in val.split(",")]
                cc_list.extend([a for a in addrs if acc["email"] not in a])
        if cc_list:
            reply["Cc"] = ", ".join(cc_list)

    orig_subject = _decode_header_value(original.get("Subject", ""))
    if not orig_subject.lower().startswith("re:"):
        reply["Subject"] = f"Re: {orig_subject}"
    else:
        reply["Subject"] = orig_subject

    reply["In-Reply-To"] = original.get("Message-ID", "")
    reply["References"] = original.get("Message-ID", "")
    reply["Date"] = formatdate(localtime=True)
    reply["Message-ID"] = make_msgid()

    reply.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL(acc["smtp_host"], acc["smtp_port"]) as smtp:
            smtp.login(acc["email"], acc["password"])
            recipients = [reply_to]
            if reply.get("Cc"):
                recipients.extend([a.strip() for a in reply["Cc"].split(",")])
            smtp.send_message(reply, to_addrs=recipients)
    except Exception as e:
        return {"error": str(e)}

    saved_to = _save_to_sent(acc, reply)
    return {
        "success": True,
        "from": acc["email"],
        "to": reply_to,
        "subject": reply["Subject"],
        "saved_to_sent": saved_to,
    }


@mcp.tool()
def email_search(query: str, account: str = "", mailbox: str = "INBOX", limit: int = 20) -> list:
    """搜尋信件。query 支援 IMAP 搜尋語法，例如：
    - 搜主旨：SUBJECT "關鍵字"
    - 搜寄件人：FROM "someone@example.com"
    - 搜內文：BODY "關鍵字"
    - 搜日期之後：SINCE 15-Mar-2026
    - 組合：FROM "someone" SUBJECT "meeting"
    """
    acc = _get_account(account)
    conn = _connect_imap(acc)
    try:
        _select(conn, mailbox, readonly=True)
        # Try UTF-8 search first, fall back to ASCII
        try:
            status, data = conn.search("UTF-8", query)
        except Exception:
            status, data = conn.search(None, query)

        if status != "OK":
            return []

        msg_ids = data[0].split()
        if not msg_ids:
            return []

        msg_ids = msg_ids[-limit:]
        msg_ids.reverse()

        results = []
        for msg_id in msg_ids:
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            parsed = _parse_message(msg)
            parsed["uid"] = msg_id.decode()
            if len(parsed["body"]) > 200:
                parsed["body"] = parsed["body"][:200] + "..."
            results.append(parsed)

        return results
    finally:
        conn.logout()


@mcp.tool()
def email_delete(uid: str, account: str = "", mailbox: str = "INBOX") -> dict:
    """刪除信件（標記為已刪除並清除）。"""
    acc = _get_account(account)
    conn = _connect_imap(acc)
    try:
        _select(conn, mailbox)
        conn.store(uid.encode(), "+FLAGS", "\\Deleted")
        conn.expunge()
        return {"success": True, "deleted_uid": uid}
    finally:
        conn.logout()


@mcp.tool()
def email_mark_read(uid: str, account: str = "", mailbox: str = "INBOX") -> dict:
    """將信件標記為已讀。"""
    acc = _get_account(account)
    conn = _connect_imap(acc)
    try:
        _select(conn, mailbox)
        conn.store(uid.encode(), "+FLAGS", "\\Seen")
        return {"success": True, "uid": uid}
    finally:
        conn.logout()


@mcp.tool()
def email_get_count(account: str = "", mailbox: str = "INBOX") -> dict:
    """取得信箱中的信件數量（總數及未讀）。"""
    acc = _get_account(account)
    conn = _connect_imap(acc)
    try:
        status, data = conn.select(_quote_mailbox(mailbox), readonly=True)
        total = int(data[0].decode()) if status == "OK" else 0
        status, data = conn.search(None, "UNSEEN")
        unread = len(data[0].split()) if status == "OK" and data[0] else 0
        return {"account": acc["email"], "mailbox": mailbox, "total": total, "unread": unread}
    finally:
        conn.logout()


if __name__ == "__main__":
    mcp.run(transport="stdio")
