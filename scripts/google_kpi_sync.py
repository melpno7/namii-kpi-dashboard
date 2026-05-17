"""
google_kpi_sync.py
Pulls Blog Sessions from GA4 and Search Console clicks/impressions,
writes rows to the Na-Mii KPI Tracker Notion database.

Runs via GitHub Actions cron. Requires these GitHub Secrets:
  GOOGLE_CLIENT_ID      - OAuth client ID
  GOOGLE_CLIENT_SECRET  - OAuth client secret
  GOOGLE_REFRESH_TOKEN  - OAuth refresh token
  NOTION_TOKEN          - Internal Integration Secret for Na-Mii KPI Scripts
"""

import os
from datetime import date, timedelta

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)

# ── Config ────────────────────────────────────────────────────────────────────

GA4_PROPERTY_ID = "471407412"
SEARCH_CONSOLE_PROPERTY = "sc-domain:na-mii.co"

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
# Use the data source (collection) ID, not the database page ID
NOTION_KPI_TRACKER_DB_ID = "71052c30eae94e6c85d168b3b70121ee"  # database page ID for query
NOTION_KPI_TRACKER_DS = "1305e553-3a07-42cb-94cd-e5e7f4a05127"  # collection ID for page creation

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

# Date range: last 30 days (rolling window, updated weekly)
today = date.today()
DATE_END = today.isoformat()
DATE_START = (today - timedelta(days=30)).isoformat()
PERIOD_LABEL = f"Last 30 days to {today.strftime('%b %d, %Y')}"  # e.g. "Last 30 days to May 17, 2026"
PERIOD_ISO = today.isoformat()  # use today as the anchor date for dedup

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_credentials():
    credentials = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )
    credentials.refresh(Request())
    return credentials


# ── GA4: Blog Sessions ────────────────────────────────────────────────────────

def fetch_ga4_blog_sessions(credentials):
    client = BetaAnalyticsDataClient(credentials=credentials)
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        metrics=[Metric(name="sessions")],
        date_ranges=[DateRange(start_date=DATE_START, end_date=DATE_END)],
    )
    response = client.run_report(request)
    total = sum(int(row.metric_values[0].value) for row in response.rows)
    print(f"GA4 blog sessions ({PERIOD_LABEL}): {total}")
    return total


# ── Search Console ────────────────────────────────────────────────────────────

def fetch_search_console(credentials):
    service = build("searchconsole", "v1", credentials=credentials)
    body = {
        "startDate": DATE_START,
        "endDate": DATE_END,
        "type": "web",
        "aggregationType": "AUTO",
    }
    response = (
        service.searchanalytics()
        .query(siteUrl=SEARCH_CONSOLE_PROPERTY, body=body)
        .execute()
    )
    rows = response.get("rows", [])
    clicks = sum(r.get("clicks", 0) for r in rows)
    impressions = sum(r.get("impressions", 0) for r in rows)
    print(f"Search Console clicks ({PERIOD_LABEL}): {clicks}")
    print(f"Search Console impressions ({PERIOD_LABEL}): {impressions}")
    return clicks, impressions


# ── Notion helpers ────────────────────────────────────────────────────────────

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def notion_row_exists(metric_name: str, period_label: str) -> bool:
    entry_title = f"{metric_name} — {period_label}"
    payload = {
        "filter": {
            "property": "Entry",
            "title": {"equals": entry_title}
        }
    }
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_KPI_TRACKER_DB_ID}/query",
        headers=NOTION_HEADERS,
        json=payload,
    )
    if not r.ok:
        print(f"  Dedup check error {r.status_code}: {r.text}")
    r.raise_for_status()
    return len(r.json().get("results", [])) > 0


def create_kpi_row(metric_name: str, value: float, period_label: str, period_iso: str,
                   channel: str = None, source: str = "Website Analytics", unit: str = "#"):
    if notion_row_exists(metric_name, period_label):
        print(f"  Skipping — row already exists: {metric_name} / {period_label}")
        return

    properties = {
        "Entry": {"title": [{"text": {"content": f"{metric_name} — {period_label}"}}]},
        "Metric": {"select": {"name": metric_name}},
        "Period": {"date": {"start": period_iso}},
        "Value": {"number": value},
        "Source": {"select": {"name": source}},
        "Unit": {"select": {"name": unit}},
    }
    if channel:
        properties["Channel"] = {"select": {"name": channel}}

    payload = {
        "parent": {"database_id": NOTION_KPI_TRACKER_DB_ID},
        "properties": properties,
    }
    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=payload,
    )
    if not r.ok:
        print(f"  Notion error {r.status_code}: {r.text}")
    r.raise_for_status()
    print(f"  Created row: {metric_name} = {value} ({period_label})")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Syncing Google KPIs for period: {PERIOD_LABEL} ({DATE_START} → {DATE_END})")
    credentials = get_credentials()

    blog_sessions = fetch_ga4_blog_sessions(credentials)
    create_kpi_row("Blog Sessions", blog_sessions, PERIOD_LABEL, PERIOD_ISO,
                   channel="Blog", unit="sessions")

    clicks, impressions = fetch_search_console(credentials)
    create_kpi_row("Search Console Clicks", clicks, PERIOD_LABEL, PERIOD_ISO,
                   channel="Blog", unit="#")

    print("Done.")


if __name__ == "__main__":
    main()
