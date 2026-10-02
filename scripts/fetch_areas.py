#!/usr/bin/env python3
"""Per-area cheapest gas stations across Nashville, Atlanta, and Birmingham,
from GasBuddy.

usage: fetch_areas.py [fetch [metro]|verify]
  fetch [metro] -> pulls live per-area data into data/areas/<metro>.json
                   (default: all metros)
  verify        -> checks data/areas/latest.json (Nashville), writes
                   verify-report.json (exit 1 on FAIL)

Technique:
  * Nashville: 20 areas use GasBuddy area pages (/gasprices/tennessee/<slug>):
    page __APOLLO_STATE__ gives station details (incl. lat/lng);
    StationPrices GraphQL x4 fuels gives fresh prices.
    4 areas without GasBuddy slugs (Downtown, Bellevue, West Nashville,
    Joelton/Whites Creek) use ZIP search (/home?search=<zip>).
  * Atlanta / Birmingham: all areas use the lat/lng nearest-stations pull
    (kind="latlng") as the primary fetch.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from gbhttp import http_get, http_post_json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TOP_N = 10
DATA_DIR = Path(os.environ.get("GAS_DATA_ROOT", Path.home() / "workspace" / "gas-data")) / "areas"
RAW_DIR = DATA_DIR / "raw"
HIST_DIR = DATA_DIR / "history"
FUELS = {1: "regular", 2: "midgrade", 3: "premium", 4: "diesel"}
MEMBERSHIP_RE = re.compile(r"sam'?s|costco|\bbj'?s\b", re.I)

# (display name, kind, value, center lat, center lng)
# kind: "slug" (GasBuddy area page), "zip" (ZIP search), "latlng" (nearest-stations pull)
METROS = {
    "nashville": {
        "label": "Nashville",
        "state": "TN",
        "state_slug": "tennessee",
        "center": [36.1627, -86.7816],
        "areas": [
            ("Downtown", "zip", "37219", 36.1627, -86.7816),
            ("Midtown / Vanderbilt", "slug", "midtown", 36.1516, -86.8000),
            ("East Nashville", "slug", "east-nashville", 36.1800, -86.7500),
            ("Germantown", "slug", "germantown", 36.1780, -86.7880),
            ("12 South / Berry Hill", "slug", "berry-hill", 36.1200, -86.7900),
            ("Green Hills", "slug", "green-hills", 36.1050, -86.8100),
            ("Belle Meade", "slug", "belle-meade", 36.1050, -86.8600),
            ("Bellevue", "zip", "37221", 36.0900, -86.9150),
            ("West Nashville", "zip", "37209", 36.1500, -86.8700),
            ("Joelton / Whites Creek", "zip", "37080", 36.2300, -86.9200),
            ("Madison", "slug", "madison", 36.2600, -86.7100),
            ("Donelson", "slug", "donelson", 36.0700, -86.6700),
            ("Hermitage", "slug", "hermitage", 36.2000, -86.6100),
            ("Antioch / Cane Ridge", "slug", "antioch", 36.0600, -86.6700),
            ("Brentwood", "slug", "brentwood", 36.0300, -86.7900),
            ("Franklin / Cool Springs", "slug", "franklin", 35.9700, -86.8400),
            ("Nolensville", "slug", "nolensville", 35.9500, -86.6700),
            ("Smyrna / La Vergne", "slug", "smyrna", 36.0000, -86.5450),
            ("Murfreesboro", "slug", "murfreesboro", 35.8450, -86.3900),
            ("Hendersonville", "slug", "hendersonville", 36.3000, -86.6200),
            ("Gallatin", "slug", "gallatin", 36.3900, -86.4500),
            ("Mount Juliet", "slug", "mount-juliet", 36.2000, -86.5200),
            ("Lebanon", "slug", "lebanon", 36.2100, -86.3300),
            ("Goodlettsville", "slug", "goodlettsville", 36.3300, -86.7100),
        ],
    },
    "atlanta": {
        "label": "Atlanta",
        "state": "GA",
        "state_slug": "georgia",
        "center": [33.7490, -84.3880],
        "areas": [
            ("Downtown", "latlng", None, 33.7490, -84.3880),
            ("Midtown", "latlng", None, 33.7840, -84.3830),
            ("Buckhead", "latlng", None, 33.9560, -84.3780),
            ("Virginia-Highland", "latlng", None, 33.7990, -84.3520),
            ("Decatur", "latlng", None, 33.7748, -84.2963),
            ("East Atlanta", "latlng", None, 33.7400, -84.3400),
            ("West End", "latlng", None, 33.7350, -84.4130),
            ("Sandy Springs", "latlng", None, 33.9304, -84.3733),
            ("Brookhaven", "latlng", None, 33.8650, -84.3350),
            ("Marietta", "latlng", None, 33.9526, -84.5499),
            ("Smyrna-Vinings", "latlng", None, 33.8840, -84.4680),
            ("Dunwoody", "latlng", None, 33.9460, -84.3340),
            ("Kennesaw", "latlng", None, 34.0232, -84.6150),
            ("College Park-Hapeville", "latlng", None, 33.6530, -84.4400),
        ],
    },
    "birmingham": {
        "label": "Birmingham",
        "state": "AL",
        "state_slug": "alabama",
        "center": [33.5207, -86.8025],
        "areas": [
            ("Downtown", "latlng", None, 33.5207, -86.8025),
            ("Homewood", "latlng", None, 33.4684, -86.8089),
            ("Hoover", "latlng", None, 33.3760, -86.8110),
            ("Vestavia Hills", "latlng", None, 33.4480, -86.7780),
            ("Mountain Brook", "latlng", None, 33.4900, -86.7400),
            ("Trussville", "latlng", None, 33.6218, -86.6600),
            ("Gardendale", "latlng", None, 33.6630, -86.8100),
            ("Bessemer", "latlng", None, 33.4010, -86.9550),
            ("Hueytown", "latlng", None, 33.4448, -86.9957),
            ("McCalla", "latlng", None, 33.3510, -86.9350),
            ("Pelham-Alabaster", "latlng", None, 33.3040, -86.8100),
            ("Leeds-Moody", "latlng", None, 33.5480, -86.5550),
        ],
    },
}
AREAS = METROS["nashville"]["areas"]  # kept for the Nashville-only verify

GQL_AREA = ("query StationPrices($area:String,$countryCode:String,$criteria:Criteria,"
            "$fuel:Int,$regionCode:String){locationByArea(area:$area,countryCode:$countryCode,"
            "criteria:$criteria,regionCode:$regionCode){displayName stations(fuel:$fuel){results{id "
            "prices(fuel:$fuel){cash{nickname postedTime price formattedPrice} "
            "credit{nickname postedTime price formattedPrice} fuelProduct}}}}}")
GQL_SEARCH = ("query SearchPrices($fuel:Int,$search:String){locationBySearchTerm(search:$search,"
              "priority:\"locality\"){displayName stations(fuel:$fuel,priority:\"locality\"){"
              "results{id prices{cash{price postedTime} credit{price postedTime} fuelProduct}}}}}")


def gql(token, referer, operation, query, variables, tries=4):
    body = {"operationName": operation, "query": query,
            "variables": variables}
    headers = {"Content-Type": "application/json",
               "apollo-require-preflight": "true", "gbcsrf": token,
               "Origin": "https://www.gasbuddy.com", "Referer": referer}
    data = http_post_json("https://www.gasbuddy.com/graphql",
                          headers=headers, payload=body, tries=tries)
    errs = data.get("errors") or []
    if errs:
        raise RuntimeError(f"GraphQL errors: {errs[0].get('message')}")
    return data


def extract_token(html):
    m = re.search(r'gbcsrf\s*=\s*"([^"]+)"', html)
    if not m:
        raise RuntimeError("gbcsrf token not found")
    return m.group(1)


def extract_apollo_state(html):
    m = re.search(r'window\.__APOLLO_STATE__\s*=\s*', html)
    if not m:
        raise RuntimeError("__APOLLO_STATE__ not found")
    start = html.find("{", m.end())
    depth, i, instr, esc = 0, start, False, False
    while i < len(html):
        c = html[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
        i += 1
    return json.loads(html[start:i + 1])


def parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_membership(brand, name):
    return bool(MEMBERSHIP_RE.search(brand or "") or MEMBERSHIP_RE.search(name or ""))


def brand_of(det, state):
    brands = det.get("brands") or []
    names = []
    for b in brands:
        if isinstance(b, dict) and b.get("name"):
            names.append(b["name"])
        elif isinstance(b, dict) and b.get("__ref"):
            ref = state.get(b["__ref"], {})
            if ref.get("name"):
                names.append(ref["name"])
    return names[0] if names else (det.get("name") or "Unknown")


def station_record(sid, det, state, fuels, now):
    addr = det.get("address") or {}
    brand = brand_of(det, state)
    name = det.get("name") or brand
    return {
        "station_id": sid,
        "brand": brand,
        "name": name,
        "membership": is_membership(brand, name),
        "address": (addr.get("line1") or "").strip(),
        "city": addr.get("locality") or "",
        "state": addr.get("region") or "",
        "zip": addr.get("postalCode") or "",
        "lat": det.get("latitude"),
        "lng": det.get("longitude"),
        "fuels": fuels,
    }


def fuel_block(price_entry, now):
    cr, ca = (price_entry.get("credit") or {}), (price_entry.get("cash") or {})
    posted = cr.get("postedTime") or ca.get("postedTime")
    pt = parse_time(posted)
    return {
        "card": cr.get("price"),
        "cash": ca.get("price"),
        "posted_at": posted,
        "age_hours": round((now - pt).total_seconds() / 3600, 1) if pt else None,
    }


def near_filter(stations, clat, clng, tol=0.25):
    """Drop stations implausibly far from the area center.

    GasBuddy's area index sometimes files stations under a wrong same-name
    place (e.g. Memphis's Germantown under Nashville's "germantown" area,
    Roane County stations under "midtown"). Stations without coordinates
    are kept - only clear outliers are removed.
    """
    kept = []
    for s in stations:
        la, ln = s.get("lat"), s.get("lng")
        if la is None or ln is None:
            kept.append(s)
        elif abs(la - clat) <= tol and abs(ln - clng) <= tol:
            kept.append(s)
    return kept


def gql_station_batch(token, referer, ids):
    """One aliased query returning details+coords for many station ids."""
    parts = []
    for sid in ids:
        parts.append(
            f's{sid}: station(id: {sid}) {{ id name latitude longitude '
            f'address {{ line1 locality region postalCode }} brands {{ name }} }}')
    q = "query GetStations { " + " ".join(parts) + " }"
    data = gql(token, referer, "GetStations", q, {})
    return (data.get("data") or {})


GQL_SEARCH_LATLNG = ("query SearchPrices($fuel:Int,$lat:Float,$lng:Float,$cursor:String){"
    "locationBySearchTerm(lat:$lat,lng:$lng,priority:\"locality\"){displayName "
    "stations(fuel:$fuel,lat:$lat,lng:$lng,priority:\"locality\",cursor:$cursor){"
    "cursor{next} "
    "results{id prices{cash{price postedTime} credit{price postedTime} "
    "fuelProduct}}}}}")


def supplement_latlng(name, clat, clng, have_ids, now):
    """Nearest stations to the area center, with prices; for thin areas.

    Paginates (up to 3 pages) until enough priced candidates are gathered.
    """
    page_url = "https://www.gasbuddy.com/home"
    html = http_get(page_url, {"User-Agent": UA,
                               "Accept": "text/html,application/xhtml+xml",
                               "Accept-Language": "en-US,en;q=0.9"})
    token = extract_token(html)
    price_by_id, order = {}, []
    cursor = None
    for _ in range(3):
        data = gql(token, page_url, "SearchPrices", GQL_SEARCH_LATLNG,
                   {"fuel": 1, "lat": clat, "lng": clng, "cursor": cursor})
        loc = ((data.get("data") or {}).get("locationBySearchTerm")) or {}
        st = (loc.get("stations") or {})
        results = st.get("results") or []
        for r in results:
            sid = str(r["id"])
            if sid in price_by_id:
                continue
            order.append(sid)
            per_fuel = {}
            for p in (r.get("prices") or []):
                fname = {"regular_gas": "regular", "midgrade_gas": "midgrade",
                         "premium_gas": "premium",
                         "diesel": "diesel"}.get(p.get("fuelProduct"))
                if fname:
                    per_fuel[fname] = fuel_block(p, now)
            price_by_id[sid] = per_fuel
        cursor = (st.get("cursor") or {}).get("next")
        if not cursor or len(order) >= 30:
            break
        time.sleep(1)
    need = [sid for sid in order if sid not in have_ids]
    time.sleep(1)
    batch = gql_station_batch(token, page_url, need) if need else {}
    stations = []
    for sid in need:
        s = batch.get(f"s{sid}") or {}
        if not s.get("id"):
            continue
        la, ln = s.get("latitude"), s.get("longitude")
        # GasBuddy's lat/lng search can leak far-flung same-name places
        # (e.g. Memphis's Germantown); keep only stations near the center.
        if la is None or ln is None or abs(la - clat) > 0.18 or abs(ln - clng) > 0.18:
            continue
        det = {"name": s.get("name"),
               "address": s.get("address") or {},
               "brands": s.get("brands") or [],
               "latitude": s.get("latitude"),
               "longitude": s.get("longitude")}
        fuels = {fname: price_by_id[sid].get(
            fname, {"card": None, "cash": None, "posted_at": None, "age_hours": None})
            for fname in FUELS.values()}
        stations.append(station_record(sid, det, {}, fuels, now))
    return stations
    eligible = [s for s in stations
                if not s["membership"]
                and s["fuels"]["regular"]["card"] not in (None, 0)]
    eligible.sort(key=lambda s: s["fuels"]["regular"]["card"])
    return eligible[:TOP_N]


def fetch_slug_area(name, slug, clat, clng, now, state_code="TN", state_slug="tennessee"):
    page_url = f"https://www.gasbuddy.com/gasprices/{state_slug}/{slug}"
    html = http_get(page_url, {"User-Agent": UA,
                               "Accept": "text/html,application/xhtml+xml",
                               "Accept-Language": "en-US,en;q=0.9"})
    token = extract_token(html)
    state = extract_apollo_state(html)
    details = {k.split(":", 1)[1]: v for k, v in state.items()
               if k.startswith("Station:")}
    by_fuel = {}
    for fuel in sorted(FUELS):
        data = gql(token, page_url, "StationPrices", GQL_AREA,
                   {"area": slug, "countryCode": "US",
                    "criteria": {"location_type": ["locality", "metro"]},
                    "fuel": fuel, "regionCode": state_code})
        loc = (data.get("data") or {}).get("locationByArea") or {}
        results = ((loc.get("stations") or {}).get("results")) or []
        by_fuel[fuel] = {str(r["id"]): (r.get("prices") or [{}])[0] for r in results}
        time.sleep(1)
    stations = []
    for sid, det in details.items():
        fuels = {fname: fuel_block(by_fuel[f].get(sid, {}), now)
                 for f, fname in FUELS.items()}
        stations.append(station_record(sid, det, state, fuels, now))
        time.sleep(0)
    stations = near_filter(stations, clat, clng)
    return {"name": name, "kind": "slug", "slug": slug,
            "center": [clat, clng], "candidates": len(stations),
            "all_stations": stations,
            "stations": finalize(stations)}


def fetch_zip_area(name, zipc, clat, clng, now):
    page_url = f"https://www.gasbuddy.com/home?search={zipc}"
    html = http_get(page_url, {"User-Agent": UA,
                               "Accept": "text/html,application/xhtml+xml",
                               "Accept-Language": "en-US,en;q=0.9"})
    token = extract_token(html)
    state = extract_apollo_state(html)
    details = {k.split(":", 1)[1]: v for k, v in state.items()
               if k.startswith("Station:")}
    # all-fuel prices, one call
    data = gql(token, page_url, "SearchPrices", GQL_SEARCH,
               {"fuel": 1, "search": zipc})
    loc = ((data.get("data") or {}).get("locationBySearchTerm")) or {}
    results = ((loc.get("stations") or {}).get("results")) or []
    prices = {}
    for r in results:
        sid = str(r["id"])
        per_fuel = {}
        for p in (r.get("prices") or []):
            fp = p.get("fuelProduct")
            fname = {"regular_gas": "regular", "midgrade_gas": "midgrade",
                     "premium_gas": "premium", "diesel": "diesel"}.get(fp)
            if fname:
                per_fuel[fname] = fuel_block(p, now)
        prices[sid] = per_fuel
    time.sleep(1)
    # coords via one aliased batch
    ids = [sid for sid in details if sid in prices]
    parts = []
    for sid in ids:
        parts.append(
            f's{sid}: station(id: {sid}) {{ id name latitude longitude '
            f'address {{ line1 locality region postalCode }} brands {{ name }} }}')
    coords = {}
    if parts:
        q = "query GetStations { " + " ".join(parts) + " }"
        data = gql(token, page_url, "GetStations", q, {})
        d = (data.get("data") or {})
        for sid in ids:
            s = d.get(f"s{sid}") or {}
            coords[sid] = s
    stations = []
    for sid, det in details.items():
        if sid not in prices:
            continue
        c = coords.get(sid, {})
        if c.get("latitude"):
            det = {**det, "latitude": c["latitude"], "longitude": c["longitude"]}
        fuels = {fname: prices[sid].get(fname,
                 {"card": None, "cash": None, "posted_at": None, "age_hours": None})
                 for fname in FUELS.values()}
        stations.append(station_record(sid, det, state, fuels, now))
    stations = near_filter(stations, clat, clng)
    return {"name": name, "kind": "zip", "zip": zipc,
            "center": [clat, clng], "candidates": len(stations),
            "all_stations": stations,
            "stations": finalize(stations)}


def fetch_latlng_area(name, clat, clng, now):
    """Primary pull for metros without GasBuddy area pages: nearest
    stations to the area center, ranked to top 10."""
    stations = supplement_latlng(name, clat, clng, set(), now)
    return {"name": name, "kind": "latlng", "value": None,
            "center": [clat, clng], "candidates": len(stations),
            "stations": finalize(stations)}


def rank_top(stations):
    eligible = [s for s in stations
                if not s["membership"]
                and s["fuels"]["regular"]["card"] not in (None, 0)]
    eligible.sort(key=lambda s: s["fuels"]["regular"]["card"])
    return eligible[:TOP_N]


def finalize(stations):
    top = rank_top(stations)
    out = []
    for rank, s in enumerate(top, 1):
        reg = s["fuels"]["regular"]
        out.append({**s, "rank": rank, "regular_card": reg["card"],
                    "price_posted_at": reg["posted_at"],
                    "price_age_hours": reg["age_hours"]})
    return out


def fetch_one_metro(mkey):
    cfg = METROS[mkey]
    now = datetime.now(timezone.utc)
    areas = []
    for name, kind, value, clat, clng in cfg["areas"]:
        try:
            if kind == "slug":
                area = fetch_slug_area(name, value, clat, clng, now,
                                       cfg["state"], cfg["state_slug"])
            elif kind == "zip":
                area = fetch_zip_area(name, value, clat, clng, now)
            else:
                area = fetch_latlng_area(name, clat, clng, now)
            # thin areas: supplement with nearest-20 lat/lng pull
            # (skipped for latlng areas — that IS the lat/lng method)
            if kind != "latlng" and len(area["stations"]) < TOP_N:
                have = {s["station_id"] for s in area.get("all_stations", [])}
                try:
                    extra = supplement_latlng(name, clat, clng, have, now)
                    merged = area.get("all_stations", []) + extra
                    area["candidates"] = len(merged)
                    area["supplemented"] = len(extra)
                    area["stations"] = finalize(merged)
                except Exception as e:
                    area["supplement_error"] = str(e)[:200]
            area.pop("all_stations", None)
            areas.append(area)
            n = len(area["stations"])
            cheapest = f"${area['stations'][0]['regular_card']:.2f}" if n else "n/a"
            print(f"[ok] {name}: {area['candidates']} candidates -> top {n}, cheapest {cheapest}")
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            areas.append({"name": name, "kind": kind, "value": value,
                          "center": [clat, clng], "candidates": 0,
                          "stations": [], "error": str(e)[:200]})
        time.sleep(1.5)
    payload = {"fetched_at": now.isoformat(), "metro": mkey, "areas": areas,
               "area_count": len(areas)}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    ts = now.strftime("%Y%m%d-%H%M")
    (DATA_DIR / f"{mkey}.json").write_text(json.dumps(payload, indent=1))
    if mkey == "nashville":
        # backward compat: old readers look at latest.json
        (DATA_DIR / "latest.json").write_text(json.dumps(payload, indent=1))
    (HIST_DIR / f"{mkey}-{ts}.json").write_text(json.dumps(payload, indent=1))
    for old in sorted(HIST_DIR.glob(f"{mkey}-*.json"))[:-28]:
        old.unlink()
    print(f"\nwrote {DATA_DIR / f'{mkey}.json'}")


def do_fetch(metro=None):
    metros = [metro] if metro else list(METROS)
    for m in metros:
        fetch_one_metro(m)


# ---------------- verify ----------------
def do_verify():
    latest = DATA_DIR / "latest.json"
    if not latest.exists():
        raise SystemExit("no latest.json — run fetch first")
    payload = json.loads(latest.read_text())
    now = datetime.now(timezone.utc)
    ft = parse_time(payload.get("fetched_at"))
    fetch_age = (now - ft).total_seconds() / 3600 if ft else 1e9

    failures, warnings = [], []
    areas = payload.get("areas", [])
    if len(areas) != len(AREAS):
        failures.append(f"area count {len(areas)} != {len(AREAS)}")
    for a in areas:
        nm = a["name"]
        sts = a.get("stations", [])
        if a.get("error"):
            failures.append(f"{nm}: fetch error: {a['error']}")
            continue
        if len(sts) == 0:
            failures.append(f"{nm}: 0 stations")
            continue
        if len(sts) < TOP_N:
            warnings.append(f"{nm}: only {len(sts)}/{TOP_N} stations")
        need = ["brand", "address", "lat", "lng", "regular_card",
                "price_posted_at", "fuels"]
        bad = [s.get("rank") for s in sts if any(s.get(f) is None for f in need)]
        if bad:
            failures.append(f"{nm}: missing fields at ranks {bad}")
        prices = [s["regular_card"] for s in sts]
        if any(p is None or not (2.00 <= p <= 6.00) for p in prices):
            failures.append(f"{nm}: implausible prices {prices}")
        if prices != sorted(prices):
            failures.append(f"{nm}: not sorted ascending")
        leak = [s["brand"] for s in sts
                if MEMBERSHIP_RE.search(s.get("brand") or "")
                or MEMBERSHIP_RE.search(s.get("name") or "")]
        if leak:
            failures.append(f"{nm}: membership leaked: {leak}")
        # coords within ~15 miles of area center
        clat, clng = a["center"]
        far = [s["brand"] for s in sts
               if abs((s["lat"] or 0) - clat) > 0.25
               or abs((s["lng"] or 0) - clng) > 0.25]
        if far:
            warnings.append(f"{nm}: stations far from center: {far}")
        ages = []
        for s in sts:
            pt = parse_time(s.get("price_posted_at"))
            ages.append((now - pt).total_seconds() / 3600 if pt else 1e9)
        if max(ages) > 72:
            failures.append(f"{nm}: stale prices, oldest {max(ages):.1f}h")
        elif max(ages) > 36:
            warnings.append(f"{nm}: oldest price {max(ages):.1f}h")
    if fetch_age > 26:
        failures.append(f"fetch is {fetch_age:.1f}h old")

    passed = not failures
    report = {"verified_at": now.isoformat(), "fetched_at": payload.get("fetched_at"),
              "passed": passed, "failures": failures, "warnings": warnings,
              "areas_ok": sum(1 for a in areas if not a.get("error") and a.get("stations"))}
    (DATA_DIR / "verify-report.json").write_text(json.dumps(report, indent=1))
    print("\n== per-area verify ==")
    for f in failures:
        print("  [FAIL]", f)
    for w in warnings:
        print("  [WARN]", w)
    print(f"== {'PASS' if passed else 'FAIL'} "
          f"({report['areas_ok']}/{len(areas)} areas ok) ==\n")
    if not passed:
        sys.exit(1)


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("fetch", "verify"):
        print("usage: fetch_areas.py [fetch [metro]|verify]")
        sys.exit(2)
    if sys.argv[1] == "fetch":
        metro = sys.argv[2] if len(sys.argv) > 2 else None
        if metro and metro not in METROS:
            print(f"unknown metro {metro!r}; choose from {sorted(METROS)}")
            sys.exit(2)
        do_fetch(metro)
    else:
        do_verify()


if __name__ == "__main__":
    main()
