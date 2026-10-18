import time
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def scrape_rekrute(keyword: str, max_pages: int = 2) -> List[Dict[str, Any]]:
    """Scrape ReKrute.com — tout le Maroc."""
    results = []
    base_url = "https://www.rekrute.com"

    for page in range(1, max_pages + 1):
        url = f"{base_url}/offres.html?s=1&p={page}&keyword={keyword}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.content, "html.parser")
            postings = soup.find_all("li", class_="post-id")
            if not postings:
                break

            for post in postings:
                title_tag = post.find("a", class_="titreJob") or post.find("h2")
                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                href = title_tag.get("href") or ""
                if title_tag.find("a"):
                    href = title_tag.find("a").get("href", href)
                link = f"{base_url}{href}" if href.startswith("/") else href

                company = "Entreprise Recruteuse"
                company_box = post.find("div", class_="holder-info")
                if company_box:
                    img = company_box.find("img")
                    if img and img.get("alt"):
                        company = img.get("alt").strip()

                loc_tag = post.find("span", class_="location") or post.find("li", class_="location")
                location = loc_tag.get_text(" ", strip=True) if loc_tag else "Maroc"
                if "maroc" not in location.lower() and "morocco" not in location.lower():
                    location = f"{location}, Morocco".strip(", ")

                desc_box = post.find("div", class_="info") or post.find("span", class_="section")
                description = desc_box.get_text(" ", strip=True) if desc_box else title

                results.append({
                    "id": link,
                    "site": "rekrute",
                    "title": title,
                    "company": company,
                    "location": location,
                    "job_url": link,
                    "description": description,
                    "date_posted": "Récent",
                    "search_query": keyword,
                })
            time.sleep(0.4)
        except Exception as e:
            print(f"      [!] ReKrute skip: {e}")
            break

    return results


def scrape_emploi_ma(keyword: str, max_pages: int = 2) -> List[Dict[str, Any]]:
    """Scrape Emploi.ma — tout le Maroc."""
    results = []
    base_url = "https://www.emploi.ma"
    seen = set()

    for page in range(0, max_pages):
        url = f"{base_url}/recherche-jobs-maroc?title={keyword}&page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.content, "html.parser")
            cards = soup.select("div.card-job")
            if not cards:
                # Fallback: liens d'offres
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/offre-emploi-maroc/" not in href:
                        continue
                    title = a.get_text(" ", strip=True)
                    if not title or len(title) < 8:
                        continue
                    link = f"{base_url}{href}" if href.startswith("/") else href
                    if link in seen:
                        continue
                    seen.add(link)
                    results.append({
                        "id": link,
                        "site": "emploi_ma",
                        "title": title,
                        "company": "Entreprise Emploi.ma",
                        "location": "Maroc",
                        "job_url": link,
                        "description": title,
                        "date_posted": "Récent",
                        "search_query": keyword,
                    })
                break

            for card in cards:
                title_elem = card.select_one("h3 a, h5 a, a.card-job-link, .card-job-detail a")
                if not title_elem:
                    continue
                title = title_elem.get_text(" ", strip=True)
                href = title_elem.get("href", "")
                link = f"{base_url}{href}" if href.startswith("/") else href
                if not title or not link or link in seen:
                    continue
                seen.add(link)

                company_elem = card.select_one(".card-job-company, .company-title, .company")
                company = company_elem.get_text(" ", strip=True) if company_elem else "Confidentiel"

                loc_elem = card.select_one(".card-job-location, .location, .job-location")
                location = loc_elem.get_text(" ", strip=True) if loc_elem else "Maroc"
                if "maroc" not in location.lower():
                    location = f"{location}, Morocco".strip(", ")

                desc_elem = card.select_one(".card-job-description, .text-secondary")
                description = desc_elem.get_text(" ", strip=True) if desc_elem else title

                results.append({
                    "id": link,
                    "site": "emploi_ma",
                    "title": title,
                    "company": company,
                    "location": location,
                    "job_url": link,
                    "description": description,
                    "date_posted": "Récent",
                    "search_query": keyword,
                })
            time.sleep(0.4)
        except Exception as e:
            print(f"      [!] Emploi.ma skip: {e}")
            break

    return results


def scrape_marocannonces(keyword: str) -> List[Dict[str, Any]]:
    """Scrape MarocAnnonces — offres d'emploi IT tout Maroc."""
    results = []
    urls = [
        f"https://www.marocannonces.com/maroc/offres-emploi-b309.html?kw={keyword}",
        f"https://www.marocannonces.com/categorie/309/Offres-emploi/Informatique-Multimedia-Internet.html?kw={keyword}",
    ]
    seen = set()

    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.content, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/annonce/" not in href and "/Offres-emploi/annonce/" not in href:
                    continue
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 10:
                    continue
                full_link = href if href.startswith("http") else f"https://www.marocannonces.com/{href.lstrip('/')}"
                if full_link in seen:
                    continue
                seen.add(full_link)

                results.append({
                    "id": full_link,
                    "site": "marocannonces",
                    "title": title,
                    "company": "Recruteur MarocAnnonces",
                    "location": "Maroc",
                    "job_url": full_link,
                    "description": title,
                    "date_posted": "Récent",
                    "search_query": keyword,
                })
            time.sleep(0.3)
        except Exception as e:
            print(f"      [!] MarocAnnonces skip: {e}")

    return results
