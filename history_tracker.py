import json
from pathlib import Path
from typing import List, Dict, Any

HISTORY_FILE = Path("seen_jobs.json")


def load_seen_job_ids() -> set:
    """Charge les URLs ou identifiants des offres déjà envoyées."""
    if not HISTORY_FILE.exists():
        return set()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception:
        return set()


def save_new_job_ids(new_ids: List[str]):
    """Ajoute les nouveaux identifiants à l'historique persistant."""
    current_ids = load_seen_job_ids()
    current_ids.update(new_ids)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(current_ids), f, indent=2, ensure_ascii=False)


def filter_already_sent_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filtre la liste en ne gardant que les offres jamais vues."""
    seen_ids = load_seen_job_ids()
    new_jobs = []
    new_ids = []

    for job in jobs:
        # Identifiant unique : URL de l'offre ou combinaison Titre + Entreprise
        job_id = job.get("job_url") or f"{job.get('title')}_{job.get('company')}"
        if job_id not in seen_ids:
            new_jobs.append(job)
            new_ids.append(job_id)

    # Sauvegarde immédiate des nouvelles offres détectées
    if new_ids:
        save_new_job_ids(new_ids)

    return new_jobs