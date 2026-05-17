"""
export_notion_data.py
Fetches KPI Tracker from Notion and writes data.json to repo root.
Runs weekly via GitHub Actions alongside google_kpi_sync.
Requires: NOTION_TOKEN secret.
"""

import os
import json
from datetime import datetime
import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
KPI_TRACKER_DB = "71052c30eae94e6c85d168b3b70121ee"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

def fetch_entries():
    url = f"https://api.notion.com/v1/databases/{KPI_TRACKER_DB}/query"
    body = {
        "sorts": [{"property": "Period", "direction": "descending"}],
        "page_size": 100,
    }
    r = requests.post(url, headers=NOTION_HEADERS, json=body)
    r.raise_for_status()
    results = r.json().get("results", [])
    entries = []
    for page in results:
        p = page["properties"]
        def txt(prop): return (prop.get("title") or [{}])[0].get("plain_text", "") if prop else ""
        def sel(prop): return (prop.get("select") or {}).get("name", "") if prop else ""
        def num(prop): return prop.get("number") if prop else None
        def dt(prop): return (prop.get("date") or {}).get("start", "") if prop else ""
        def rtxt(prop): rt = (prop.get("rich_text") or []) if prop else []; return rt[0].get("plain_text", "") if rt else ""
        entries.append({
            "entry":   txt(p.get("Entry")),
            "metric":  sel(p.get("Metric")),
            "period":  dt(p.get("Period")),
            "value":   num(p.get("Value")),
            "target":  num(p.get("Target")),
            "status":  sel(p.get("Status")),
            "channel": sel(p.get("Channel")),
            "source":  sel(p.get("Source")),
            "unit":    sel(p.get("Unit")),
            "notes":   rtxt(p.get("Notes")),
        })
    return entries

def main():
    print("Fetching KPI Tracker from Notion...")
    entries = fetch_entries()
    print(f"Fetched {len(entries)} entries")
    data = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "entry_count": len(entries),
        "entries": entries,
    }
    with open("data.json", "w") as f:
        json.dump(data, f, indent=2)
    print("Wrote data.json")

if __name__ == "__main__":
    main()
