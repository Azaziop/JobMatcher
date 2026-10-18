import json
import os
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field
from pypdf import PdfReader
import ollama

from config import OLLAMA_MODEL, OLLAMA_HOST, PDF_PATH


class CandidateProfile(BaseModel):
    full_name: str = Field(description="Nom complet du candidat")
    email: Optional[str] = Field(description="Email de contact")
    phone: Optional[str] = Field(description="Numéro de téléphone")
    location: Optional[str] = Field(description="Ville ou pays de résidence")
    seniority_level: str = Field(description="Niveau d'expérience")
    target_roles: List[str] = Field(description="Rôles cibles débutants / premier emploi")
    skills: List[str] = Field(description="Compétences techniques clés")
    short_bio: str = Field(description="Résumé court du profil")


def extract_text_from_pdf(pdf_path: str) -> str:
    path = Path(pdf_path)
    if not path.exists():
        local_pdfs = list(Path(".").glob("*.pdf"))
        if not local_pdfs:
            raise FileNotFoundError(f"Aucun PDF trouvé à : {pdf_path}")
        path = local_pdfs[0]

    reader = PdfReader(str(path))
    text = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"
    return text.strip()


def parse_cv_with_ollama(cv_text: str) -> CandidateProfile:
    client = ollama.Client(host=OLLAMA_HOST)

    system_prompt = (
        "Tu es un assistant d'extraction de données. "
        "Extrais les données du CV et remplis STRICTEMENT le modèle JSON d'exemple fourni. "
        "Ne renvoie JAMAIS de métadonnées de schéma comme 'properties' ou 'definitions'. "
        "Retourne UNIQUEMENT un objet JSON avec les valeurs extraites du CV."
    )

    # Exemple clair directement interprétable par llama3.2:3b
    json_template = {
        "full_name": "Nom et Prénom",
        "email": "email@example.com",
        "phone": "+212...",
        "location": "Casablanca, Maroc",
        "seniority_level": "Premier emploi / Junior (ingénieur d'état)",
        "target_roles": [
            "Junior Full Stack Developer",
            "Junior AI / LLM Engineer",
            "Junior DevOps Engineer",
            "Junior Data / MLOps Engineer"
        ],
        "skills": ["Python", "FastAPI", "React", "Next.js", "Docker", "Kubernetes", "LangChain", "SQL", "AWS"],
        "short_bio": "Court résumé professionnel axé premier emploi (2-3 phrases)."
    }

    user_prompt = (
        f"Génère un JSON rempli avec les données réelles de ce CV selon EXACTEMENT cette structure :\n"
        f"{json.dumps(json_template, ensure_ascii=False, indent=2)}\n\n"
        f"TEXTE DU CV :\n{cv_text}\n\n"
        "RÉPONSE JSON :"
    )

    # Ollama supporte aussi de passer le schéma Pydantic dans format depuis les versions récentes
    response = client.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        format="json",
        options={"temperature": 0.1}
    )

    raw_json = response["message"]["content"]
    
    # Nettoyage de sécurité si le modèle encapsule les données sous une clé racine
    data = json.loads(raw_json)
    if "properties" in data:
        # Fallback si le modèle a encore imbriqué les propriétés
        raise ValueError("Le modèle a renvoyé la structure de métadonnées au lieu des valeurs.")

    return CandidateProfile.model_validate(data)

def run():
    print("=" * 60)
    print(" DÉMARRAGE DU MODULE 1 : EXTRACTION CV (OLLAMA LOCAL)")
    print("=" * 60)
    
    text = extract_text_from_pdf(PDF_PATH)
    print(f"[+] {len(text)} caractères extraits du PDF.")
    print(f"[*] Traitement local via Ollama ({OLLAMA_MODEL})...")
    
    profile = parse_cv_with_ollama(text)
    
    output_file = Path("profile_extracted.json")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(profile.model_dump_json(indent=2))

    print("=" * 60)
    print(f"[+] Profil sauvegardé avec succès dans '{output_file.resolve()}'")
    print(f"-> Candidat : {profile.full_name}")
    print(f"-> Rôles cibles : {', '.join(profile.target_roles)}")
    print("=" * 60)


if __name__ == "__main__":
    run()