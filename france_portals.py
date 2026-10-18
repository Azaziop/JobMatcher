import json
import time
import urllib.parse
from typing import List, Dict, Any
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

WTTJ_APP_ID = "CSEKHVMS53"
WTTJ_API_KEY = "4bd8f6215d0cc52b26430765769e65a0"
WTTJ_ALGOLIA = "https://csekhvms53-dsn.algolia.net/1/indexes/*/queries"


def scrape_wttj(keyword: str, hits_per_page: int = 20, max_pages: int = 1) -> List[Dict[str, Any]]:
    """Scrape Welcome to the Jungle (France) via Algolia public search."""
    results: List[Dict[str, Any]] = []
    headers = {
        **HEADERS,
        "X-Algolia-Application-Id": WTTJ_APP_ID,
        "X-Algolia-API-Key": WTTJ_API_KEY,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.welcometothejungle.com",
        "Referer": "https://www.welcometothejungle.com/",
    }

    for page in range(max_pages):
        params = urllib.parse.urlencode(
            {
                "query": keyword,
                "hitsPerPage": hits_per_page,
                "page": page,
                "filters": "website.reference:wttj_fr",
            },
            safe=":,/",
        )
        payload = {"requests": [{"indexName": "wk_cms_jobs_production", "params": params}]}
        try:
            resp = requests.post(
                WTTJ_ALGOLIA,
                headers=headers,
                data=json.dumps(payload),
                timeout=15,
            )
            if resp.status_code != 200:
                break
            hits = resp.json().get("results", [{}])[0].get("hits", [])
            if not hits:
                break

            for hit in hits:
                title = (hit.get("name") or "").strip()
                if not title:
                    continue

                org = hit.get("organization") or {}
                company = org.get("name") if isinstance(org, dict) else "Entreprise WTTJ"
                org_slug = org.get("slug") if isinstance(org, dict) else ""
                job_slug = hit.get("slug") or ""

                offices = hit.get("offices") or []
                if offices and isinstance(offices[0], dict):
                    city = offices[0].get("city") or offices[0].get("name") or ""
                    country = offices[0].get("country_code") or "FR"
                    location = f"{city}, France".strip(", ") if city else "France"
                    if country and country.upper() not in ("FR", "FRA", "FRANCE"):
                        location = f"{city}, {country}".strip(", ")
                else:
                    location = "France"

                profile = hit.get("profile") or ""
                contract = ""
                ct = hit.get("contract_type_names") or {}
                if isinstance(ct, dict):
                    contract = ct.get("fr") or ct.get("en") or ""
                description = " | ".join(x for x in [contract, profile, title] if x)

                if org_slug and job_slug:
                    job_url = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{job_slug}"
                else:
                    ref = hit.get("reference") or ""
                    job_url = f"https://www.welcometothejungle.com/fr/jobs?query={urllib.parse.quote(title)}"
                    if ref:
                        job_url = f"https://www.welcometothejungle.com/fr/jobs/{ref}"

                results.append({
                    "id": hit.get("objectID") or hit.get("reference") or job_url,
                    "site": "welcome_to_the_jungle",
                    "title": title,
                    "company": company or "Entreprise WTTJ",
                    "location": location,
                    "job_url": job_url,
                    "description": description,
                    "date_posted": str(hit.get("published_at") or "Récent"),
                    "search_query": keyword,
                })
            time.sleep(0.35)
        except Exception as e:
            print(f"      [!] WTTJ skip: {e}")
            break

    return results


def scrape_hellowork(keyword: str, max_pages: int = 1) -> List[Dict[str, Any]]:
    """Scrape HelloWork (France) — pages de recherche HTML."""
    results: List[Dict[str, Any]] = []
    from bs4 import BeautifulSoup

    seen = set()
    for page in range(1, max_pages + 1):
        url = (
            "https://www.hellowork.com/fr-fr/emploi/recherche.html"
            f"?k={urllib.parse.quote(keyword)}&l=France&p={page}"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.content, "html.parser")
            cards = soup.select("[data-id-offer], .offer--content, article.offer, .hw-offer")
            links = []
            if cards:
                for card in cards:
                    a = card.find("a", href=True)
                    if a:
                        links.append((card, a))
            else:
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/emplois/" in href or "/emploi/" in href:
                        if a.get_text(" ", strip=True):
                            links.append((a, a))

            if not links:
                break

            for parent, a in links:
                href = a["href"]
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 8:
                    continue
                link = href if href.startswith("http") else f"https://www.hellowork.com{href}"
                if link in seen:
                    continue
                seen.add(link)

                company = "Entreprise HelloWork"
                loc = "France"
                if hasattr(parent, "select_one"):
                    c = parent.select_one(".company, .offer__company, [data-cy='company']")
                    if c:
                        company = c.get_text(" ", strip=True) or company
                    l = parent.select_one(".location, .offer__location, [data-cy='location']")
                    if l:
                        loc = l.get_text(" ", strip=True) or loc

                results.append({
                    "id": link,
                    "site": "hellowork",
                    "title": title,
                    "company": company,
                    "location": loc if "france" in loc.lower() or "," in loc else f"{loc}, France",
                    "job_url": link,
                    "description": title,
                    "date_posted": "Récent",
                    "search_query": keyword,
                })
            time.sleep(0.5)
        except Exception as e:
            print(f"      [!] HelloWork skip: {e}")
            break

    return results
