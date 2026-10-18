# n8n — Ajouter l'envoi auto CV + lettres

## Flux cible

```
Planification 09h00
  → POST /run-pipeline
  → POST /auto-apply?send=true&max=10
  → Formater message
  → Telegram
```

## Option A — Importer le workflow

1. Ouvre http://localhost:5678
2. **Workflows → Import from File**
3. Sélectionne `n8n_workflow_jobmatcher.json`
4. Reconnecte ton credential **Telegram** sur le dernier nœud
5. Active le workflow

## Option B — Modifier le workflow existant (manuel)

1. Ouvre ton workflow actuel
2. Entre **Exécuter Pipeline JobMatcher** et **Envoyer Alerte Telegram**, ajoute un nœud **HTTP Request** :
   - **Name** : `Envoyer CV + Lettres (Gmail)`
   - **Method** : `POST`
   - **URL** : `http://jobmatcher-api:8000/auto-apply?send=true&max=10`
   - **Timeout** : `900000` (15 min)
3. (Recommandé) Ajoute un nœud **Code** pour formater le résumé Telegram avec le JSON renvoyé (`sent`, `errors`, `applications`)
4. Relie : Pipeline → Auto-apply → Telegram
5. Save + Activate

## Redémarrer l'API (SMTP + nouveau endpoint)

```powershell
cd C:\JobMatcher
docker compose up -d --build jobmatcher-api
```

Vérifier :

```powershell
curl http://localhost:8000/health
curl -X POST "http://localhost:8000/auto-apply?send=false&max=2"
```

## Variables SMTP

Dans `.env` (déjà configuré) :

- `SMTP_USER=zaouiazaziop@gmail.com`
- `SMTP_PASSWORD=...` (mot de passe d'application)
- `AUTO_APPLY_MAX=10`
- `MIN_APPLY_SCORE=80`

Le conteneur lit `.env` via `env_file` + `docker-compose.yml`.
