#!/usr/bin/env python3
"""
UMD Hours Scraper — 每12小时自动抓取
抓取：Natatorium, Dining Halls, IDEA Factory 的开放时间
"""

import json
import os
from datetime import datetime, date, timedelta

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    os.system("pip install requests beautifulsoup4 -q")
    import requests
    from bs4 import BeautifulSoup


# ─── Helpers ────────────────────────────────────────────────────────────────

def parse_gviz(url: str) -> dict | None:
    """Fetch Google Visualization API data and return parsed JSON."""
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    text = resp.text
    # Strip /*O_o*/ and function wrapper
    json_str = text.split("(", 1)[1].rsplit(")", 1)[0]
    return json.loads(json_str)


def find_date_column(headers: list[str], target: date) -> int:
    """Find column index for a given date in headers."""
    for i, h in enumerate(headers):
        try:
            parts = h.strip().split("/")
            d = date(int(parts[2]), int(parts[0]), int(parts[1]))
            if d == target:
                return i
        except (ValueError, IndexError):
            continue
    return -1


def get_this_week_dates() -> list[date]:
    """Return Mon-Sun dates for the current week."""
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    return [mon + timedelta(days=i) for i in range(7)]


def day_name_from_date(d: date) -> str:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return names[d.weekday()]


def parse_duration(duration_str: str) -> dict | None:
    """Parse '6am to 8pm' or '10am to 10:30am' into structured format."""
    if not duration_str or duration_str.lower() in ("closed", "", "none"):
        return None
    parts = duration_str.lower().replace("–", "-").replace("—", "-").split(" to ")
    if len(parts) == 2:
        return {"from": parts[0].strip(), "to": parts[1].strip()}
    parts = duration_str.lower().replace("–", "-").replace("—", "-").split("-")
    if len(parts) == 2:
        return {"from": parts[0].strip(), "to": parts[1].strip()}
    return {"text": duration_str.strip()}


# ─── 1. Natatorium (RecWell Facility Alerts) ──────────────────────────────

def scrape_natatorium() -> dict:
    """Parse Natatorium hours from RecWell facility-alerts page."""
    url = "https://recwell.umd.edu/facility-alerts"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    result = {
        "source": url,
        "last_updated": datetime.now().isoformat(),
        "alerts": [],
        "tables": [],
    }

    # Find the Natatorium accordion item
    items = soup.find_all("umd-element-accordion-item")
    nat_item = None
    for item in items:
        text = item.get_text(strip=True)
        if "Natatorium" in text:
            nat_item = item
            break

    if not nat_item:
        result["error"] = "Natatorium accordion item not found"
        return result

    # Get the text slot (div with slot="text")
    text_div = nat_item.find("div", slot="text")
    if not text_div:
        result["error"] = "Text slot not found"
        return result

    # Extract description paragraph
    desc = text_div.find("p")
    if desc:
        result["alerts"].append(desc.get_text(strip=True))

    # Extract all tables
    tables = text_div.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [th.get_text(strip=True) for th in rows[0].find_all("th")]
        table_data = []
        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            entry = {"facility": cells[0].get_text(strip=True)}
            for i, cell in enumerate(cells[1:], start=1):
                if i < len(headers):
                    entry[headers[i]] = cell.get_text(strip=True)
            table_data.append(entry)
        result["tables"].append({
            "headers": headers,
            "rows": table_data,
        })

    return result


# ─── 2. Dining Halls / Cafes (Google Sheets) ──────────────────────────────

DINING_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1vdWskGO2-aJfKLSW8-3zMaj_nx4SBJHF3OvMEy4-ZNo/gviz/tq?gid=479022338"
)

CAFES_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1vdWskGO2-aJfKLSW8-3zMaj_nx4SBJHF3OvMEy4-ZNo/gviz/tq?gid=2021515491"
)


def scrape_sheet(url: str, name: str) -> dict:
    """Parse a Google Sheet with venue hours."""
    try:
        data = parse_gviz(url)
    except Exception as e:
        return {"source": name, "url": url, "error": str(e), "venues": []}

    rows_data = data["table"]["rows"]
    headers_data = rows_data[0]["c"]
    headers = [c["v"] if c else "" for c in headers_data]

    week_dates = get_this_week_dates()
    week_day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    venues = {}
    first_row_empty = True

    for row_obj in rows_data[1:]:
        cells = row_obj.get("c", [])
        if not cells or not cells[0] or not cells[0].get("v"):
            continue
        venue_name = cells[0]["v"]
        if not venue_name.strip():
            continue

        parts = venue_name.split(" | ", 1)
        venue_short = parts[0].strip()
        meal = parts[1].strip() if len(parts) > 1 else ""

        if venue_short not in venues:
            venues[venue_short] = {"name": venue_short, "schedule": {}}

        # Get the hours for this week
        for wi, wd in enumerate(week_dates):
            col = find_date_column(headers, wd)
            if col >= 0 and col < len(cells):
                val = cells[col]["v"] if cells[col] else ""
                val = val.strip() if val else ""
                day_key = week_day_names[wi]
                if meal not in venues[venue_short]["schedule"]:
                    venues[venue_short]["schedule"][meal] = {}
                venues[venue_short]["schedule"][meal][day_key] = val

    return {
        "source": name,
        "url": url,
        "last_updated": datetime.now().isoformat(),
        "week_of": f"{week_dates[0]} - {week_dates[-1]}",
        "venues": list(venues.values()),
    }


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    output = {
        "generated_at": datetime.now().isoformat(),
        "generated_at_readable": datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "week_dates": [{"date": d.strftime("%Y-%m-%d"), "day": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][d.weekday()]} for d in get_this_week_dates()],
    }

    print("🔄 Fetching Natatorium hours...")
    output["natatorium"] = scrape_natatorium()

    print("🔄 Fetching Dining Hall hours...")
    output["dining_halls"] = scrape_sheet(DINING_SHEET_URL, "UMD Dining Halls")

    print("🔄 Fetching Cafe hours...")
    output["cafes"] = scrape_sheet(CAFES_SHEET_URL, "UMD Cafes")

    # Save
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved to {out_path}")
    nat_tables = len(output["natatorium"].get("tables", []))
    dh_venues = len(output["dining_halls"].get("venues", []))
    cafe_venues = len(output["cafes"].get("venues", []))
    print(f"   Natatorium: {nat_tables} table(s)")
    print(f"   Dining Halls: {dh_venues} venues")
    print(f"   Cafes: {cafe_venues} venues")


if __name__ == "__main__":
    main()
