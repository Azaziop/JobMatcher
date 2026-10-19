"""Export Excel / CSV des offres et candidatures pour n8n."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

FINAL_JSON = Path("final_job_opportunities.json")
FINAL_CSV = Path("final_job_opportunities.csv")
FINAL_XLSX = Path("final_job_opportunities.xlsx")
APPS_JSON = Path("applications_sent.json")
APPS_XLSX = Path("applications_report.xlsx")
APPS_CSV = Path("applications_report.csv")

OFFER_COLUMNS = [
    "match_score",
    "title",
    "company",
    "location",
    "rh_email",
    "rh_source",
    "confidence",
    "job_url",
    "date_posted",
    "evaluation_summary",
]

APP_COLUMNS = [
    "status",
    "title",
    "company",
    "rh_email",
    "match_score",
    "job_url",
    "sent_at",
    "bounced_at",
]


def _load_json(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def export_offers(jobs: List[Dict[str, Any]] | None = None) -> Dict[str, str]:
    rows = jobs if jobs is not None else _load_json(FINAL_JSON)
    df = pd.DataFrame(rows)
    for col in OFFER_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if not df.empty:
        df = df[OFFER_COLUMNS]
    else:
        df = pd.DataFrame(columns=OFFER_COLUMNS)

    df.to_csv(FINAL_CSV, index=False, encoding="utf-8-sig")
    df.to_excel(FINAL_XLSX, index=False)
    # JSON sync
    FINAL_JSON.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "csv": str(FINAL_CSV.resolve()),
        "xlsx": str(FINAL_XLSX.resolve()),
        "json": str(FINAL_JSON.resolve()),
        "count": str(len(rows)),
    }


def export_applications_report() -> Dict[str, Any]:
    apps = _load_json(APPS_JSON)
    # clean emails display
    cleaned = []
    for a in apps:
        if not isinstance(a, dict):
            continue
        cleaned.append(
            {
                "status": a.get("status", ""),
                "title": a.get("title", ""),
                "company": a.get("company", ""),
                "rh_email": a.get("rh_email", ""),
                "match_score": a.get("match_score", ""),
                "job_url": a.get("job_url", ""),
                "sent_at": a.get("sent_at", ""),
                "bounced_at": a.get("bounced_at", ""),
            }
        )
    df = pd.DataFrame(cleaned)
    for col in APP_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if not df.empty:
        df = df[APP_COLUMNS]
    else:
        df = pd.DataFrame(columns=APP_COLUMNS)

    df.to_csv(APPS_CSV, index=False, encoding="utf-8-sig")
    df.to_excel(APPS_XLSX, index=False)

    summary = {
        "total": len(cleaned),
        "sent": sum(1 for a in cleaned if a.get("status") == "sent"),
        "bounced": sum(1 for a in cleaned if a.get("status") == "bounced"),
        "error": sum(1 for a in cleaned if a.get("status") == "error"),
        "csv": str(APPS_CSV.resolve()),
        "xlsx": str(APPS_XLSX.resolve()),
        "applications": cleaned,
    }
    return summary
