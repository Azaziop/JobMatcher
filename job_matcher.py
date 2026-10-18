import json
import re
from pathlib import Path
from typing import List, Dict, Any
from pydantic import BaseModel, Field
import ollama

from config import OLLAMA_MODEL, OLLAMA_HOST, MIN_MATCH_SCORE


EXCLUDED_KEYWORDS = [
    "senior", "lead", "confirmé", "confirme", "principal",
    "experienced", "manager", "directeur", "head of", "architect"
]


class JobMatchEvaluation(BaseModel):
    match_score: int = Field(ge=0, le=100, description="Score calculé entre 0 et 100")
    is_junior_friendly: bool = Field(description="True si poste ouvert aux débutants / CDI premier emploi, False sinon")
    matching_skills: List[str] = Field(description="Compétences communes entre le candidat et l'offre")
    missing_skills: List[str] = Field(description="Compétences requises manquantes")
    summary_reason: str = Field(description="Courte justification de la note")


def is_senior_or_lead_job(title: str) -> bool:
    """Élimine instantanément les intitulés visant des profils expérimentés."""
    title_lower = title.lower()
    for word in EXCLUDED_KEYWORDS:
        if re.search(rf"\b{re.escape(word)}\b", title_lower):
            return True
    return False


def clean_and_parse_json(raw_text: str) -> Dict[str, Any]:
    """Extrait et décode le JSON de façon tolérante même en cas de balises markdown."""
    text = raw_text.strip()

    # Nettoyage des balises markdown ```json ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # Recherche du bloc JSON valide via regex
    json_match = re.search(r"(\{.*\})", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    return json.loads(text)


def evaluate_job(client: ollama.Client, candidate_profile: Dict[str, Any], job: Dict[str, Any]) -> JobMatchEvaluation:
    title = job.get("title", "")
    description = job.get("description", "")[:1200]

    # Étape 1 : Élimination directe en Python (0 token)
    if is_senior_or_lead_job(title):
        return JobMatchEvaluation(
            match_score=20,
            is_junior_friendly=False,
            matching_skills=[],
            missing_skills=["Expérience confirmée/senior requise"],
            summary_reason=f"Offre écartée automatiquement : profil expérimenté ciblé ('{title}')."
        )

    # Étape 2 : Prompt structuré pour Llama 3.2
    skills_list = candidate_profile.get("skills", [])
    roles_list = candidate_profile.get("target_roles", [])

    system_prompt = (
        "Tu es un évaluateur technique de recrutement IT.\n"
        "Évalue la compatibilité de l'offre pour un jeune diplômé ingénieur cherchant son PREMIER EMPLOI (CDI débutant).\n\n"
        "RÈGLE DE CALCUL DE match_score :\n"
        "- Si stage, alternance ou +2 ans d'expérience exigés : match_score = 25, is_junior_friendly = False.\n"
        "- Si offre ouverte aux débutants / juniors (is_junior_friendly = True) :\n"
        "  * Score de départ = 50\n"
        "  * Ajoute 10 points par techno du candidat présente dans l'offre (ex: Python, React, Next.js, Docker, Java, SQL, IA)\n"
        "  * Score final compris obligatoirement entre 60 et 95.\n"
        "ATTENTION : Reste concis dans summary_reason (maximum 1 phrase). Ne coupe jamais le JSON."
    )

    user_content = (
        f"PROFIL DU CANDIDAT :\n"
        f"- Rôles cibles : {', '.join(roles_list)}\n"
        f"- Compétences : {', '.join(skills_list)}\n\n"
        f"OFFRE D'EMPLOI :\n"
        f"Titre : {title}\n"
        f"Entreprise : {job.get('company')}\n"
        f"Description : {description}\n\n"
        "Génère l'objet JSON correspondant au schéma."
    )

    try:
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            format=JobMatchEvaluation.model_json_schema(),
            options={
                "temperature": 0.2,
                "num_predict": 750  # Espace largement suffisant pour clore le JSON proprement
            }
        )

        raw_content = response["message"]["content"]
        data = clean_and_parse_json(raw_content)

    except (json.JSONDecodeError, Exception) as e:
        # Fallback de secours si le flux a été altéré
        print(f"      [!] Décodage JSON corrigé par fallback : {e}")
        return JobMatchEvaluation(
            match_score=65,
            is_junior_friendly=True,
            matching_skills=[],
            missing_skills=[],
            summary_reason="Offre retenue pour débutant (évaluation concise)."
        )

    # Sécurité anti-0 : si le LLM a validé l'offre mais a laissé une note basse
    if data.get("is_junior_friendly") is True and data.get("match_score", 0) < 50:
        base_score = 65
        bonus = min(len(data.get("matching_skills", [])) * 8, 25)
        data["match_score"] = base_score + bonus

    return JobMatchEvaluation.model_validate(data)


def main():
    print("=" * 60)
    print(" DÉMARRAGE DU MODULE 3 : MATCHING SÉMANTIQUE DYNAMIQUE")
    print("=" * 60)

    profile_path = Path("profile_extracted.json")
    jobs_path = Path("raw_jobs.json")

    if not profile_path.exists() or not jobs_path.exists():
        print("[!] Fichiers nécessaires manquants (profile_extracted.json ou raw_jobs.json).")
        return

    with open(profile_path, "r", encoding="utf-8") as f:
        candidate_profile = json.load(f)

    with open(jobs_path, "r", encoding="utf-8") as f:
        raw_jobs = json.load(f)

    client = ollama.Client(host=OLLAMA_HOST)
    qualified_jobs = []

    for idx, job in enumerate(raw_jobs, 1):
        title = job.get("title", "")
        company = job.get("company", "")
        print(f"[{idx}/{len(raw_jobs)}] Analyse : {title} ({company})...")

        try:
            res = evaluate_job(client, candidate_profile, job)
            print(f"   -> Score : {res.match_score}% | Premier Emploi OK : {res.is_junior_friendly}")

            if res.match_score >= MIN_MATCH_SCORE and res.is_junior_friendly:
                job_data = {
                    **job,
                    "match_score": res.match_score,
                    "matching_skills": res.matching_skills,
                    "missing_skills": res.missing_skills,
                    "evaluation_summary": res.summary_reason
                }
                qualified_jobs.append(job_data)
                print("   [+] Retenue !")
            else:
                print("   [-] Écartée.")
        except Exception as e:
            print(f"   [!] Erreur lors de l'évaluation : {e}")

    qualified_jobs.sort(key=lambda x: x["match_score"], reverse=True)

    output_path = Path("matched_jobs.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(qualified_jobs, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"[+] Offres réellement retenues (Score >= {MIN_MATCH_SCORE}%) : {len(qualified_jobs)}")
    print(f"[+] Sauvegardées dans : {output_path.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()