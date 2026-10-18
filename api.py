import json
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
import ollama

from config import OLLAMA_HOST, MIN_MATCH_SCORE, PDF_PATH, AUTO_APPLY_MAX
from cv_parser import extract_text_from_pdf, parse_cv_with_ollama
from job_matcher import evaluate_job
from job_scraper import collect_jobs_multiplatform
from rh_finder import deduce_rh_contacts
from auto_apply import run as run_auto_apply

app = FastAPI(title="JobMatcher Worker API")

HISTORY_FILE = Path("/app/seen_jobs.json") if Path("/app").exists() else Path("seen_jobs.json")
FINAL_JOBS_FILE = Path("final_job_opportunities.json")
PROFILE_FILE = Path("profile_extracted.json")
STATUS_FILE = Path("pipeline_status.json")

_lock = threading.Lock()
_pipeline_state: Dict[str, Any] = {
    "status": "idle",  # idle | running | completed | error
    "phase": "",
    "started_at": None,
    "finished_at": None,
    "qualified_count": 0,
    "new_count": 0,
    "jobs": [],
    "error": None,
    "progress": "",
}


def _set_state(**kwargs) -> None:
    with _lock:
        _pipeline_state.update(kwargs)
        try:
            STATUS_FILE.write_text(
                json.dumps(_pipeline_state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass


def _get_state() -> Dict[str, Any]:
    with _lock:
        return dict(_pipeline_state)


def load_seen_job_ids() -> set:
    if not HISTORY_FILE.exists():
        return set()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen_job_ids(seen_ids: set) -> None:
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_ids), f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[!] Erreur de sauvegarde de l'historique : {e}")


def filter_unseen_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen_ids = load_seen_job_ids()
    new_jobs = []
    for job in jobs:
        job_id = job.get("job_url") or f"{job.get('title')}_{job.get('company')}".lower().strip()
        if job_id not in seen_ids:
            new_jobs.append(job)
            seen_ids.add(job_id)
    if new_jobs:
        save_seen_job_ids(seen_ids)
    return new_jobs


def _execute_pipeline() -> None:
    try:
        _set_state(
            status="running",
            phase="cv",
            progress="Analyse du CV…",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
            error=None,
            jobs=[],
            qualified_count=0,
            new_count=0,
        )

        if not Path(PDF_PATH).exists():
            local_pdfs = list(Path(".").glob("*.pdf"))
            if not local_pdfs:
                raise FileNotFoundError("Fichier PDF introuvable dans le conteneur.")
            target_pdf = str(local_pdfs[0])
        else:
            target_pdf = PDF_PATH

        raw_text = extract_text_from_pdf(target_pdf)
        profile = parse_cv_with_ollama(raw_text)
        profile_dict = profile.model_dump()
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(profile_dict, f, indent=2, ensure_ascii=False)

        _set_state(phase="scrape", progress="Scraping multi-sites Maroc + France…")

        # Requêtes réduites pour n8n (durée raisonnable)
        search_queries = [
            "Junior Full Stack Developer",
            "Développeur Python Débutant",
            "Junior DevOps Engineer",
            "Junior AI Engineer",
            "Junior Data Scientist",
            "Junior Software Engineer",
        ]

        raw_jobs = collect_jobs_multiplatform(
            search_terms=search_queries,
            results_per_term=8,
            hours_old=168,
        )

        _set_state(
            phase="match",
            progress=f"Matching IA sur {len(raw_jobs)} offres…",
        )

        client = ollama.Client(host=OLLAMA_HOST)
        qualified_jobs: List[Dict[str, Any]] = []

        for idx, job in enumerate(raw_jobs, 1):
            if idx % 10 == 0 or idx == 1:
                _set_state(progress=f"Matching {idx}/{len(raw_jobs)}…")
            eval_res = evaluate_job(client, profile_dict, job)
            if eval_res.match_score >= MIN_MATCH_SCORE and eval_res.is_junior_friendly:
                contact = deduce_rh_contacts(job)
                qualified_jobs.append(
                    {
                        "title": job.get("title"),
                        "company": job.get("company"),
                        "location": job.get("location"),
                        "match_score": eval_res.match_score,
                        "evaluation_summary": eval_res.summary_reason,
                        "job_url": job.get("job_url"),
                        "rh_email": contact["email"],
                        "rh_source": contact["source"],
                        "confidence": contact["confidence"],
                        "date_posted": job.get("date_posted", ""),
                    }
                )

        qualified_jobs.sort(key=lambda x: x["match_score"], reverse=True)

        with open(FINAL_JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(qualified_jobs, f, indent=2, ensure_ascii=False)

        unseen_jobs = filter_unseen_jobs(qualified_jobs)
        print(
            f"[*] Offres qualifiées : {len(qualified_jobs)} | "
            f"Nouvelles offres : {len(unseen_jobs)}"
        )

        _set_state(
            status="completed",
            phase="done",
            progress="Terminé",
            finished_at=datetime.now(timezone.utc).isoformat(),
            qualified_count=len(qualified_jobs),
            new_count=len(unseen_jobs),
            jobs=unseen_jobs,
            error=None,
        )
    except Exception as e:
        print(f"[!] Pipeline error: {e}")
        traceback.print_exc()
        _set_state(
            status="error",
            phase="error",
            progress="Erreur",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )


@app.get("/health")
def health():
    state = _get_state()
    return {"status": "ok", "pipeline": state.get("status"), "progress": state.get("progress")}


@app.post("/run-pipeline")
def run_pipeline(
    sync: bool = Query(False, description="True = attendre la fin (lent). False = async n8n."),
) -> Any:
    """
    Démarre le pipeline.
    - sync=false (défaut) : répond tout de suite, n8n poll /run-pipeline/status
    - sync=true : attend la fin (peut timeout côté n8n)
    """
    state = _get_state()
    if state.get("status") == "running":
        return {
            "status": "running",
            "message": "Pipeline déjà en cours",
            "progress": state.get("progress"),
        }

    if sync:
        _execute_pipeline()
        final = _get_state()
        if final.get("status") == "error":
            raise HTTPException(status_code=500, detail=final.get("error"))
        return final.get("jobs", [])

    thread = threading.Thread(target=_execute_pipeline, daemon=True)
    thread.start()
    return {
        "status": "started",
        "message": "Pipeline lancé en arrière-plan. Poller GET /run-pipeline/status",
        "status_url": "/run-pipeline/status",
    }


@app.get("/run-pipeline/status")
def run_pipeline_status() -> Dict[str, Any]:
    """Statut du pipeline pour n8n (Wait + boucle)."""
    state = _get_state()
    # Réponse légère pour le poll (sans toutes les offres si trop long)
    out = {
        "status": state.get("status"),
        "phase": state.get("phase"),
        "progress": state.get("progress"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "qualified_count": state.get("qualified_count", 0),
        "new_count": state.get("new_count", 0),
        "error": state.get("error"),
        "done": state.get("status") in ("completed", "error", "idle"),
    }
    if state.get("status") == "completed":
        out["jobs"] = state.get("jobs", [])
    return out


def _wait_for_pipeline(timeout_sec: int = 2700, poll_sec: int = 15) -> Dict[str, Any]:
    """Attend la fin du pipeline (pour n8n linéaire Pipeline → Auto-apply)."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        state = _get_state()
        status = state.get("status")
        if status == "completed":
            return state
        if status == "error":
            raise HTTPException(
                status_code=500,
                detail=f"Pipeline en erreur: {state.get('error')}",
            )
        if status != "running":
            # idle / rien en cours — tenter quand même si final_job_opportunities existe
            if FINAL_JOBS_FILE.exists():
                return state
            raise HTTPException(
                status_code=409,
                detail="Aucun pipeline terminé. Lancez d'abord POST /run-pipeline.",
            )
        print(f"[*] auto-apply attend pipeline… {state.get('progress')}")
        time.sleep(poll_sec)

    raise HTTPException(
        status_code=504,
        detail="Timeout: pipeline toujours en cours après attente. Réessayez plus tard.",
    )


@app.post("/auto-apply")
def auto_apply(
    send: bool = Query(True, description="True = envoi Gmail réel, False = dry-run"),
    max: Optional[int] = Query(None, description="Plafond de candidatures"),
    wait: bool = Query(True, description="Attendre la fin du pipeline si encore running"),
) -> Dict[str, Any]:
    """
    Génère lettres + envoie CV aux RH fiables.
    Si wait=true (défaut) et pipeline running → attend la fin (pas de 409).
    """
    state = _get_state()
    if state.get("status") == "running":
        if not wait:
            raise HTTPException(
                status_code=409,
                detail="Pipeline encore en cours. Attendez GET /run-pipeline/status = completed.",
            )
        print("[*] Pipeline en cours — auto-apply attend la fin…")
        _wait_for_pipeline()

    try:
        limit = max if max is not None else AUTO_APPLY_MAX
        limit = min(int(limit), 30)
        return run_auto_apply(send=send, max_apps=limit)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
