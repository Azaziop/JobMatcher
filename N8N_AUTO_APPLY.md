# Tout visualiser dans n8n

Import : `n8n_workflow_jobmatcher.json`

## Où regarder

1. Ouvre **Executions** → dernière run  
2. Clique les nœuds avec 📊 / 📧 / ⚠️ / 📱  
3. Passe en vue **Table** → **1 ligne = 1 offre / 1 mail / 1 échec**

| Nœud | Contenu visible |
|------|-----------------|
| 📊 Voir compteurs | scrapées, qualifiées, mails envoyés |
| 📊 TABLEAU OFFRES | toutes les offres (score, RH, URL) |
| 📊 Voir Excel | chemin `.xlsx` + nombre d’offres |
| 📧 TABLEAU MAILS ENVOYÉS | chaque candidature `sent` |
| ⚠️ TABLEAU ÉCHECS | bounce / erreur + URL + titre |
| 📱 Telegram | jobs notifiés |

## Flux

```
09h00 → POST /run-daily → poll status
     → GET /offers → tableau offres (n8n)
                  → Excel
     → check-bounces → tableau bounces
     → GET /applications → tableaux sent + échecs
     → si échecs → Telegram (URLs)
```

Dedup = déjà fait par l’API (même URL = pas de re-envoi). Excel = `final_job_opportunities.xlsx` dans le volume API.
