"""
google_kpi_sync.py
Pulls Blog Sessions from GA4 and Search Console clicks/impressions,
writes rows to the Na-Mii KPI Tracker Notion database.

Runs via GitHub Actions cron. Requires these GitHub Secrets:
  GOOGLE_SERVICE_ACCOUNT_JSON  - full contents of the service account JSON key
  NOTION_TOKEN                 - Internal Integration Secret for Na-Mii KPI Scripts
"""

import json
import os
import sys
from datetime import date, timedelta

import requests
from google.oauth2 import service_account
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
NOTION_KPI_TRACKER_DB = "1305e553-3a07-42cb-94cd-e5e7f4a05127"

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

# Date range: previous full calendar month
today = date.today()
first_of_this_month = today.replace(day=1)
last_month_end = first_of_this_month - timedelta(days=1)
last_month_start = last_month_end.replace(day=1)
PERIOD_LABEL = last_month_start.strftime("%B %Y")   # e.g. "April 2026"
DATE_START = last_month_start.isoformat()
DATE_END = last_month_end.isoformat()

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_credentials():
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    sa_info = json.loads(sa_json)
    return service_account.Credentials.from_service_account_info(sa_info, scopes=SCOPES)


# ── GA4: Blog Sessions ────────────────────────────────────────────────────────

def fetch_ga4_blog_sessions(credentials):
    client = BetaAnalyticsDataClient(credentials=credentials)
    request = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        dimensions=[Dimension(name="pagePathPlusQueryString")],
        metrics=[Metric(name="sessions")],
        date_ranges=[DateRange(start_date=DATE_START, end_date=DATE_END)],
        dimension_filter={
            "filter": {
                "field_name": "pagePathPlusQueryString",
                "string_filter": {
                    "match_type": "BEGINS_WITH",
                    "value": "/blog",
                },
            }
        },
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


def notion_row_exists(metric_name: str, period: str) -> bool:
    """Return True if a row for this metric + period already exists."""
    payload = {
        "filter": {
            "and": [
                {"property": "Metric", "select": {"equals": metric_name}},
                {"property": "Period", "rich_text": {"equals": period}},
            ]
        }
    }
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_KPI_TRACKER_DB}/query",
        headers=NOTION_HEADERS,
        json=payload,
    )
    r.raise_for_status()
    return len(r.json().get("results", [])) > 0


def create_kpi_row(metric_name: str, value: float, period: str):
    if notion_row_exists(metric_name, period):
        print(f"  Skipping — row already exists: {metric_name} / {period}")
        return

    payload = {
        "parent": {"database_id": NOTION_KPI_TRACKER_DB},
        "properties": {
            "Metric": {"select": {"name": metric_name}},
            "Period": {"rich_text": [{"text": {"content": period}}]},
            "Value": {"number": value},
        },
    }
    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=payload,
    )
    r.raise_for_status()
    print(f"  Created row: {metric_name} = {value} ({period})")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Syncing Google KPIs for period: {PERIOD_LABEL} ({DATE_START} → {DATE_END})")
    credentials = get_credentials()

    # GA4
    blog_sessions = fetch_ga4_blog_sessions(credentials)
    create_kpi_row("Blog Sessions", blog_sessions, PERIOD_LABEL)

    # Search Console — we log clicks as the primary metric
    # Impressions stored as a separate row for reference
    clicks, impressions = fetch_search_console(credentials)
    # Search Console clicks map to organic traffic signal — store under Blog Sessions
    # if you want a separate SC metric, add "Search Console Clicks" to the Metric options
    # For now we log clicks only if they differ meaningfully from GA4 sessions
    # Uncomment below to write SC clicks as a separate row:
    # create_kpi_row("Search Console Clicks", clicks, PERIOD_LABEL)

    print("Done.")


if __name__ == "__main__":
    main()
