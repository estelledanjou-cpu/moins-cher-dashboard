# moins-cher-dashboard

Dashboard "Moins Cher" — pipeline de données SEO.

## Contenu de ce dossier

| Fichier | Rôle |
|---|---|
| `generate_data.py` | Interroge BigQuery, génère `output/data.json` |
| `requirements.txt` | Dépendances Python |
| `dashboard-moins-cher-LIVE.html` | Le dashboard, adapté pour charger les données en direct via `fetch()` au lieu d'avoir les données codées en dur |
| `.github/workflows/update-data.yml` | Automatisation : relance `generate_data.py` chaque jour et publie le résultat |

## Comment ça marche

```
BigQuery  →  generate_data.py  →  output/data.json  →  hébergé quelque part (ex: files.papernest.com)
                                                              ↓
                                              dashboard-moins-cher-LIVE.html (fetch au chargement)
```

## Mise en route, étape par étape

### 1. Tester en local

```bash
pip install -r requirements.txt --break-system-packages
gcloud auth application-default login   # authentification BigQuery
python generate_data.py
```

Ça doit produire un fichier `output/data.json`. Vérifie son contenu avant d'aller plus loin.

### 2. Configurer le dashboard pour pointer vers la bonne URL

Dans `dashboard-moins-cher-LIVE.html`, cherche cette ligne (en haut du `<script>`) :

```js
const DATA_URL = 'https://files.papernest.com/internal/seo/moins-cher-project-data';
```

Remplace-la par l'URL réelle où `data.json` sera hébergé.

### 3. Héberger `data.json` quelque part d'accessible

Le plus simple pour rester cohérent avec Chèques Énergie : le même mécanisme qu'eux
utilisent déjà pour `files.papernest.com`. Demande à la personne qui gère ce
mécanisme comment publier un nouveau fichier au même endroit — ou adapte le
workflow GitHub Actions (`update-data.yml`, section "Publication") selon
l'hébergement réel utilisé chez vous (bucket Google Cloud Storage, autre serveur...).

### 4. Automatiser avec GitHub Actions

1. Pousse ce dossier dans un repo GitHub
2. Crée un compte de service Google Cloud avec accès en lecture à BigQuery
   (rôle `BigQuery Data Viewer` + `BigQuery Job User` sur le projet `souscritoo-1343`)
3. Télécharge sa clé JSON, colle son contenu dans un secret GitHub nommé
   `GCP_SA_KEY` (Settings → Secrets and variables → Actions → New repository secret)
4. Adapte la section "Publication du fichier" du workflow selon votre hébergement réel
5. Le workflow tournera automatiquement chaque jour à 6h UTC (modifiable dans le
   `cron` du fichier `.yml`), ou peut être lancé manuellement depuis l'onglet
   "Actions" de GitHub ("workflow_dispatch")

## Historique / contexte

Ce pipeline reproduit, pour les 8 pages "moins cher" (énergie), le même
principe que le dashboard Chèques Énergie
(`https://files.papernest.com/internal/ops/cheque-energie-project`) :
un fichier HTML autonome qui lit un JSON statique régénéré quotidiennement,
sans jamais appeler BigQuery directement depuis le navigateur.

Les 4 requêtes SQL utilisées dans `generate_data.py` ont été construites et
vérifiées manuellement (voir l'historique de la conversation qui a produit ce
dashboard pour le détail des vérifications de cohérence des données).
