#!/usr/bin/env python3
"""
Génère le fichier data.json consommé par le dashboard "Moins Cher".

Ce script :
  1. Interroge BigQuery (3 tables : business, GSC, GA4)
  2. Agrège les doublons éventuels par (page, semaine)
  3. Fusionne le tout dans un JSON unique, au format attendu par le dashboard
  4. Écrit le résultat dans ./output/data.json (à uploader ensuite vers
     files.papernest.com, ou tout autre hébergement statique)

Prérequis :
  pip install google-cloud-bigquery pandas --break-system-packages
  Authentification : `gcloud auth application-default login`
  (ou une clé de service compte configurée via GOOGLE_APPLICATION_CREDENTIALS)

Usage :
  python generate_data.py
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from google.cloud import bigquery

PROJECT_ID = "souscritoo-1343"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_FILE = OUTPUT_DIR / "data.json"

# Périmètre : les 8 pages "moins cher" (énergie uniquement)
URL_FILTER_BUSINESS = "papernest.com/demarches-energie/moins-cher%"
URL_FILTER_GSC = "%demarches-energie/moins-cher%"
URL_FILTER_GA4 = "%demarches-energie/moins-cher%"

PAGES = [
    {"path": "/demarches-energie/moins-cher/", "label": "Hub · Moins cher"},
    {"path": "/demarches-energie/moins-cher/elec/", "label": "Électricité"},
    {"path": "/demarches-energie/moins-cher/elec/professionnels/", "label": "Électricité · Pro"},
    {"path": "/demarches-energie/moins-cher/elec/verte/", "label": "Électricité · Verte"},
    {"path": "/demarches-energie/moins-cher/fournisseur-electricite/", "label": "Fournisseur Électricité"},
    {"path": "/demarches-energie/moins-cher/gaz/", "label": "Gaz"},
    {"path": "/demarches-energie/moins-cher/gaz/pro/", "label": "Gaz · Pro"},
    {"path": "/demarches-energie/moins-cher/gaz/vert/", "label": "Gaz · Vert"},
]


def get_client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT_ID)


# ---------------------------------------------------------------------------
# 1. Requête business (impressions, clics, funnel, finance estimée)
# ---------------------------------------------------------------------------
BUSINESS_QUERY = f"""
SELECT
  REGEXP_REPLACE(REGEXP_REPLACE(url, r'^papernest\\.com', ''), r'\\?.*$', '') AS path,
  date_week,
  impressions, clicks_gsc AS clicks, position,
  prospects, pprospects AS pp, aprospects AS ap,
  clients_raw AS rc, clients_net,
  CM1_e AS cm1, CM2_e AS cm2, CM3_e AS cm3, processing_costs AS pc
FROM `{PROJECT_ID}.bi_dashboard.bi_dashboard_seo_kpis_per_url_papernest_com`
WHERE url LIKE '{URL_FILTER_BUSINESS}'
ORDER BY path, date_week
"""


def fetch_business(client: bigquery.Client) -> pd.DataFrame:
    df = client.query(BUSINESS_QUERY).to_dataframe()
    if df.empty:
        return df

    df["pos_x_impr"] = df["position"].fillna(0) * df["impressions"]
    agg = df.groupby(["path", "date_week"], as_index=False).agg(
        impressions=("impressions", "sum"),
        clicks=("clicks", "sum"),
        pos_x_impr=("pos_x_impr", "sum"),
        prospects=("prospects", "sum"),
        pp=("pp", "sum"),
        ap=("ap", "sum"),
        rc=("rc", "sum"),
        clients_net=("clients_net", "sum"),
        cm1=("cm1", "sum"),
        cm2=("cm2", "sum"),
        cm3=("cm3", "sum"),
        pc=("pc", "sum"),
    )
    agg["position"] = agg.apply(
        lambda r: (r["pos_x_impr"] / r["impressions"]) if r["impressions"] > 0 else None,
        axis=1,
    )
    agg = agg.drop(columns=["pos_x_impr"])
    agg["date_week"] = agg["date_week"].astype(str)
    return agg


# ---------------------------------------------------------------------------
# 2. Requête GSC — queries mensuelles vs M-1
# ---------------------------------------------------------------------------
GSC_QUERY = f"""
WITH monthly_q AS (
  SELECT
    REGEXP_REPLACE(REGEXP_REPLACE(sites, r'^https?://(www\\.)?papernest\\.com', ''), r'\\?.*$', '') AS path,
    query,
    DATE_TRUNC(CAST(dates AS DATE), MONTH) AS month,
    SUM(CAST(clicks AS INT64)) AS clicks,
    SUM(CAST(impressions AS INT64)) AS impressions,
    SAFE_DIVIDE(SUM(CAST(position AS FLOAT64) * CAST(impressions AS INT64)), SUM(CAST(impressions AS INT64))) AS avg_position
  FROM `{PROJECT_ID}.souscritoo_bi.bi_cleaned_seo_google_search_console`
  WHERE sites LIKE '{URL_FILTER_GSC}'
  GROUP BY path, query, month
)
SELECT
  path, query, month, clicks, impressions, avg_position,
  LAG(clicks) OVER (PARTITION BY path, query ORDER BY month) AS clicks_m1,
  LAG(avg_position) OVER (PARTITION BY path, query ORDER BY month) AS position_m1
FROM monthly_q
ORDER BY path, month DESC, clicks DESC
"""


def fetch_gsc_queries(client: bigquery.Client) -> pd.DataFrame:
    return client.query(GSC_QUERY).to_dataframe()


# ---------------------------------------------------------------------------
# 3a. GA4 — sessions / durée / engagement, par semaine
# ---------------------------------------------------------------------------
GA4_SESSIONS_QUERY = f"""
WITH sessions AS (
  SELECT
    ga_session_id,
    REGEXP_REPLACE(REGEXP_REPLACE(ANY_VALUE(event_location), r'^https?://(www\\.)?papernest\\.com', ''), r'\\?.*$', '') AS path,
    ANY_VALUE(event_date) AS event_date,
    MAX(duration_since_session_start) AS session_duration,
    LOGICAL_OR(is_user_session_engaged) AS engaged
  FROM `{PROJECT_ID}.team_seo.cleaned_ga4_events_papernest_com`
  WHERE event_location LIKE '{URL_FILTER_GA4}'
  GROUP BY ga_session_id
)
SELECT
  path,
  DATE_TRUNC(event_date, WEEK(MONDAY)) AS week,
  COUNT(DISTINCT ga_session_id) AS sessions,
  AVG(UNIX_SECONDS(TIMESTAMP '1970-01-01 00:00:00 UTC' + session_duration)) AS avg_duration_seconds,
  SAFE_DIVIDE(SUM(CASE WHEN engaged THEN 1 ELSE 0 END), COUNT(*)) AS engagement_rate
FROM sessions
GROUP BY path, week
ORDER BY path, week DESC
"""


def fetch_ga4_sessions(client: bigquery.Client) -> pd.DataFrame:
    return client.query(GA4_SESSIONS_QUERY).to_dataframe()


# ---------------------------------------------------------------------------
# 3b. GA4 — clics par composant, par semaine
# ---------------------------------------------------------------------------
GA4_COMPONENTS_QUERY = f"""
SELECT
  REGEXP_REPLACE(REGEXP_REPLACE(event_location, r'^https?://(www\\.)?papernest\\.com', ''), r'\\?.*$', '') AS path,
  DATE_TRUNC(event_date, WEEK(MONDAY)) AS week,
  eventAction AS component,
  category AS device,
  COUNT(*) AS clicks
FROM `{PROJECT_ID}.team_seo.cleaned_ga4_events_papernest_com`
WHERE event_location LIKE '{URL_FILTER_GA4}'
  AND event_name = 'cta_all_clicks'
GROUP BY path, week, component, device
ORDER BY path, week DESC, clicks DESC
"""


def fetch_ga4_components(client: bigquery.Client) -> pd.DataFrame:
    return client.query(GA4_COMPONENTS_QUERY).to_dataframe()


# ---------------------------------------------------------------------------
# Assemblage du JSON final
# ---------------------------------------------------------------------------
def safe_num(v):
    """None/NaN -> null JSON ; sinon float arrondi."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return round(float(v), 4)


def build_rows(business_df: pd.DataFrame, ga4_df: pd.DataFrame) -> list:
    ga4_lookup = {}
    for _, r in ga4_df.iterrows():
        key = (r["path"], str(r["week"])[:10])
        ga4_lookup[key] = (
            safe_num(r["sessions"]),
            safe_num(r["avg_duration_seconds"]),
            safe_num(r["engagement_rate"]),
        )

    rows = []
    for _, r in business_df.iterrows():
        key = (r["path"], r["date_week"])
        sessions, avg_dur, eng = ga4_lookup.get(key, (None, None, None))
        rows.append({
            "date": r["date_week"],
            "page": r["path"],
            "impressions": safe_num(r["impressions"]),
            "clicks": safe_num(r["clicks"]),
            "position": safe_num(r["position"]),
            "prospects": int(r["prospects"]),
            "pp": int(r["pp"]),
            "ap": int(r["ap"]),
            "rc": int(r["rc"]),
            "client_n": int(r["clients_net"]),
            "cm1": safe_num(r["cm1"]),
            "cm2": safe_num(r["cm2"]),
            "cm3": safe_num(r["cm3"]),
            "pc": safe_num(r["pc"]),
            "sessions": sessions,
            "avg_duration_s": avg_dur,
            "engagement_rate": eng,
        })
    return rows


def build_gsc_queries(gsc_df: pd.DataFrame) -> dict:
    out = {}
    for path, group in gsc_df.groupby("path"):
        out[path] = [
            {
                "month": str(r["month"])[:7],
                "query": r["query"] or "(not fourni)",
                "clicks": safe_num(r["clicks"]),
                "impressions": safe_num(r["impressions"]),
                "position": safe_num(r["avg_position"]),
                "clicks_m1": safe_num(r["clicks_m1"]),
                "position_m1": safe_num(r["position_m1"]),
            }
            for _, r in group.iterrows()
        ]
    return out


def build_components(components_df: pd.DataFrame) -> dict:
    out = {}
    for path, group in components_df.groupby("path"):
        out[path] = [
            {
                "week": str(r["week"])[:10],
                "component": r["component"],
                "device": r["device"],
                "clicks": int(r["clicks"]),
            }
            for _, r in group.iterrows()
        ]
    return out


def main():
    print(f"[{datetime.now().isoformat()}] Connexion à BigQuery ({PROJECT_ID})...")
    client = get_client()

    print("→ Extraction business...")
    business_df = fetch_business(client)
    print(f"  {len(business_df)} lignes (agrégées)")

    print("→ Extraction GSC (queries mensuelles)...")
    gsc_df = fetch_gsc_queries(client)
    print(f"  {len(gsc_df)} lignes")

    print("→ Extraction GA4 (sessions)...")
    ga4_sessions_df = fetch_ga4_sessions(client)
    print(f"  {len(ga4_sessions_df)} lignes")

    print("→ Extraction GA4 (composants)...")
    ga4_components_df = fetch_ga4_components(client)
    print(f"  {len(ga4_components_df)} lignes")

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": build_rows(business_df, ga4_sessions_df),
        "gsc_queries": build_gsc_queries(gsc_df) if not gsc_df.empty else {},
        "components": build_components(ga4_components_df) if not ga4_components_df.empty else {},
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✓ Écrit : {OUTPUT_FILE} ({OUTPUT_FILE.stat().st_size / 1024:.1f} Ko)")
    print(f"  {len(data['rows'])} lignes business")
    print(f"  {sum(len(v) for v in data['gsc_queries'].values())} lignes queries GSC")
    print(f"  {sum(len(v) for v in data['components'].values())} lignes composants")


if __name__ == "__main__":
    main()
