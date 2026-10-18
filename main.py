import os
import subprocess
import sys
import time
import json
from pathlib import Path


def print_step(step_num: int, title: str, total: int = 5):
    print("\n" + "=" * 65)
    print(f" [ÉTAPE {step_num}/{total}] : {title.upper()}")
    print("=" * 65)


def run_module(script_name: str, step_num: int, title: str):
    print_step(step_num, title)
    script_path = Path(script_name)
    if not script_path.exists():
        print(f"[!] Erreur : Le fichier '{script_name}' est introuvable.")
        sys.exit(1)

    t0 = time.time()
    result = subprocess.run([sys.executable, str(script_path)])
    elapsed = time.time() - t0

    if result.returncode != 0:
        print(f"\n[!] Arrêt du programme : erreur survenue dans {script_name}.")
        sys.exit(result.returncode)

    print(f"[✓] Terminé avec succès en {elapsed:.1f} s.")


def display_final_summary():
    """Affiche les résultats directement dans la console."""
    results_path = Path("final_job_opportunities.json")
    if not results_path.exists():
        print("\n[!] Aucun fichier de résultats trouvé.")
        return

    with open(results_path, "r", encoding="utf-8") as f:
        jobs = json.load(f)

    print("\n" + "═" * 65)
    print(f"  🎯 OFFRES QUALIFIÉES POUR PREMIER EMPLOI ({len(jobs)} TROUVÉES)")
    print("═" * 65)

    if not jobs:
        print("[-] Aucune offre n'a atteint le score minimal requis.")
        return

    for idx, job in enumerate(jobs, 1):
        print(f"\n[{idx}] {job.get('title')} — {job.get('company')}")
        print(f"    📍 Lieu       : {job.get('location', 'N/A')}")
        print(f"    📊 Score Match: {job.get('match_score')}%")
        print(f"    📧 Email RH   : {job.get('rh_email')}")
        print(f"    🔗 URL Offre  : {job.get('job_url')}")
        print(f"    💡 Avis IA    : {job.get('evaluation_summary')}")
        print("-" * 65)

    csv_path = Path("final_job_opportunities.csv").resolve()
    print(f"\n[+] Tableau récapitulatif exporté : {csv_path}")


def main():
    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║            AI JOB MATCHER & RH CONTACT FINDER                 ║")
    print("║        Recherche Premier Emploi / Débutant (Local)            ║")
    print("╚═══════════════════════════════════════════════════════════════╝")

    t_start = time.time()

    # 1. Parsing du CV
    run_module("cv_parser.py", 1, "Analyse et structuration du CV")

    # 2. Scraping multi-plateformes (LinkedIn, Indeed, Google Jobs)
    run_module("job_scraper.py", 2, "Collecte des offres d'emploi (7 jours)")

    # 3. Matching LLM (Filtrage Junior / Rejet des stages)
    run_module("job_matcher.py", 3, "Matching sémantique par IA")

    # 4. Extraction et enrichissement des emails RH
    run_module("rh_finder.py", 4, "Recherche des emails RH")

    # Affichage du bilan dans le terminal
    display_final_summary()

    # 5. Lettres + candidatures (dry-run sauf si AUTO_APPLY_SEND=true)
    run_module("auto_apply.py", 5, "Candidatures auto (CV + lettre) — dry-run par défaut")

    total_time = time.time() - t_start
    print(f"\nExecution globale terminée en {total_time:.1f} secondes.")


if __name__ == "__main__":
    main()