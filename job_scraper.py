import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict, Any, Optional
from jobspy import scrape_jobs
from morocco_portals import scrape_rekrute, scrape_emploi_ma, scrape_marocannonces
from france_portals import scrape_wttj, scrape_hellowork


FORBIDDEN_KEYWORDS = [
    "stage", "pfe", "intern", "internship", "stagiaire",
    "alternance", "alternant", "apprentissage", "apprenti", "trainee"
]


def is_real_job_offer(title: str, description: str) -> bool:
    """Élimine les stages, alternances et apprentissages."""
    title_lower = title.lower()
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{kw}\b", title_lower):
            return False

    first_lines = description[:300].lower()
    if any(phrase in first_lines for phrase in [
        "offre de stage", "stage pfe", "sujet de stage", "projet de fin d'études"
    ]):
        return False

    return True


def clean_text_for_comparison(text: str) -> str:
    t = text.lower()
    t = re.sub(
        r"\b(sarl|sa|inc|ltd|ste|societe|groupe|group|maroc|f/h|h/f|cdi|cdd|ref|junior|débutant|debutant)\b",
        "",
        t,
    )
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def are_jobs_duplicate(job_a: Dict[str, Any], job_b: Dict[str, Any], similarity_threshold: float = 0.80) -> bool:
    if job_a.get("job_url") and job_a.get("job_url") == job_b.get("job_url"):
        return True

    comp_a = clean_text_for_comparison(job_a.get("company", ""))
    comp_b = clean_text_for_comparison(job_b.get("company", ""))

    ignore_companies = {
        "confidentiel", "entreprise rekrute", "recruteur marocannonces",
        "entreprise emploi ma", "entreprise recruteuse", "entreprise wttj",
        "entreprise hellowork", "entreprise emploi.ma",
    }
    if comp_a and comp_b and comp_a not in ignore_companies and comp_b not in ignore_companies:
        company_ratio = SequenceMatcher(None, comp_a, comp_b).ratio()
        if company_ratio < 0.75:
            return False

        title_a = clean_text_for_comparison(job_a.get("title", ""))
        title_b = clean_text_for_comparison(job_b.get("title", ""))
        if not title_a or not title_b:
            return False
        if title_a in title_b or title_b in title_a:
            return True
        return SequenceMatcher(None, title_a, title_b).ratio() >= similarity_threshold

    return False


def deduplicate_jobs(raw_jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique_list: List[Dict[str, Any]] = []
    for new_job in raw_jobs:
        is_dup = False
        for idx, existing_job in enumerate(unique_list):
            if are_jobs_duplicate(new_job, existing_job):
                is_dup = True
                if len(new_job.get("description", "")) > len(existing_job.get("description", "")):
                    unique_list[idx] = new_job
                break
        if not is_dup:
            unique_list.append(new_job)
    return unique_list


def _row_to_job(row, site_label: str, term: str) -> Optional[Dict[str, Any]]:
    t = str(row.get("title") or "").strip()
    d = str(row.get("description") or "").strip()
    u = str(row.get("job_url") or "").strip()
    if not (t and u and is_real_job_offer(t, d)):
        return None
    return {
        "id": str(row.get("id") or ""),
        "site": site_label,
        "title": t,
        "company": str(row.get("company") or "").strip(),
        "location": str(row.get("location") or "").strip(),
        "job_url": u,
        "description": d,
        "date_posted": str(row.get("date_posted") or "").strip(),
        "search_query": term,
    }


def _scrape_site(
    all_jobs: List[Dict[str, Any]],
    *,
    site_name: List[str],
    term: str,
    location: str,
    results_wanted: int,
    hours_old: int,
    label: str,
    country_indeed: str = "france",
    google_search_term: Optional[str] = None,
) -> int:
    try:
        kwargs = dict(
            site_name=site_name,
            search_term=term,
            location=location,
            results_wanted=results_wanted,
            hours_old=hours_old,
            country_indeed=country_indeed,
            verbose=0,
        )
        if google_search_term:
            kwargs["google_search_term"] = google_search_term
        df = scrape_jobs(**kwargs)
        if df is None or df.empty:
            print(f"    -> {label} : +0")
            return 0
        count = 0
        for _, row in df.iterrows():
            job = _row_to_job(row, label, term)
            if job:
                all_jobs.append(job)
                count += 1
        print(f"    -> {label} : +{count}")
        return count
    except Exception as e:
        print(f"    [!] {label} skip : {e}")
        return 0


def collect_jobs_multiplatform(
    search_terms: List[str],
    results_per_term: int = 10,
    hours_old: int = 168,
) -> List[Dict[str, Any]]:
    all_jobs: List[Dict[str, Any]] = []

    # =========================================================================
    # PHASE 1 : JobSpy — LinkedIn, Indeed, Glassdoor, Google Jobs, Bayt
    # =========================================================================
    print("\n--- PHASE 1 : JOBSPY (LinkedIn, Indeed, Glassdoor, Google, Bayt) ---")
    for term in search_terms:
        print(f"[*] Recherche multi-sites : '{term}'...")

        # LinkedIn
        for li_loc in ["Morocco", "France"]:
            _scrape_site(
                all_jobs,
                site_name=["linkedin"],
                term=term,
                location=li_loc,
                results_wanted=results_per_term,
                hours_old=hours_old,
                label=f"linkedin_{li_loc.lower()}",
            )

        # Indeed
        for country, loc in [("morocco", "Morocco"), ("france", "France")]:
            _scrape_site(
                all_jobs,
                site_name=["indeed"],
                term=term,
                location=loc,
                results_wanted=results_per_term,
                hours_old=hours_old,
                label=f"indeed_{country}",
                country_indeed=country,
            )

        # Glassdoor (France — meilleur couverture)
        _scrape_site(
            all_jobs,
            site_name=["glassdoor"],
            term=term,
            location="France",
            results_wanted=results_per_term,
            hours_old=hours_old,
            label="glassdoor_france",
            country_indeed="france",
        )

        # Google Jobs Maroc + France
        for country_label, gquery in [
            ("morocco", f"{term} jobs in Morocco since past week"),
            ("france", f"{term} jobs in France since past week"),
        ]:
            _scrape_site(
                all_jobs,
                site_name=["google"],
                term=term,
                location=country_label.title(),
                results_wanted=results_per_term,
                hours_old=hours_old,
                label=f"google_{country_label}",
                google_search_term=gquery,
            )

        # Bayt (MENA / Maroc)
        _scrape_site(
            all_jobs,
            site_name=["bayt"],
            term=term,
            location="Morocco",
            results_wanted=results_per_term,
            hours_old=hours_old,
            label="bayt_morocco",
            country_indeed="morocco",
        )

        time.sleep(0.8)

    # =========================================================================
    # PHASE 2 : Portails Maroc — ReKrute, Emploi.ma, MarocAnnonces
    # =========================================================================
    print("\n--- PHASE 2 : PORTAILS MAROC (ReKrute, Emploi.ma, MarocAnnonces) ---")
    local_keywords = [
        "full stack", "python", "devops", "cloud", "data",
        "react", "IA", "LLM", "fastapi", "ingénieur logiciel",
    ]

    for kw in local_keywords:
        print(f"[*] Scan Maroc : '{kw}'...")
        try:
            rk = [j for j in scrape_rekrute(kw) if is_real_job_offer(j["title"], j["description"])]
            all_jobs.extend(rk)
            print(f"    -> ReKrute : +{len(rk)}")
        except Exception as e:
            print(f"    [!] ReKrute skip : {e}")

        try:
            emp = [j for j in scrape_emploi_ma(kw) if is_real_job_offer(j["title"], j["description"])]
            all_jobs.extend(emp)
            print(f"    -> Emploi.ma : +{len(emp)}")
        except Exception as e:
            print(f"    [!] Emploi.ma skip : {e}")

        try:
            ma = [j for j in scrape_marocannonces(kw) if is_real_job_offer(j["title"], j["description"])]
            all_jobs.extend(ma)
            print(f"    -> MarocAnnonces : +{len(ma)}")
        except Exception as e:
            print(f"    [!] MarocAnnonces skip : {e}")

        time.sleep(0.5)

    # =========================================================================
    # PHASE 3 : Portails France — Welcome to the Jungle, HelloWork
    # =========================================================================
    print("\n--- PHASE 3 : PORTAILS FRANCE (Welcome to the Jungle, HelloWork) ---")
    fr_keywords = [
        "développeur full stack junior",
        "développeur python",
        "ingénieur devops junior",
        "ingénieur IA",
        "data scientist junior",
        "développeur react",
        "machine learning engineer junior",
        "software engineer junior",
    ]

    for kw in fr_keywords:
        print(f"[*] Scan France : '{kw}'...")
        try:
            wttj = [j for j in scrape_wttj(kw) if is_real_job_offer(j["title"], j["description"])]
            all_jobs.extend(wttj)
            print(f"    -> Welcome to the Jungle : +{len(wttj)}")
        except Exception as e:
            print(f"    [!] WTTJ skip : {e}")

        try:
            hw = [j for j in scrape_hellowork(kw) if is_real_job_offer(j["title"], j["description"])]
            all_jobs.extend(hw)
            print(f"    -> HelloWork : +{len(hw)}")
        except Exception as e:
            print(f"    [!] HelloWork skip : {e}")

        time.sleep(0.4)

    # =========================================================================
    # PHASE 4 : Dédoublonnage
    # =========================================================================
    total_raw = len(all_jobs)
    print(f"\n[*] Total brut collecté : {total_raw} offres.")

    unique_jobs = deduplicate_jobs(all_jobs)
    duplicates_removed = total_raw - len(unique_jobs)
    print(
        f"[+] Dédoublonnage terminé : {len(unique_jobs)} offres uniques "
        f"({duplicates_removed} doublons éliminés)."
    )

    # Stats par site
    by_site: Dict[str, int] = {}
    for j in unique_jobs:
        site = j.get("site", "unknown")
        by_site[site] = by_site.get(site, 0) + 1
    print("\n--- Répartition par source ---")
    for site, n in sorted(by_site.items(), key=lambda x: -x[1]):
        print(f"    {site:30s} : {n}")

    return unique_jobs


def main():
    print("=" * 60)
    print(" SOURCING MULTI-SITES — MAROC + FRANCE")
    print(" LinkedIn | Indeed | Glassdoor | Google Jobs | Bayt")
    print(" ReKrute | Emploi.ma | MarocAnnonces | WTTJ | HelloWork")
    print("=" * 60)

    search_queries = [
        "Junior Full Stack Developer",
        "Développeur Full Stack Junior CDI",
        "Développeur Python Débutant",
        "Junior DevOps Engineer",
        "Ingénieur DevOps Débutant",
        "Junior AI Engineer",
        "Ingénieur IA LLM Junior",
        "Junior Data Scientist",
        "Développeur React Next.js Junior",
        "Junior Software Engineer",
        "Ingénieur Logiciel Premier Emploi",
        "Développeur FastAPI React",
    ]

    jobs = collect_jobs_multiplatform(
        search_terms=search_queries,
        results_per_term=10,
        hours_old=168,
    )

    output_file = Path("raw_jobs.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"[+] Total d'offres uniques sauvegardées : {len(jobs)}")
    print(f"[+] Sauvegardées dans : {output_file.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
