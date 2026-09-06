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


def get_two_week_dates() -> list[date]:
    """Return 14 dates: this Mon-Sun + next Mon-Sun."""
    today = date.today()
    mon = today - timedelta(days=today.weekday())
    return [mon + timedelta(days=i) for i in range(14)]


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


def alert_end_date(text: str) -> date | None:
    """Extract the latest 'Month Day' mentioned in an alert → its end date.
    E.g. 'closed Monday, August 17 through Saturday, August 22' → Aug 22."""
    found = []
    for m in re.finditer(rf"({'|'.join(MONTH_MAP)})\s+(\d{{1,2}})", text, re.I):
        month = MONTH_MAP.get(m.group(1).lower().rstrip(","))
        if not month:
            continue
        try:
            found.append(date(date.today().year, month, int(m.group(2))))
        except ValueError:
            continue
    return max(found) if found else None


# 手动覆盖：RecWell 只发纯文字公告、页面无表格数据时使用。
# 日期滚出 14 天窗口后自动失效，无需清理。
# key: date → (natatorium, outdoor_aquatic)
NAT_MANUAL_OVERRIDES = {
    # 2026-08-17 ~ 08-22 维护闭馆（50M池/教学池/桑拿/蒸汽房全关，OAC 延长开放）
    date(2026, 8, 17): ("Closed", "6am-8pm"),
    date(2026, 8, 18): ("Closed", "6am-8pm"),
    date(2026, 8, 19): ("Closed", "6am-8pm"),
    date(2026, 8, 20): ("Closed", "6am-8pm"),
    date(2026, 8, 21): ("Closed", "6am-8pm"),
    date(2026, 8, 22): ("Closed", "8am-7:30pm"),
}


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
    if not nat_item and items:
        # 假日单公告模式（如 "Hours of Operation - Labor Day 9/7"）没有独立 Natatorium item：
        # 退回第一个公告继续解析，避免整个 provider 失败。
        nat_item = items[0]
    if not nat_item:
        result["error"] = "Natatorium accordion item not found"
        return result

    text_div = nat_item.find("div", slot="text")
    if not text_div:
        result["error"] = "Text slot not found"
        return result

    # 收集所有公告段落和列表项（纯文字公告没有表格，靠这里展示）
    for el in text_div.find_all(["p", "ul"]):
        if el.name == "ul":
            items = [li.get_text(strip=True) for li in el.find_all("li")]
            if items:
                result["alerts"].append(" · ".join(items))
        else:
            t = el.get_text(strip=True)
            if t:
                result["alerts"].append(t)

    # Parse tables and build a date → hours lookup for Natatorium
    nat_hours_lookup = {}  # date → hours string
    tables = text_div.find_all("table")
    reg_hours_nat = "6am-8pm"
    reg_hours_oac = "10am-8pm"

    # 假日公告日期：从公告文本提取（如 "on Monday, September 7" → 9/7），
    # 用于把 "Facility | Hours" 格式的假日表映射到具体日期。
    announce_dates = set()
    for a in result["alerts"]:
        d = alert_end_date(a)
        if d:
            announce_dates.add(d)

    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [th.get_text(strip=True) for th in rows[0].find_all("th")]
        table_data = []
        # 假日表：表头为 Facility | Hours（而非日期）→ 时间应用到公告日期
        is_facility_hours = len(headers) >= 2 and headers[0].lower() == "facility" and "hour" in headers[1].lower()
        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            # 剥离斜体副注（如 "sauna & steam room open 10am to 10pm"），只保留主时段
            for cell in cells:
                for tag in cell.find_all(["em", "strong", "i"]):
                    tag.decompose()
            entry = {"facility": cells[0].get_text(strip=True)}
            for i, cell in enumerate(cells[1:], start=1):
                if i < len(headers):
                    entry[headers[i]] = cell.get_text(strip=True)
            table_data.append(entry)

            if is_facility_hours:
                fac = entry["facility"]
                hours_val = entry.get(headers[1], "Closed") if len(headers) > 1 else "Closed"
                for ad in announce_dates:
                    if "Natatorium" in fac and "Outdoor" not in fac:
                        nat_hours_lookup.setdefault(ad, {})["natatorium"] = hours_val
                    elif "Outdoor" in fac:
                        nat_hours_lookup.setdefault(ad, {})["outdoor"] = hours_val
                continue

            # 日期表头格式（如 "July 9"）：每个日期列建 lookup
            for i, h in enumerate(headers[1:], start=1):
                d = parse_special_date(h)
                if d:
                    key = "natatorium" if "Natatorium" in entry["facility"] else "outdoor"
                    if d not in nat_hours_lookup:
                        nat_hours_lookup[d] = {}
                    nat_hours_lookup[d][key] = entry.get(h, "Closed")

        result["tables"].append({"headers": headers, "rows": table_data})

    # Build two-week schedule
    week_dates = get_two_week_dates()
    weekly = []
    for i, wd in enumerate(week_dates):
        if wd in NAT_MANUAL_OVERRIDES:
            nat_val, oac_val = NAT_MANUAL_OVERRIDES[wd]
        elif wd in nat_hours_lookup:
            nat_val = nat_hours_lookup[wd].get("natatorium", "Closed")
            oac_val = nat_hours_lookup[wd].get("outdoor", "Closed")
        else:
            nat_val = reg_hours_nat
            oac_val = reg_hours_oac
        weekly.append({
            "date": wd.isoformat(),
            "day": WEEK_DAY_NAMES[wd.weekday()],
            "week_number": (i // 7) + 1,
            "natatorium": nat_val,
            "outdoor_aquatic": oac_val,
        })

    result["weekly_hours"] = weekly
    result["regular_hours_note"] = f"常规时间 Regular: Natatorium {reg_hours_nat} · Outdoor Aquatic Center {reg_hours_oac}"

    # 过期公告分拣：结束日期已过去的 → expired_alerts（前端灰显折叠），不再当红横幅。
    # 无日期的配套公告（如“OAC 延长开放”）跟随批次：带日期的公告全部过期时一并过期。
    ends = {a: alert_end_date(a) for a in result["alerts"]}
    dated_ends = [e for e in ends.values() if e]
    all_expired = bool(dated_ends) and all(e < date.today() for e in dated_ends)
    active, expired = [], []
    for a, end in ends.items():
        if (end and end < date.today()) or (not end and all_expired):
            expired.append(a)
        else:
            active.append(a)
    result["alerts"] = active
    result["expired_alerts"] = expired
    return result


# ─── 2. Dining Halls / Cafes / Stamp (Google Sheets) ────────────────────

BASE_SHEET = "https://docs.google.com/spreadsheets/d/1vdWskGO2-aJfKLSW8-3zMaj_nx4SBJHF3OvMEy4-ZNo/gviz/tq?gid="

DINING_SHEET_URL = BASE_SHEET + "479022338"
CAFES_SHEET_URL = BASE_SHEET + "2021515491"
STAMP_SHEET_URL = BASE_SHEET + "57096019"


def scrape_sheet(url: str, name: str, two_weeks: bool = False) -> dict:
    try:
        data = parse_gviz(url)
        rows_data = data["table"]["rows"]
        if not rows_data:
            return {"source": name, "url": url, "error": "Empty rows data", "venues": []}
        headers_data = rows_data[0]["c"]
        headers = [c["v"] if c else "" for c in headers_data]
    except Exception as e:
        return {"source": name, "url": url, "error": f"GViz parse error: {e}", "venues": []}

    week_dates = get_two_week_dates() if two_weeks else get_this_week_dates()

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
                if two_weeks:
                    # 14 天里星期重复，key 带上日期，如 "Mon 7/27"
                    day_key = f"{WEEK_DAY_NAMES[wi % 7]} {wd.month}/{wd.day}"
                else:
                    day_key = WEEK_DAY_NAMES[wi]
                if meal not in venues[venue_short]["schedule"]:
                    venues[venue_short]["schedule"][meal] = {}
                venues[venue_short]["schedule"][meal][day_key] = val

    return {
        "source": name,
        "url": url,
        "last_updated": datetime.now().isoformat(),
        "week_of": f"{week_dates[0].month}/{week_dates[0].day} - {week_dates[-1].month}/{week_dates[-1].day}",
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
    try:
        output["natatorium"] = scrape_natatorium()
    except Exception as e:
        output["natatorium"] = {"error": f"Natatorium scrape failed: {e}", "source": "recwell.umd.edu"}

    print("🔄 Fetching Dining Hall hours...")
    try:
        output["dining_halls"] = scrape_sheet(DINING_SHEET_URL, "UMD Dining Halls", two_weeks=True)
        # 显示顺序：Yahentamitsi 置顶，其余保持 Sheet 原始顺序（sort 稳定）
        venues = output["dining_halls"].get("venues", [])
        venues.sort(key=lambda v: 0 if v.get("name") == "Yahentamitsi" else 1)
    except Exception as e:
        output["dining_halls"] = {"error": f"Dining Halls scrape failed: {e}", "venues": []}

    print("🔄 Fetching Cafe hours...")
    try:
        output["cafes"] = scrape_sheet(CAFES_SHEET_URL, "UMD Cafes")
    except Exception as e:
        output["cafes"] = {"error": f"Cafes scrape failed: {e}", "venues": []}

    print("🔄 Fetching Stamp hours...")
    try:
        output["stamp"] = scrape_sheet(STAMP_SHEET_URL, "UMD Stamp Dining")
    except Exception as e:
        output["stamp"] = {"error": f"Stamp scrape failed: {e}", "venues": []}

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved to {out_path}")
    # 容错：某 provider 走 error 分支（weekly_hours=None）时不能因此让整个 workflow 失败
    nat = output.get("natatorium") or {}
    nat_days = len(nat.get("weekly_hours") or [])
    nat_err = f" ⚠️ {nat.get('error')}" if nat.get("error") else ""
    print(f"   Natatorium: weekly_hours with {nat_days} days{nat_err}")
    print(f"   Dining Halls: {len((output.get('dining_halls') or {}).get('venues') or [])} venues")
    print(f"   Cafes: {len((output.get('cafes') or {}).get('venues') or [])} venues")
    print(f"   Stamp: {len((output.get('stamp') or {}).get('venues') or [])} venues")


if __name__ == "__main__":
    main()
