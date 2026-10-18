import csv
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
from dotenv import load_dotenv

load_dotenv()
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")

# Domaines et portails RH connus des entreprises actives au Maroc
KNOWN_COMPANY_DOMAINS = {
    "sqli": "sqli.com",
    "inetum": "inetum.com",
    "capgemini": "capgemini.com",
    "capgemini engineering": "capgemini.com",
    "sofrecom": "sofrecom.com",
    "sofrecom maroc": "sofrecom.com",
    "orange business": "orange.com",
    "orange": "orange.com",
    "devoteam": "devoteam.com",
    "talan": "talan.com",
    "smile": "smile.eu",
    "societe generale": "socgen.com",
    "société générale": "socgen.com",
    "deloitte": "deloitte.com",
    "totalcall": "totalcall.ma",
    "astek": "astekgroup.fr",
    "logigroup": "logigroup.com",
    "webintelligence": "webintelligence.fr",
    "dm geosoftware": "dmgeosoftware.com",
    "maroc fer": "marocfer.ma",
    "forterro": "forterro.com",
    "fortee": "fortee.ma",
    "cegid": "cegid.com",
    "stmicroelectronics": "st.com",
    "mckinsey": "mckinsey.com",
    "artefact": "artefact.com",
    "alten": "alten.com",
    "stellantis": "stellantis.com",
    "cgi": "cgi.com",
    "atos": "atos.net",
    "sopra steria": "soprasteria.com",
    "neosoft": "neosoft.fr",
    "solutec": "solutec.fr",
    "akkodis": "akkodis.com",
    "wavestone": "wavestone.com",
    "thales": "thalesgroup.com",
    "scalian": "scalian.com",
    "extia": "extia.fr",
    "finastra": "finastra.com",
    "reply": "reply.com",
    "bcg": "bcg.com",
    "boston consulting": "bcg.com",
}


# ---------------------------------------------------------------------------
# 1. Stratégies d'extraction d'emails
# ---------------------------------------------------------------------------
def extract_email_from_text(text: str) -> Optional[str]:
    """Recherche un email de contact direct dans le descriptif de l'annonce."""
    pattern = r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+'
    matches = re.findall(pattern, text)

    ignored_extensions = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")
    valid_emails = []

    for email in matches:
        clean = email.strip(".,;:()[]{}<>\"'")
        if not any(clean.lower().endswith(ext) for ext in ignored_extensions):
            valid_emails.append(clean)

    if valid_emails:
        # Priorité aux boîtes RH / recrutement
        for email in valid_emails:
            lower = email.lower()
            if any(k in lower for k in ["rh", "recrut", "talent", "career", "job", "candidat", "contact"]):
                return email
        return valid_emails[0]

    return None


def resolve_domain(company_name: str) -> Optional[str]:
    """Détermine le domaine web le plus probable d'une entreprise."""
    comp_lower = company_name.lower().strip()

    # 1. Recherche dans le dictionnaire direct
    for key, domain in KNOWN_COMPANY_DOMAINS.items():
        if key in comp_lower:
            return domain

    # 2. Nettoyage générique des formes juridiques
    cleaned = re.sub(r'\b(sarl|sa|inc|ltd|ste|societe|groupe|maroc|technologies|solutions|services)\b', '', comp_lower)
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', cleaned).strip()

    if len(cleaned) >= 3:
        return f"{cleaned}.com"

    return None


def find_email_via_hunter(domain: str) -> Optional[Dict[str, Any]]:
    """Interroge l'API Hunter.io si une clé valide est fournie."""
    if not HUNTER_API_KEY or not domain:
        return None

    url = f"https://api.hunter.io/v2/domain-search?domain={domain}&api_key={HUNTER_API_KEY}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", {})
            emails = data.get("emails", [])

            # 1. Priorité département RH / Recrutement
            for entry in emails:
                dept = (entry.get("department") or "").lower()
                pos = (entry.get("position") or "").lower()
                if any(k in dept or k in pos for k in ["human", "recrut", "talent", "rh"]):
                    return {
                        "email": entry.get("value"),
                        "type": f"Contact RH Nominatif ({entry.get('position', 'RH')})",
                        "confidence": entry.get("confidence", 0)
                    }

            # 2. Premier email d'entreprise trouvé
            if emails:
                first = emails[0]
                return {
                    "email": first.get("value"),
                    "type": "Contact Entreprise",
                    "confidence": first.get("confidence", 0)
                }
    except Exception as e:
        print(f"      [!] Hunter.io exception : {e}")

    return None


def deduce_rh_contacts(job: Dict[str, Any]) -> Dict[str, Any]:
    """Cascade de détection du meilleur contact RH."""
    description = job.get("description", "")
    company = job.get("company", "")

    # Niveau 1 : Détection directe dans le texte de l'annonce
    in_text_email = extract_email_from_text(description)
    if in_text_email:
        return {
            "email": in_text_email,
            "source": "Mentionné directement dans l'offre",
            "confidence": "Haute (100%)"
        }

    # Niveau 2 : Résolution de domaine + Hunter.io
    domain = resolve_domain(company)
    if domain and HUNTER_API_KEY:
        hunter_res = find_email_via_hunter(domain)
        if hunter_res:
            return {
                "email": hunter_res["email"],
                "source": f"Hunter.io ({hunter_res['type']})",
                "confidence": f"{hunter_res['confidence']}%"
            }

    # Niveau 3 : Domaine d'entreprise connu (dictionnaire) → contact careers standard
    if domain and domain in KNOWN_COMPANY_DOMAINS.values():
        return {
            "email": f"recrutement@{domain}",
            "source": "Domaine entreprise connu (dictionnaire)",
            "confidence": "Bonne (domaine vérifié, boîte générique)"
        }

    # Niveau 4 : Pattern inventé — à ne pas utiliser pour l'envoi auto
    target_domain = domain if domain else f"{re.sub(r'[^a-z0-9]', '', company.lower())}.com"
    return {
        "email": f"recrutement@{target_domain} (suggéré)",
        "source": "Pattern standard d'entreprise",
        "confidence": "Moyenne (Vérification LinkedIn conseillée)"
    }


# ---------------------------------------------------------------------------
# 2. Processus d'enrichissement et export
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print(" DÉMARRAGE DU MODULE 4 : ENRICHISSEMENT DES CONTACTS RH")
    print("=" * 60)

    matched_path = Path("matched_jobs.json")
    if not matched_path.exists():
        print("[!] Fichier 'matched_jobs.json' introuvable. Exécutez d'abord job_matcher.py.")
        return

    with open(matched_path, "r", encoding="utf-8") as f:
        matched_jobs: List[Dict[str, Any]] = json.load(f)

    print(f"[*] Traitement de {len(matched_jobs)} offres qualifiées...")

    enriched_jobs = []
    for idx, job in enumerate(matched_jobs, 1):
        title = job.get("title", "")
        company = job.get("company", "")
        print(f"\n[{idx}/{len(matched_jobs)}] Recherche contact : {company} ({title})")

        contact_info = deduce_rh_contacts(job)
        print(f"   -> Email : {contact_info['email']} [{contact_info['source']}]")

        enriched_entry = {
            "title": title,
            "company": company,
            "location": job.get("location", ""),
            "match_score": job.get("match_score", 0),
            "job_url": job.get("job_url", ""),
            "rh_email": contact_info["email"],
            "rh_source": contact_info["source"],
            "confidence": contact_info["confidence"],
            "evaluation_summary": job.get("evaluation_summary", ""),
            "date_posted": job.get("date_posted", "")
        }
        enriched_jobs.append(enriched_entry)

    # Export JSON
    json_output = Path("final_job_opportunities.json")
    with open(json_output, "w", encoding="utf-8") as f:
        json.dump(enriched_jobs, f, indent=2, ensure_ascii=False)

    # Export CSV pour Excel
    csv_output = Path("final_job_opportunities.csv")
    if enriched_jobs:
        keys = ["match_score", "title", "company", "location", "rh_email", "job_url", "confidence", "evaluation_summary"]
        with open(csv_output, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(enriched_jobs)

    print("\n" + "=" * 60)
    print(f"[+] Enrichissement terminé pour {len(enriched_jobs)} offres !")
    print(f"[+] Fichiers générés :")
    print(f"    - {json_output.resolve()}")
    print(f"    - {csv_output.resolve()} (Consultable sous Excel)")
    print("=" * 60)


if __name__ == "__main__":
    main()