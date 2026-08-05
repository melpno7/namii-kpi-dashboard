"""
export_notion_data.py
Fetches KPI Tracker + Cycle Overview + Cycle Goals from Notion and writes
data.json to the repo root. Runs weekly via GitHub Actions.
Requires: NOTION_TOKEN secret.
"""

import os
import re
import json
from datetime import datetime, date
import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]

KPI_TRACKER_DB    = "71052c30eae94e6c85d168b3b70121ee"
CYCLE_OVERVIEW_DB = "fb571740cfd948328d0cc5ae43095e7f"
CYCLE_GOALS_DB    = "988401501f9746f0bed594eb3c180a32"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# Metric keyword -> icon shown on the goal card
ICON_MAP = [
    ("call",         "\U0001F4DE"),
    ("conversation", "\U0001F3A4"),
    ("lead magnet",  "\U0001F9F2"),
    ("download",     "\U0001F9F2"),
    ("newsletter",   "\U0001F4E7"),
    ("open rate",    "\U0001F4E7"),
    ("email",        "\U0001F4E7"),
    ("linkedin",     "\U0001F4BC"),
    ("instagram",    "\U0001F4F8"),
    ("blog",         "\U0001F4F0"),
    ("article",      "\U0001F4F0"),
    ("aeo",          "\U0001F50D"),
    ("geo",          "\U0001F50D"),
    ("session",      "\U0001F310"),
    ("sales cycle",  "\u23F1\uFE0F"),
    ("pieces",       "\U0001F4DA"),
    ("pillar",       "\U0001F4CA"),
]


# ---------------------------------------------------------------- readers
def txt(prop):
    if not prop:
        return ""
    return (prop.get("title") or [{}])[0].get("plain_text", "")


def rtxt(prop):
    if not prop:
        return ""
    rt = prop.get("rich_text") or []
    return rt[0].get("plain_text", "") if rt else ""


def sel(prop):
    if not prop:
        return ""
    return (prop.get("select") or {}).get("name", "")


def num(prop):
    return prop.get("number") if prop else None


def dt(prop):
    if not prop:
        return ""
    return (prop.get("date") or {}).get("start", "") or ""


def chk(prop):
    return bool(prop.get("checkbox")) if prop else False


def query_db(db_id, body=None):
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    body = dict(body or {})
    body.setdefault("page_size", 100)
    results, cursor = [], None
    while True:
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, headers=NOTION_HEADERS, json=body, timeout=30)
        r.raise_for_status()
        payload = r.json()
        results.extend(payload.get("results", []))
        if not payload.get("has_more"):
            return results
        cursor = payload.get("next_cursor")


# ------------------------------------------------------- text -> numbers
def parse_num(s):
    """First number in free text. '0 calls / 4' -> 0.0,
    '15% (sustained)' -> 15.0, '<= prior cycle' -> None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = re.search(r"-?\d+(?:\.\d+)?", str(s).replace(",", ""))
    return float(m.group()) if m else None


def parse_unit(target_text, metric_text):
    blob = f"{target_text} {metric_text}".lower()
    if "%" in str(target_text):
        return "%"
    for word in ("calls", "convos", "conversations", "downloads",
                 "articles", "days", "pieces", "posts", "sessions"):
        if word in blob:
            return word
    # Word boundary matters: "strategy" contains "rate".
    if re.search(r"\brate\b", blob):
        return "%"
    return ""


def pick_icon(blob):
    low = blob.lower()
    for key, icon in ICON_MAP:
        if key in low:
            return icon
    return "\U0001F3AF"


# ------------------------------------------------------------ KPI Tracker
def fetch_entries():
    pages = query_db(
        KPI_TRACKER_DB,
        {"sorts": [{"property": "Period", "direction": "descending"}]},
    )
    entries = []
    for page in pages:
        p = page["properties"]
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


# --------------------------------------------------------- Cycle Overview
def fetch_cycle():
    """The cycle whose period contains today, else the most recent cycle
    that has started. Skips Archived rows. Deliberately does NOT filter on
    Approval Status: an unapproved current cycle still has to show."""
    pages = query_db(CYCLE_OVERVIEW_DB)
    today = date.today().isoformat()
    rows = []
    for page in pages:
        p = page["properties"]
        status = sel(p.get("Approval Status"))
        if status == "Archived":
            continue
        start, end = dt(p.get("Period Start")), dt(p.get("Period End"))
        if not start:
            continue
        rows.append({
            "month":       txt(p.get("Cycle Month")),
            "focus":       rtxt(p.get("Cycle Focus")),
            "anchor":      rtxt(p.get("Anchor Event")),
            "start":       start,
            "end":         end or start,
            "totalPieces": num(p.get("Total Pieces")) or 0,
            "approval":    status,
            "_edited":     page.get("last_edited_time", ""),
            "pillars": {
                "LinkedIn":   num(p.get("LinkedIn Count"))   or 0,
                "Instagram":  num(p.get("Instagram Count"))  or 0,
                "Blog":       num(p.get("Blog Count"))       or 0,
                "Email":      num(p.get("Email Count"))      or 0,
                "Newsletter": num(p.get("Newsletter Count")) or 0,
                "Other":      num(p.get("Other Count"))      or 0,
            },
        })

    if not rows:
        return None

    # Newest edit wins when a cycle month is duplicated.
    rows.sort(key=lambda r: (r["start"], r["_edited"]))
    current = [r for r in rows if r["start"] <= today <= r["end"]]
    started = [r for r in rows if r["start"] <= today]
    chosen = current[-1] if current else (started[-1] if started else rows[0])
    chosen.pop("_edited", None)
    return chosen


# ------------------------------------------------------------ Cycle Goals
def fetch_goals(cycle_month):
    if not cycle_month:
        return []
    pages = query_db(CYCLE_GOALS_DB)
    goals = []
    for page in pages:
        p = page["properties"]
        if rtxt(p.get("Cycle Month")).strip() != cycle_month.strip():
            continue
        title = txt(p.get("Goal"))
        if not title:
            continue
        metric      = rtxt(p.get("Metric"))
        target_text = rtxt(p.get("Target"))
        actual_text = rtxt(p.get("Actual"))
        goals.append({
            "icon":           pick_icon(f"{title} {metric}"),
            "short":          title if len(title) <= 30 else title[:29].rstrip() + "\u2026",
            "title":          title,
            "metric":         metric,
            "target":         parse_num(target_text),
            "targetText":     target_text,
            "actual":         parse_num(actual_text),
            "actualText":     actual_text,
            "unit":           parse_unit(target_text, metric),
            "status":         sel(p.get("Status")) or "Pending",
            "higherIsBetter": chk(p.get("Higher Is Better")),
            "note":           rtxt(p.get("Notes")).split("\n")[0],
        })
    return goals


def main():
    print("Fetching KPI Tracker...")
    entries = fetch_entries()
    print(f"  {len(entries)} entries")

    print("Fetching Cycle Overview...")
    cycle = fetch_cycle()
    print(f"  cycle: {cycle['month'] if cycle else 'NONE'} "
          f"({cycle['approval'] if cycle else '-'})")

    print("Fetching Cycle Goals...")
    goals = fetch_goals(cycle["month"] if cycle else None)
    print(f"  {len(goals)} goals")

    data = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "entry_count": len(entries),
        "cycle": cycle,
        "goals": goals,
        "entries": entries,
    }
    with open("data.json", "w") as f:
        json.dump(data, f, indent=2)
    print("Wrote data.json")


if __name__ == "__main__":
    main()
