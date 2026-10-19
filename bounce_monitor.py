"""
Surveille Gmail (IMAP) pour les bounces / adresses introuvables.
Si une candidature a rebondi → notifie Telegram avec l'URL de l'offre.
"""
from __future__ import annotations

import email
import imaplib
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from config import (
    APPLICATIONS_SENT_PATH,
    SMTP_PASSWORD,
    SMTP_USER,
)

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
BOUNCE_SEEN_PATH = Path("bounces_processed.json")

BOUNCE_FROM_HINTS = (
    "mailer-daemon@",
    "postmaster@",
    "mail-delivery-subsystem",
    "microsoftexchange",
)

BOUNCE_SUBJECT_HINTS = (
    "undeliverable",
    "delivery status",
    "failure",
    "adresse introuvable",
    "mail delivery failed",
    "returned mail",
    "delivery incomplete",
    "couldn't be delivered",
    "non remis",
)

FAILED_EMAIL_PATTERNS = [
    r"n['’]est pas parvenu[e]?\s+[àa]\s+([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
    r"Your message to\s+([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\s+couldn",
    r"wasn['’]t found at\s+([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",  # needs local part nearby
    r"Address rejected[:\s]+.*?([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
    r"Final-Recipient:\s*rfc822;\s*([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
    r"Original-Recipient:\s*rfc822;\s*([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
    r"<([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>\s*(?:wasn|couldn|introuvable|rejected)",
    r"message to\s+([a-zA-Z0-9_.+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
]


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def notify_telegram(text: str) -> bool:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        print("[!] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants — bounce enregistré sans Telegram")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3500]}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return True
    except Exception as e:
        print(f"[!] Telegram error: {e}")
        return False


def _decode_mime(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _get_body(msg: email.message.Message) -> str:
    texts: List[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype in ("text/plain", "text/html") and "attachment" not in disp.lower():
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    texts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    continue
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            texts.append(payload.decode(charset, errors="replace"))
        except Exception:
            texts.append(str(msg.get_payload()))
    return "\n".join(texts)


def extract_failed_emails(text: str) -> List[str]:
    found: List[str] = []
    seen: Set[str] = set()
    for pat in FAILED_EMAIL_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            val = m.group(1).strip().lower()
            # Pattern "wasn't found at domain" → pas un email complet
            if "@" not in val:
                # Cherche recrutement@domain dans le texte
                for m2 in re.finditer(
                    rf"([a-zA-Z0-9_.+\-]+)@{re.escape(val)}",
                    text,
                    flags=re.IGNORECASE,
                ):
                    full = f"{m2.group(1).lower()}@{val}"
                    if full not in seen:
                        seen.add(full)
                        found.append(full)
                continue
            if val not in seen and "mailer-daemon" not in val and "googlemail" not in val:
                seen.add(val)
                found.append(val)
    return found


def is_bounce_message(from_addr: str, subject: str, body: str) -> bool:
    f = from_addr.lower()
    s = subject.lower()
    b = body.lower()
    if any(h in f for h in BOUNCE_FROM_HINTS):
        return True
    if any(h in s for h in BOUNCE_SUBJECT_HINTS):
        return True
    if "adresse introuvable" in b or "address rejected" in b or "couldn't be delivered" in b:
        return True
    if "n'est pas parvenu" in b or "n’est pas parvenu" in b:
        return True
    return False


def load_applications() -> List[Dict[str, Any]]:
    data = _load_json(Path(APPLICATIONS_SENT_PATH), [])
    return data if isinstance(data, list) else []


def save_applications(apps: List[Dict[str, Any]]) -> None:
    _save_json(Path(APPLICATIONS_SENT_PATH), apps)


def find_apps_for_email(apps: List[Dict[str, Any]], failed_email: str) -> List[Dict[str, Any]]:
    fe = failed_email.lower().strip()
    matches = []
    for app in apps:
        if not isinstance(app, dict):
            continue
        rh = (app.get("rh_email") or "").lower().replace(" (suggéré)", "").strip()
        if rh == fe and app.get("status") in ("sent", "bounced", ""):
            matches.append(app)
    return matches


def mark_bounced(apps: List[Dict[str, Any]], failed_email: str) -> int:
    fe = failed_email.lower().strip()
    n = 0
    for app in apps:
        if not isinstance(app, dict):
            continue
        rh = (app.get("rh_email") or "").lower().replace(" (suggéré)", "").strip()
        if rh == fe and app.get("status") == "sent":
            app["status"] = "bounced"
            app["bounced_at"] = datetime.now(timezone.utc).isoformat()
            n += 1
    return n


def check_bounces(limit: int = 40) -> Dict[str, Any]:
    """
    Lit les derniers mails Gmail, détecte les bounces,
    marque les candidatures et envoie les URLs sur Telegram.
    """
    user = SMTP_USER
    password = SMTP_PASSWORD
    if not user or not password:
        return {"ok": False, "error": "SMTP_USER / SMTP_PASSWORD manquants pour IMAP"}

    processed: Set[str] = set(_load_json(BOUNCE_SEEN_PATH, []))
    apps = load_applications()
    notifications: List[Dict[str, Any]] = []
    newly_processed: List[str] = []

    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        mail.login(user, password)
        mail.select("INBOX")

        # Limiter aux mails récents (évite SEARCH ALL trop gros)
        from datetime import timedelta

        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d-%b-%Y")
        ids: List[bytes] = []
        for criteria in (
            f'(SINCE {since} FROM "mailer-daemon")',
            f'(SINCE {since} FROM "postmaster")',
            f'(SINCE {since} SUBJECT "undeliverable")',
            f'(SINCE {since} SUBJECT "Adresse introuvable")',
            f'(SINCE {since} SUBJECT "Delivery Status")',
            f'(SINCE {since} SUBJECT "Failure")',
        ):
            status, data = mail.search(None, criteria)
            if status == "OK" and data and data[0]:
                ids.extend(data[0].split())

        # Dédupliquer IDs
        uniq: List[bytes] = []
        seen_ids: Set[bytes] = set()
        for i in ids:
            if i not in seen_ids:
                seen_ids.add(i)
                uniq.append(i)
        ids = uniq[-limit:]

        if not ids:
            return {
                "ok": True,
                "bounces_notified": 0,
                "notifications": [],
                "message": "Aucun bounce récent (7 jours)",
            }

        for msg_id in reversed(ids):
            mid = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
            uid_key = f"imap-{mid}"
            if uid_key in processed:
                continue

            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            from_addr = _decode_mime(msg.get("From"))
            subject = _decode_mime(msg.get("Subject"))
            body = _get_body(msg)
            message_id = (msg.get("Message-ID") or uid_key).strip()

            if message_id in processed:
                newly_processed.append(uid_key)
                continue

            if not is_bounce_message(from_addr, subject, body):
                continue

            failed_emails = extract_failed_emails(f"{subject}\n{body}")
            if not failed_emails:
                # Fallback : croiser tous les rh_email connus présents dans le corps
                for app in apps:
                    rh = (app.get("rh_email") or "").lower().strip()
                    if rh and rh in body.lower():
                        failed_emails.append(rh)
                failed_emails = list(dict.fromkeys(failed_emails))

            if not failed_emails:
                newly_processed.append(message_id)
                newly_processed.append(uid_key)
                continue

            notified_keys: Set[str] = set()
            for fe in failed_emails:
                matched = find_apps_for_email(apps, fe)
                mark_bounced(apps, fe)
                if not matched:
                    key = f"unmatched:{fe}"
                    if key in notified_keys:
                        continue
                    notified_keys.add(key)
                    text = (
                        f"⚠️ Bounce email (sans offre liée)\n"
                        f"Adresse rejetée: {fe}\n"
                        f"Sujet: {subject[:120]}"
                    )
                    tg = notify_telegram(text)
                    notifications.append(
                        {"failed_email": fe, "job_url": None, "telegram": tg, "matched": False}
                    )
                    continue

                # Une notif par URL unique
                seen_urls: Set[str] = set()
                for app in matched:
                    job_url = app.get("job_url") or "N/A"
                    if job_url in seen_urls:
                        continue
                    seen_urls.add(job_url)
                    key = f"{fe}|{job_url}"
                    if key in notified_keys:
                        continue
                    notified_keys.add(key)

                    title = app.get("title") or "?"
                    company = app.get("company") or "?"
                    text = (
                        f"❌ Candidature non livrée (email introuvable)\n"
                        f"Poste: {title}\n"
                        f"Entreprise: {company}\n"
                        f"Email RH: {fe}\n"
                        f"URL: {job_url}"
                    )
                    tg = notify_telegram(text)
                    notifications.append(
                        {
                            "failed_email": fe,
                            "title": title,
                            "company": company,
                            "job_url": job_url,
                            "telegram": tg,
                            "matched": True,
                        }
                    )
                    print(f"[+] Bounce → Telegram: {fe} | {job_url}")

            newly_processed.append(message_id)
            newly_processed.append(uid_key)

    finally:
        try:
            mail.logout()
        except Exception:
            pass

    if newly_processed:
        processed.update(newly_processed)
        _save_json(BOUNCE_SEEN_PATH, sorted(processed))
    save_applications(apps)

    return {
        "ok": True,
        "bounces_notified": len(notifications),
        "notifications": notifications,
        "message": f"{len(notifications)} bounce(s) traité(s)",
    }


if __name__ == "__main__":
    result = check_bounces()
    print(json.dumps(result, indent=2, ensure_ascii=False))
