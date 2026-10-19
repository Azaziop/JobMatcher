"""
JobMatcher API — optimisée pour n8n (async, matching plafonné, pas de timeout HTTP long).
"""
from __future__ import annotations

import json
import re
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
import ollama

from config import OLLAMA_HOST, MIN_MATCH_SCORE, PDF_PATH, AUTO_APPLY_MAX
from cv_parser import extract_text_from_pdf, parse_cv_with_ollama
from job_matcher import evaluate_job, is_senior_or_lead_job
from job_scraper import collect_jobs_multiplatform
from rh_finder import deduce_rh_contacts
from auto_apply import run as run_auto_apply
from bounce_monitor import check_bounces
from excel_export import export_offers, export_applications_report

app = FastAPI(title="JobMatcher Worker API")

HISTORY_FILE = Path("/app/seen_jobs.json") if Path("/app").exists() else Path("seen_jobs.json")
FINAL_JOBS_FILE = Path("final_job_opportunities.json")
PROFILE_FILE = Path("profile_extracted.json")
STATUS_FILE = Path("pipeline_status.json")
APPLY_STATUS_FILE = Path("auto_apply_status.json")

# Plafond matching LLM (sinon 400+ offres = 20–40 min → timeout n8n)
MAX_LLM_MATCH = 50

RELEVANT_TITLE_KEYWORDS = [
    "full stack", "fullstack", "full-stack", "python", "devops", "react",
    "fastapi", "next.js", "nextjs", "software engineer", "développeur",
    "developpeur", "developer", "data scientist", "data engineer",
    "machine learning", "mlops", "llm", " genai", "gen ai", "ia ", " ai ",
    "intelligence artificielle", "backend", "front-end", "frontend",
    "cloud", "junior", "débutant", "debutant",
]

IRRELEVANT_TITLE_KEYWORDS = [
    "manutentionnaire", "comptable", "commercial", "secrétaire", "secretaire",
    "vendeur", "assistant administratif", "chauffeur", "rh généraliste",
    "infirmier", "cuisinier", "agent polyvalent", "hôtesse", "hotesse",
]

_lock = threading.Lock()
_pipeline_state: Dict[str, Any] = {
    "status": "idle",
    "phase": "",
    "started_at": None,
    "finished_at": None,
    "qualified_count": 0,
    "new_count": 0,
    "raw_count": 0,
    "prefilter_count": 0,
    "duplicates_removed": 0,
    "jobs": [],          # nouvelles (non vues) — destinées à l'envoi
    "all_jobs": [],      # toutes les offres qualifiées (visualisation / Excel)
    "excel": {},
    "error": None,
    "progress": "",
}
_apply_state: Dict[str, Any] = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "progress": "",
    "result": None,
    "error": None,
}


def _set_state(target: Dict[str, Any], path: Path, **kwargs) -> None:
    with _lock:
        target.update(kwargs)
        try:
            path.write_text(json.dumps(target, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def _get_state(target: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        return dict(target)


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
        print(f"[!] Erreur sauvegarde historique : {e}")


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


def prefilter_for_llm(jobs: List[Dict[str, Any]], limit: int = MAX_LLM_MATCH) -> List[Dict[str, Any]]:
    """Filtre rapide avant Ollama : pertinence titre + anti-senior + anti-doublon URL."""
    seen_urls: set = set()
    seen_keys: set = set()
    kept: List[Dict[str, Any]] = []

    for job in jobs:
        title = (job.get("title") or "").strip()
        company = (job.get("company") or "").strip()
        url = (job.get("job_url") or "").strip()
        t = f" {title.lower()} "

        if not title:
            continue
        if is_senior_or_lead_job(title):
            continue
        if any(bad in t for bad in IRRELEVANT_TITLE_KEYWORDS):
            continue
        if not any(ok in t for ok in RELEVANT_TITLE_KEYWORDS):
            continue
        if url and url in seen_urls:
            continue
        key = re.sub(r"\W+", "", f"{title}_{company}".lower())[:120]
        if key in seen_keys:
            continue

        if url:
            seen_urls.add(url)
        seen_keys.add(key)
        kept.append(job)
        if len(kept) >= limit:
            break

    return kept


def dedupe_qualified(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dédoublonnage final titre+entreprise / URL."""
    out: List[Dict[str, Any]] = []
    seen_urls: set = set()
    seen_keys: set = set()
    for job in jobs:
        url = (job.get("job_url") or "").strip()
        key = re.sub(
            r"\W+",
            "",
            f"{job.get('title', '')}_{job.get('company', '')}".lower(),
        )[:120]
        if url and url in seen_urls:
            continue
        if key and key in seen_keys:
            continue
        if url:
            seen_urls.add(url)
        if key:
            seen_keys.add(key)
        out.append(job)
    return out


def _execute_pipeline() -> None:
    try:
        _set_state(
            _pipeline_state,
            STATUS_FILE,
            status="running",
            phase="cv",
            progress="Analyse du CV…",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
            error=None,
            jobs=[],
            all_jobs=[],
            excel={},
            qualified_count=0,
            new_count=0,
            raw_count=0,
            prefilter_count=0,
            duplicates_removed=0,
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

        _set_state(
            _pipeline_state,
            STATUS_FILE,
            phase="scrape",
            progress="Scraping multi-sites…",
        )

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
        raw_count = len(raw_jobs)

        candidates = prefilter_for_llm(raw_jobs, limit=MAX_LLM_MATCH)
        _set_state(
            _pipeline_state,
            STATUS_FILE,
            phase="match",
            raw_count=raw_count,
            prefilter_count=len(candidates),
            progress=f"Matching IA {len(candidates)}/{raw_count} (préfiltrées)…",
        )
        print(
            f"[*] Scraping: {raw_count} uniques | Préfiltre LLM: {len(candidates)} "
            f"(plafond {MAX_LLM_MATCH})"
        )

        client = ollama.Client(host=OLLAMA_HOST)
        qualified_jobs: List[Dict[str, Any]] = []

        for idx, job in enumerate(candidates, 1):
            if idx == 1 or idx % 5 == 0:
                _set_state(
                    _pipeline_state,
                    STATUS_FILE,
                    progress=f"Matching {idx}/{len(candidates)}…",
                )
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

        before_dedupe = len(qualified_jobs)
        qualified_jobs = dedupe_qualified(qualified_jobs)
        dupes = before_dedupe - len(qualified_jobs)
        qualified_jobs.sort(key=lambda x: x["match_score"], reverse=True)

        with open(FINAL_JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(qualified_jobs, f, indent=2, ensure_ascii=False)

        excel_meta = export_offers(qualified_jobs)
        print(f"[*] Excel: {excel_meta.get('xlsx')} ({excel_meta.get('count')} offres)")

        unseen_jobs = filter_unseen_jobs(qualified_jobs)
        print(
            f"[*] Qualifiées: {len(qualified_jobs)} (doublons match retirés: {dupes}) | "
            f"Nouvelles: {len(unseen_jobs)}"
        )

        _set_state(
            _pipeline_state,
            STATUS_FILE,
            status="completed",
            phase="done",
            progress="Pipeline terminé",
            finished_at=datetime.now(timezone.utc).isoformat(),
            qualified_count=len(qualified_jobs),
            new_count=len(unseen_jobs),
            duplicates_removed=dupes,
            jobs=unseen_jobs,
            all_jobs=qualified_jobs,
            excel=excel_meta,
            error=None,
        )
    except Exception as e:
        print(f"[!] Pipeline error: {e}")
        traceback.print_exc()
        _set_state(
            _pipeline_state,
            STATUS_FILE,
            status="error",
            phase="error",
            progress="Erreur",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )


def _execute_auto_apply(send: bool, max_apps: int) -> None:
    try:
        _set_state(
            _apply_state,
            APPLY_STATUS_FILE,
            status="running",
            progress="Envoi candidatures…",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None,
            result=None,
            error=None,
        )
        # Si pipeline encore running, attendre (max ~20 min)
        for _ in range(80):
            p = _get_state(_pipeline_state)
            if p.get("status") != "running":
                break
            _set_state(
                _apply_state,
                APPLY_STATUS_FILE,
                progress=f"Attente pipeline: {p.get('progress')}",
            )
            import time
            time.sleep(15)

        p = _get_state(_pipeline_state)
        if p.get("status") == "error":
            raise RuntimeError(f"Pipeline en erreur: {p.get('error')}")
        if p.get("status") == "running":
            raise RuntimeError("Timeout: pipeline toujours en cours")

        result = run_auto_apply(send=send, max_apps=max_apps)
        _set_state(
            _apply_state,
            APPLY_STATUS_FILE,
            status="completed",
            progress="Candidatures terminées",
            finished_at=datetime.now(timezone.utc).isoformat(),
            result=result,
            error=None,
        )
    except Exception as e:
        print(f"[!] Auto-apply error: {e}")
        traceback.print_exc()
        _set_state(
            _apply_state,
            APPLY_STATUS_FILE,
            status="error",
            progress="Erreur",
            finished_at=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )


def _notify_telegram(text: str) -> bool:
    """Notification Telegram optionnelle (si TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)."""
    import os
    import urllib.parse
    import urllib.request

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": text[:3500]}
        ).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        print("[+] Telegram notifié")
        return True
    except Exception as e:
        print(f"[!] Telegram skip: {e}")
        return False


def _execute_daily(send: bool, max_apps: int) -> None:
    """
    Cycle autonome complet :
    1) cherche les offres  2) match  3) envoie CV + lettres
    """
    # Toujours envoyer réellement dans le cycle quotidien
    send = True
    cap = "toutes" if max_apps >= 10**8 else str(max_apps)
    print(f"[*] Cycle quotidien autonome — recherche + envoi ({cap} éligibles)")
    _execute_pipeline()
    p = _get_state(_pipeline_state)
    if p.get("status") != "completed":
        _set_state(
            _apply_state,
            APPLY_STATUS_FILE,
            status="error",
            error=f"Pipeline non terminé: {p.get('status')} / {p.get('error')}",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        _notify_telegram(
            f"❌ JobMatcher: pipeline en erreur\n{p.get('error') or p.get('status')}"
        )
        return

    _execute_auto_apply(send=send, max_apps=max_apps)
    a = _get_state(_apply_state)
    r = a.get("result") or {}
    lines = [
        "🎯 JobMatcher — cycle terminé",
        f"Offres qualifiées: {p.get('qualified_count', 0)} | Nouvelles: {p.get('new_count', 0)}",
        f"Emails envoyés: {r.get('sent', 0)} | Erreurs: {r.get('errors', 0)} | Éligibles: {r.get('eligible', 0)}",
    ]
    for app in (r.get("applications") or [])[:8]:
        lines.append(f"• [{app.get('status')}] {app.get('title')} → {app.get('rh_email')}")
    if a.get("error"):
        lines.append(f"⚠️ {a.get('error')}")
    _notify_telegram("\n".join(lines))


@app.get("/health")
def health():
    p = _get_state(_pipeline_state)
    a = _get_state(_apply_state)
    return {
        "status": "ok",
        "pipeline": p.get("status"),
        "pipeline_progress": p.get("progress"),
        "auto_apply": a.get("status"),
        "auto_apply_progress": a.get("progress"),
    }


@app.post("/run-pipeline")
def run_pipeline(
    sync: bool = Query(False, description="True = attendre (déconseillé pour n8n)"),
) -> Any:
    state = _get_state(_pipeline_state)
    if state.get("status") == "running":
        return {
            "status": "running",
            "message": "Pipeline déjà en cours",
            "progress": state.get("progress"),
        }

    if sync:
        _execute_pipeline()
        final = _get_state(_pipeline_state)
        if final.get("status") == "error":
            raise HTTPException(status_code=500, detail=final.get("error"))
        return final.get("jobs", [])

    threading.Thread(target=_execute_pipeline, daemon=True).start()
    return {
        "status": "started",
        "message": "Pipeline lancé. Poller GET /run-pipeline/status",
    }


@app.get("/run-pipeline/status")
def run_pipeline_status() -> Dict[str, Any]:
    state = _get_state(_pipeline_state)
    out = {
        "status": state.get("status"),
        "phase": state.get("phase"),
        "progress": state.get("progress"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "raw_count": state.get("raw_count", 0),
        "prefilter_count": state.get("prefilter_count", 0),
        "qualified_count": state.get("qualified_count", 0),
        "new_count": state.get("new_count", 0),
        "duplicates_removed": state.get("duplicates_removed", 0),
        "error": state.get("error"),
        "done": state.get("status") in ("completed", "error", "idle"),
    }
    if state.get("status") == "completed":
        out["jobs"] = state.get("jobs", [])
    return out


@app.post("/auto-apply")
def auto_apply(
    send: bool = Query(True),
    max: Optional[int] = Query(None),
    wait: bool = Query(False, description="False=async (recommandé n8n). True=bloquant."),
) -> Dict[str, Any]:
    """
    Lance les candidatures.
    Par défaut ASYNC : répond tout de suite, poller GET /auto-apply/status.
    """
    # max omis = toutes les offres éligibles (pas de plafond)
    limit = int(max) if max is not None else None
    a = _get_state(_apply_state)
    if a.get("status") == "running":
        return {
            "status": "running",
            "message": "Auto-apply déjà en cours",
            "progress": a.get("progress"),
        }

    if wait:
        _execute_auto_apply(send=send, max_apps=limit if limit is not None else 10**9)
        final = _get_state(_apply_state)
        if final.get("status") == "error":
            raise HTTPException(status_code=500, detail=final.get("error"))
        return final.get("result") or {}

    threading.Thread(
        target=_execute_auto_apply,
        kwargs={"send": send, "max_apps": limit if limit is not None else 10**9},
        daemon=True,
    ).start()
    return {
        "status": "started",
        "message": "Auto-apply lancé. Poller GET /auto-apply/status",
        "send": send,
        "max": limit if limit is not None else "all",
    }


@app.get("/auto-apply/status")
def auto_apply_status() -> Dict[str, Any]:
    state = _get_state(_apply_state)
    out = {
        "status": state.get("status"),
        "progress": state.get("progress"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "error": state.get("error"),
        "done": state.get("status") in ("completed", "error", "idle"),
    }
    if state.get("status") == "completed" and state.get("result"):
        r = state["result"]
        out["sent"] = r.get("sent", 0)
        out["errors"] = r.get("errors", 0)
        out["eligible"] = r.get("eligible", 0)
        out["applications"] = r.get("applications", [])
        out["message"] = r.get("message", "")
    return out


@app.post("/run-daily")
def run_daily(
    send: bool = Query(True),
    max: Optional[int] = Query(None, description="Optionnel. Omit = toutes les offres éligibles"),
) -> Dict[str, Any]:
    """
    Un seul appel n8n : cherche + envoie toutes les candidatures éligibles.
    Pas besoin de max. Poller GET /run-daily/status si besoin.
    """
    p = _get_state(_pipeline_state)
    a = _get_state(_apply_state)
    if p.get("status") == "running" or a.get("status") == "running":
        return {
            "status": "running",
            "message": "Cycle déjà en cours",
            "pipeline": p.get("progress"),
            "auto_apply": a.get("progress"),
        }

    limit = int(max) if max is not None else 10**9  # toutes
    threading.Thread(
        target=_execute_daily,
        kwargs={"send": send, "max_apps": limit},
        daemon=True,
    ).start()
    return {
        "status": "started",
        "message": "Cycle lancé : recherche + envoi de toutes les offres éligibles",
        "send": True,
        "max": "all" if max is None else limit,
    }


@app.post("/check-bounces")
def api_check_bounces(
    limit: int = Query(50, description="Nombre de mails récents à scanner"),
) -> Dict[str, Any]:
    """
    Vérifie Gmail pour les bounces (adresse introuvable).
    Si bounce lié à une candidature → Telegram avec l'URL de l'offre.
    """
    try:
        return check_bounces(limit=min(int(limit), 100))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/offers")
def list_offers(
    only_new: bool = Query(False, description="True = seulement les nouvelles (non vues)"),
) -> Dict[str, Any]:
    """
    Liste les offres scrappées/qualifiées pour visualisation n8n.
    Dedup déjà faite côté pipeline (URL + titre+entreprise).
    """
    p = _get_state(_pipeline_state)
    all_jobs = p.get("all_jobs") or []
    if not all_jobs and FINAL_JOBS_FILE.exists():
        try:
            all_jobs = json.loads(FINAL_JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            all_jobs = []
    jobs = p.get("jobs") or [] if only_new else all_jobs
    return {
        "count": len(jobs),
        "qualified_count": len(all_jobs),
        "new_count": len(p.get("jobs") or []),
        "duplicates_removed": p.get("duplicates_removed", 0),
        "excel": p.get("excel") or {},
        "jobs": jobs,
    }


@app.post("/offers/export")
def offers_export() -> Dict[str, Any]:
    """Régénère CSV + Excel des offres qualifiées."""
    meta = export_offers()
    return {"ok": True, **meta}


@app.get("/applications")
def list_applications() -> Dict[str, Any]:
    """
    Rapport candidatures : sent / bounced / error.
    Utilisé par n8n pour vérifier si tous les mails sont bien partis,
    et lister ceux qui ont bounce (mail inexistant).
    """
    report = export_applications_report()
    bounced = [a for a in report["applications"] if a.get("status") == "bounced"]
    sent = [a for a in report["applications"] if a.get("status") == "sent"]
    errors = [a for a in report["applications"] if a.get("status") == "error"]
    all_ok = report["bounced"] == 0 and report["error"] == 0 and report["sent"] > 0
    return {
        "ok": all_ok,
        "total": report["total"],
        "sent": report["sent"],
        "bounced": report["bounced"],
        "error": report["error"],
        "xlsx": report["xlsx"],
        "csv": report["csv"],
        "sent_apps": sent,
        "bounced_apps": bounced,
        "error_apps": errors,
        "failed_for_telegram": [
            {
                "title": a.get("title"),
                "company": a.get("company"),
                "rh_email": a.get("rh_email"),
                "job_url": a.get("job_url"),
                "status": a.get("status"),
            }
            for a in bounced + errors
            if a.get("job_url")
        ],
    }


@app.post("/applications/notify-failed")
def notify_failed_applications() -> Dict[str, Any]:
    """
    Envoie sur Telegram les offres dont le mail a bounce / échoué
    (URL + titre + entreprise).
    """
    report = export_applications_report()
    failed = [
        a
        for a in report["applications"]
        if a.get("status") in ("bounced", "error") and a.get("job_url")
    ]
    if not failed:
        return {"ok": True, "notified": 0, "message": "Aucune candidature échouée"}

    lines = [
        f"⚠️ JobMatcher — {len(failed)} mail(s) non reçus (bounce/erreur)",
        "",
    ]
    for a in failed[:25]:
        lines.append(
            f"• {a.get('title')} @ {a.get('company')}\n"
            f"  {a.get('rh_email')}\n"
            f"  {a.get('job_url')}"
        )
    ok = _notify_telegram("\n".join(lines))
    return {"ok": ok, "notified": len(failed), "failed": failed}


@app.get("/run-daily/status")
def run_daily_status() -> Dict[str, Any]:
    p = _get_state(_pipeline_state)
    a = _get_state(_apply_state)
    running = p.get("status") == "running" or a.get("status") == "running"
    completed = (
        p.get("status") == "completed"
        and a.get("status") == "completed"
    )
    errored = p.get("status") == "error" or a.get("status") == "error"

    out: Dict[str, Any] = {
        "status": "running" if running else ("completed" if completed else ("error" if errored else "idle")),
        "done": completed or errored,
        "pipeline": {
            "status": p.get("status"),
            "progress": p.get("progress"),
            "raw_count": p.get("raw_count", 0),
            "prefilter_count": p.get("prefilter_count", 0),
            "qualified_count": p.get("qualified_count", 0),
            "new_count": p.get("new_count", 0),
            "duplicates_removed": p.get("duplicates_removed", 0),
            "error": p.get("error"),
            "excel": p.get("excel") or {},
        },
        "auto_apply": {
            "status": a.get("status"),
            "progress": a.get("progress"),
            "error": a.get("error"),
        },
    }
    if p.get("status") == "completed":
        out["jobs"] = p.get("jobs", [])
        out["all_jobs"] = p.get("all_jobs", [])
    if a.get("status") == "completed" and a.get("result"):
        r = a["result"]
        out["auto_apply"]["sent"] = r.get("sent", 0)
        out["auto_apply"]["errors"] = r.get("errors", 0)
        out["auto_apply"]["eligible"] = r.get("eligible", 0)
        out["auto_apply"]["applications"] = r.get("applications", [])
    return out
