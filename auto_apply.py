"""
Module 5 : génération de lettres de motivation + envoi automatique (Gmail SMTP).
Dry-run par défaut. Envoi réel : python auto_apply.py --send
"""
from __future__ import annotations

import argparse
import json
import re
import smtplib
import time
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional

import ollama

from config import (
    APPLICATIONS_SENT_PATH,
    AUTO_APPLY_DELAY_SEC,
    AUTO_APPLY_MAX,
    AUTO_APPLY_SEND,
    COVER_LETTERS_DIR,
    MIN_APPLY_SCORE,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    PDF_PATH,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
)
from rh_finder import KNOWN_COMPANY_DOMAINS

# Domaines / boîtes clairement inventés par le scraper (ne jamais envoyer)
BLOCKED_EMAIL_PATTERNS = [
    r"@entrepriserecruteuse\.com$",
    r"@entreprisehellowork\.com$",
    r"@entreprisewttj\.com$",
    r"@recruteurmarocannonces\.com$",
    r"@entrepriseemploi\.?ma\.com$",
    r"@bostonconsultinggroupbcg\.com$",
    r"@amarisconsultingpartofmantu\.com$",
    r"@viessmannclimate\.com$",
]


def clean_email(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    email = raw.strip()
    email = re.sub(r"\s*\(suggéré\)\s*", "", email, flags=re.IGNORECASE).strip()
    email = email.strip(".,;:()[]<>\"'")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return None
    return email.lower()


def is_email_trusted(job: Dict[str, Any], email: str) -> bool:
    """N'autorise que les emails fiables (offre / Hunter / haute confiance)."""
    raw = (job.get("rh_email") or "")
    source = (job.get("rh_source") or "").lower()
    confidence = (job.get("confidence") or "").lower()

    for pat in BLOCKED_EMAIL_PATTERNS:
        if re.search(pat, email, re.IGNORECASE):
            return False

    # Jamais les patterns inventés marqués suggérés
    if "suggéré" in raw.lower() or "suggere" in raw.lower():
        return False
    if "pattern standard" in source:
        return False
    if "moyenne" in confidence and "hunter" not in source:
        return False

    # Accepté : mention dans l'offre, Hunter, haute confiance
    if "mentionné" in source or "mentionne" in source or "directement" in source:
        return True
    if "hunter" in source:
        return True
    if "domaine entreprise connu" in source or "bonne" in confidence:
        domain = email.split("@", 1)[-1]
        known_domains = set(KNOWN_COMPANY_DOMAINS.values())
        local = email.split("@", 1)[0]
        if domain in known_domains and any(
            k in local for k in ("recrut", "rh", "talent", "career", "job", "hr", "emploi")
        ):
            return True
    if "haute" in confidence or confidence.startswith("9") or confidence.startswith("8") or confidence.startswith("100"):
        return True

    # Confidence numérique type "95%"
    conf_match = re.search(r"(\d+)", confidence)
    if conf_match and int(conf_match.group(1)) >= 70 and "hunter" in source:
        return True

    return False


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_sent_urls() -> set:
    data = load_json(Path(APPLICATIONS_SENT_PATH), [])
    if isinstance(data, list):
        urls = set()
        for item in data:
            if isinstance(item, str):
                urls.add(item)
            elif isinstance(item, dict) and item.get("job_url"):
                urls.add(item["job_url"])
        return urls
    return set()


def append_sent_record(record: Dict[str, Any]) -> None:
    path = Path(APPLICATIONS_SENT_PATH)
    data = load_json(path, [])
    if not isinstance(data, list):
        data = []
    data.append(record)
    save_json(path, data)


def generate_cover_letter(
    client: ollama.Client,
    profile: Dict[str, Any],
    job: Dict[str, Any],
) -> str:
    skills = ", ".join(profile.get("skills") or [])
    roles = ", ".join(profile.get("target_roles") or [])
    name = profile.get("full_name") or "ZAOUI ANASS"
    phone = profile.get("phone") or ""
    email = profile.get("email") or SMTP_FROM
    bio = profile.get("short_bio") or ""

    system_prompt = (
        "Tu rédiges des lettres de motivation professionnelles en français. "
        "Style clair, direct, sans fioritures. Pas de markdown, pas de titre. "
        "Exactement 3 à 4 courts paragraphes + formule de politesse + signature."
    )
    user_prompt = (
        f"Rédige une lettre de motivation pour ce poste.\n\n"
        f"CANDIDAT : {name}\n"
        f"Email : {email}\n"
        f"Téléphone : {phone}\n"
        f"Profil : {bio}\n"
        f"Compétences : {skills}\n"
        f"Rôles cibles : {roles}\n\n"
        f"OFFRE :\n"
        f"Poste : {job.get('title')}\n"
        f"Entreprise : {job.get('company')}\n"
        f"Lieu : {job.get('location')}\n"
        f"URL : {job.get('job_url')}\n"
        f"Contexte match : {job.get('evaluation_summary', '')}\n\n"
        "Consignes :\n"
        "- Tutoiement interdit, vouvoiement.\n"
        "- Mentionne 2-3 compétences pertinentes pour CE poste.\n"
        "- Indique que le CV est joint.\n"
        f"- Termine par : Cordialement, puis {name}, {phone}, {email}\n"
        "- Ne invente pas d'années d'expérience.\n"
        "LETTRE :"
    )

    response = client.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        options={"temperature": 0.4},
    )
    letter = (response["message"]["content"] or "").strip()
    # Nettoyage balises éventuelles
    letter = re.sub(r"^```(?:\w+)?\s*", "", letter)
    letter = re.sub(r"\s*```$", "", letter)
    return letter.strip()


def build_message(
    *,
    to_email: str,
    subject: str,
    body: str,
    cv_path: Path,
) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Reply-To"] = SMTP_FROM
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(cv_path, "rb") as f:
        part = MIMEApplication(f.read(), Name=cv_path.name)
    part["Content-Disposition"] = f'attachment; filename="{cv_path.name}"'
    msg.attach(part)
    return msg


def send_email(msg: MIMEMultipart) -> None:
    if not SMTP_PASSWORD:
        raise RuntimeError("SMTP_PASSWORD manquant dans .env")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def select_eligible_jobs(jobs: List[Dict[str, Any]], sent_urls: set) -> tuple[List[Dict], List[str]]:
    eligible = []
    skipped_reasons = []
    for job in jobs:
        score = int(job.get("match_score") or 0)
        url = (job.get("job_url") or "").strip()
        if score < MIN_APPLY_SCORE:
            continue
        if url and url in sent_urls:
            skipped_reasons.append(f"déjà envoyé: {job.get('company')} — {job.get('title')}")
            continue
        email = clean_email(job.get("rh_email"))
        if not email:
            skipped_reasons.append(f"email invalide: {job.get('company')}")
            continue
        if not is_email_trusted(job, email):
            skipped_reasons.append(f"email non fiable: {email} ({job.get('company')})")
            continue
        job = dict(job)
        job["_clean_email"] = email
        eligible.append(job)
    # Meilleurs scores d'abord
    eligible.sort(key=lambda j: -int(j.get("match_score") or 0))
    return eligible, skipped_reasons


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE)
    return text.strip("_")[:80] or "lettre"


def run(send: bool = False, max_apps: Optional[int] = None) -> Dict[str, Any]:
    """Exécute candidatures auto. Retourne un résumé JSON (utile pour l'API / n8n)."""
    print("=" * 60)
    print(" MODULE 5 : CANDIDATURES AUTO (CV + LETTRE)")
    print(f" Mode : {'ENVOI RÉEL' if send else 'DRY-RUN (aucune email envoyé)'}")
    print("=" * 60)

    summary: Dict[str, Any] = {
        "mode": "send" if send else "dry_run",
        "eligible": 0,
        "processed": 0,
        "sent": 0,
        "errors": 0,
        "applications": [],
        "message": "",
    }

    profile_path = Path("profile_extracted.json")
    jobs_path = Path("final_job_opportunities.json")
    cv_path = Path(PDF_PATH)

    if not profile_path.exists():
        summary["message"] = "profile_extracted.json introuvable"
        print(f"[!] {summary['message']}")
        return summary
    if not jobs_path.exists():
        summary["message"] = "final_job_opportunities.json introuvable"
        print(f"[!] {summary['message']}")
        return summary
    if not cv_path.exists():
        summary["message"] = f"CV introuvable : {cv_path}"
        print(f"[!] {summary['message']}")
        return summary

    profile = load_json(profile_path, {})
    jobs = load_json(jobs_path, [])
    sent_urls = load_sent_urls()
    limit = max_apps if max_apps is not None else AUTO_APPLY_MAX

    eligible, skipped = select_eligible_jobs(jobs, sent_urls)
    summary["eligible"] = len(eligible)
    print(f"[*] Offres totales        : {len(jobs)}")
    print(f"[*] Score min candidature : {MIN_APPLY_SCORE}%")
    print(f"[*] Emails fiables OK     : {len(eligible)}")
    print(f"[*] Plafond envoi         : {limit}")
    if skipped:
        print(f"[*] Ignorées (échantillon): {len(skipped)}")
        for line in skipped[:8]:
            print(f"    - {line}")

    to_process = eligible[:limit]
    summary["processed"] = len(to_process)
    if not to_process:
        summary["message"] = "Aucune offre éligible (emails fiables + score + non déjà envoyées)."
        print(f"\n[-] {summary['message']}")
        return summary

    cover_dir = Path(COVER_LETTERS_DIR)
    cover_dir.mkdir(parents=True, exist_ok=True)
    client = ollama.Client(host=OLLAMA_HOST)

    sent_ok = 0
    errors = 0

    for idx, job in enumerate(to_process, 1):
        to_email = job["_clean_email"]
        title = job.get("title") or "Poste"
        company = job.get("company") or "Entreprise"
        print(f"\n[{idx}/{len(to_process)}] {title} — {company}")
        print(f"    -> Destinataire : {to_email}")
        print(f"    -> Score        : {job.get('match_score')}%")

        app_item: Dict[str, Any] = {
            "title": title,
            "company": company,
            "rh_email": to_email,
            "match_score": job.get("match_score"),
            "job_url": job.get("job_url"),
            "status": "pending",
        }

        try:
            letter = generate_cover_letter(client, profile, job)
        except Exception as e:
            print(f"    [!] Échec génération lettre : {e}")
            errors += 1
            app_item["status"] = "letter_error"
            app_item["error"] = str(e)
            summary["applications"].append(app_item)
            continue

        letter_file = cover_dir / f"{slugify(company)}_{slugify(title)}.txt"
        letter_file.write_text(letter, encoding="utf-8")
        print(f"    -> Lettre       : {letter_file.name}")
        preview = letter[:220].replace("\n", " ")
        print(f"    -> Aperçu       : {preview}...")
        app_item["cover_letter_file"] = str(letter_file)

        subject = f"Candidature – {title} – {profile.get('full_name', 'ZAOUI ANASS')}"

        if not send:
            print("    -> DRY-RUN      : email non envoyé")
            app_item["status"] = "dry_run"
            summary["applications"].append(app_item)
            continue

        try:
            msg = build_message(
                to_email=to_email,
                subject=subject,
                body=letter,
                cv_path=cv_path,
            )
            send_email(msg)
            record = {
                "job_url": job.get("job_url"),
                "title": title,
                "company": company,
                "rh_email": to_email,
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "match_score": job.get("match_score"),
                "cover_letter_file": str(letter_file),
                "status": "sent",
            }
            append_sent_record(record)
            sent_ok += 1
            app_item["status"] = "sent"
            print("    -> ENVOYÉ ✓")
        except Exception as e:
            errors += 1
            app_item["status"] = "smtp_error"
            app_item["error"] = str(e)
            print(f"    [!] Échec SMTP : {e}")
            append_sent_record({
                "job_url": job.get("job_url"),
                "title": title,
                "company": company,
                "rh_email": to_email,
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "error": str(e),
            })

        summary["applications"].append(app_item)

        if idx < len(to_process) and send:
            print(f"    -> Pause {AUTO_APPLY_DELAY_SEC}s...")
            time.sleep(AUTO_APPLY_DELAY_SEC)

    summary["sent"] = sent_ok
    summary["errors"] = errors
    print("\n" + "=" * 60)
    if send:
        summary["message"] = f"Envoyés : {sent_ok} | Erreurs : {errors}"
        print(f"[+] {summary['message']}")
        print(f"[+] Historique : {Path(APPLICATIONS_SENT_PATH).resolve()}")
    else:
        summary["message"] = f"Dry-run terminé : {len(to_process)} candidature(s) prêtes."
        print(f"[+] {summary['message']}")
        print("    Pour envoyer réellement : python auto_apply.py --send")
    print(f"[+] Lettres : {cover_dir.resolve()}")
    print("=" * 60)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Envoi auto CV + lettre de motivation")
    parser.add_argument(
        "--send",
        action="store_true",
        help="Envoi réel via Gmail SMTP (sinon dry-run)",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        help=f"Nombre max de candidatures (défaut: {AUTO_APPLY_MAX})",
    )
    args = parser.parse_args()
    do_send = args.send or AUTO_APPLY_SEND
    run(send=do_send, max_apps=args.max)


if __name__ == "__main__":
    main()
