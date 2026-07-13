#!/usr/bin/env python3
"""
UMD Hours Scraper — 每12小时自动抓取
抓取：Natatorium, Dining Halls, IDEA Factory, Stamp 的开放时间
"""

import json
import os
import re
from datetime import datetime, date, timedelta

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    os.system("pip install requests beautifulsoup4 -q")
    import requests
    from bs4 import BeautifulSoup


# ─── Helpers ────────────────────────────────────────────────────────────────

def parse_gviz(url: str) -> dict:
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    text = resp.text
    json_str = text.split("(", 1)[1].rsplit(")", 1)[0]
    return json.loads(json_str)


def find_date_column(headers: list[str], target: date) -> int:
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
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    return [mon + timedelta(days=i) for i in range(7)]


WEEK_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ─── 1. Natatorium (RecWell Facility Alerts) ──────────────────────────────

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_special_date(s: str) -> date | None:
    """Parse 'July 9' into date(2026, 7, 9). Year is current year."""
    s = s.strip()
    parts = s.split()
    if len(parts) == 2:
        month_str, day_str = parts
        month = MONTH_MAP.get(month_str.lower().rstrip(","))
        if month:
            return date(date.today().year, month, int(day_str.rstrip(",")))
    return None


def scrape_natatorium() -> dict:
    url = "https://recwell.umd.edu/facility-alerts"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    result = {
        "source": url,
        "last_updated": datetime.now().isoformat(),
        "alerts": [],
        "tables": [],
        "weekly_hours": None,
    }

    items = soup.find_all("umd-element-accordion-item")
    nat_item = None
    for item in items:
        if "Natatorium" in item.get_text(strip=True):
            nat_item = item
            break
    if not nat_item:
        result["error"] = "Natatorium accordion item not found"
        return result

    text_div = nat_item.find("div", slot="text")
    if not text_div:
        result["error"] = "Text slot not found"
        return result

    desc = text_div.find("p")
    if desc:
        result["alerts"].append(desc.get_text(strip=True))

    # Parse tables and build a date → hours lookup for Natatorium
    nat_hours_lookup = {}  # date → hours string
    tables = text_div.find_all("table")
    reg_hours_nat = "6am-8pm"
    reg_hours_oac = "10am-8pm"

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

            # Build lookup: for each special date → hours
            for i, h in enumerate(headers[1:], start=1):
                d = parse_special_date(h)
                if d:
                    key = "natatorium" if "Natatorium" in entry["facility"] else "outdoor"
                    if d not in nat_hours_lookup:
                        nat_hours_lookup[d] = {}
                    nat_hours_lookup[d][key] = entry.get(h, "Closed")

        result["tables"].append({"headers": headers, "rows": table_data})

    # Build this week's schedule
    week_dates = get_this_week_dates()
    weekly = []
    for wd in week_dates:
        if wd in nat_hours_lookup:
            nat_val = nat_hours_lookup[wd].get("natatorium", "Closed")
            oac_val = nat_hours_lookup[wd].get("outdoor", "Closed")
        else:
            nat_val = reg_hours_nat
            oac_val = reg_hours_oac
        weekly.append({
            "date": wd.isoformat(),
            "day": WEEK_DAY_NAMES[wd.weekday()],
            "natatorium": nat_val,
            "outdoor_aquatic": oac_val,
        })

    result["weekly_hours"] = weekly
    result["regular_hours_note"] = f"常规时间 Regular: Natatorium {reg_hours_nat} · Outdoor Aquatic Center {reg_hours_oac}"
    return result


# ─── 2. Dining Halls / Cafes / Stamp (Google Sheets) ────────────────────

BASE_SHEET = "https://docs.google.com/spreadsheets/d/1vdWskGO2-aJfKLSW8-3zMaj_nx4SBJHF3OvMEy4-ZNo/gviz/tq?gid="

DINING_SHEET_URL = BASE_SHEET + "479022338"
CAFES_SHEET_URL = BASE_SHEET + "2021515491"
STAMP_SHEET_URL = BASE_SHEET + "57096019"


def scrape_sheet(url: str, name: str) -> dict:
    try:
        data = parse_gviz(url)
    except Exception as e:
        return {"source": name, "url": url, "error": str(e), "venues": []}

    rows_data = data["table"]["rows"]
    headers_data = rows_data[0]["c"]
    headers = [c["v"] if c else "" for c in headers_data]
    week_dates = get_this_week_dates()

    venues = {}
    for row_obj in rows_data[1:]:
        cells = row_obj.get("c", [])
        if not cells or not cells[0] or not cells[0].get("v"):
            continue
        venue_name = cells[0]["v"]
        if not venue_name.strip():
            continue

        parts = venue_name.split(" | ", 1)
        venue_short = parts[0].strip()
        meal = parts[1].strip() if len(parts) > 1 else "hours"

        if venue_short not in venues:
            venues[venue_short] = {"name": venue_short, "schedule": {}}

        for wi, wd in enumerate(week_dates):
            col = find_date_column(headers, wd)
            if col >= 0 and col < len(cells):
                val = cells[col]["v"] if cells[col] else ""
                val = val.strip() if val else ""
                day_key = WEEK_DAY_NAMES[wi]
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
        "week_dates": [
            {"date": d.isoformat(), "day": WEEK_DAY_NAMES[d.weekday()]}
            for d in get_this_week_dates()
        ],
    }

    print("🔄 Fetching Natatorium hours...")
    output["natatorium"] = scrape_natatorium()

    print("🔄 Fetching Dining Hall hours...")
    output["dining_halls"] = scrape_sheet(DINING_SHEET_URL, "UMD Dining Halls")

    print("🔄 Fetching Cafe hours...")
    output["cafes"] = scrape_sheet(CAFES_SHEET_URL, "UMD Cafes")

    print("🔄 Fetching Stamp hours...")
    output["stamp"] = scrape_sheet(STAMP_SHEET_URL, "UMD Stamp Dining")

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved to {out_path}")
    print(f"   Natatorium: weekly_hours with {len(output['natatorium'].get('weekly_hours',[]))} days")
    print(f"   Dining Halls: {len(output['dining_halls'].get('venues',[]))} venues")
    print(f"   Cafes: {len(output['cafes'].get('venues',[]))} venues")
    print(f"   Stamp: {len(output['stamp'].get('venues',[]))} venues")


if __name__ == "__main__":
    main()
