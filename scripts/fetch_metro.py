#!/usr/bin/env python3
"""Fetch and verify greater-Nashville cheapest gas stations from GasBuddy.

usage: nashville_gas.py [fetch|verify]
  fetch  -> pulls live data into ~/workspace/gas-data/latest.json
  verify -> checks latest.json, writes verify-report.json (exit 1 on FAIL)
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
AREA = "nashville"
TOP_N = 10
DATA_DIR = Path(os.environ.get("GAS_DATA_ROOT", Path.home() / "workspace" / "gas-data"))
RAW_DIR = DATA_DIR / "raw"
HIST_DIR = DATA_DIR / "history"
FUELS = {1: "regular", 2: "midgrade", 3: "premium", 4: "diesel"}
# greater Nashville bounding box: lat 35.85-36.75, lng -87.35 to -85.95
BBOX = (35.85, 36.75, -87.35, -85.95)
MEMBERSHIP_RE = re.compile(r"sam'?s|costco|\bbj'?s\b", re.I)

GQL_QUERY = ("query StationPrices($area:String,$countryCode:String,$criteria:Criteria,"
             "$fuel:Int,$regionCode:String){locationByArea(area:$area,countryCode:$countryCode,"
             "criteria:$criteria,regionCode:$regionCode){displayName stations(fuel:$fuel){results{id "
             "prices(fuel:$fuel){cash{nickname postedTime price formattedPrice} "
             "credit{nickname postedTime price formattedPrice} fuelProduct}}}}}")


def http_get(url, headers=None, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"GET {url} failed after {tries} tries: {last}")


def extract_token(html):
    m = re.search(r'gbcsrf\s*=\s*"([^"]+)"', html)
    if not m:
        raise RuntimeError("gbcsrf token not found in page HTML")
    return m.group(1)


def extract_apollo_state(html):
    m = re.search(r'window\.__APOLLO_STATE__\s*=\s*', html)
    if not m:
        raise RuntimeError("__APOLLO_STATE__ not found in page HTML")
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


def gql_prices(token, fuel):
    body = json.dumps({
        "operationName": "StationPrices",
        "query": GQL_QUERY,
        "variables": {"area": AREA, "countryCode": "US",
                      "criteria": {"location_type": ["locality", "metro"]},
                      "fuel": fuel, "regionCode": "TN"},
    }).encode()
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(
                "https://www.gasbuddy.com/graphql", data=body,
                headers={"User-Agent": UA, "Content-Type": "application/json",
                         "apollo-require-preflight": "true", "gbcsrf": token,
                         "Origin": "https://www.gasbuddy.com",
                         "Referer": f"https://www.gasbuddy.com/gasprices/tennessee/{AREA}"})
            return json.loads(urllib.request.urlopen(req, timeout=25).read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"GraphQL fuel={fuel} failed after 4 tries: {last}")


def is_membership(brand, name):
    return bool(MEMBERSHIP_RE.search(brand or "") or MEMBERSHIP_RE.search(name or ""))


def parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def do_fetch():
    page_url = f"https://www.gasbuddy.com/gasprices/tennessee/{AREA}"
    html = http_get(page_url, {"User-Agent": UA,
                               "Accept": "text/html,application/xhtml+xml",
                               "Accept-Language": "en-US,en;q=0.9"})
    token = extract_token(html)
    state = extract_apollo_state(html)
    details = {k.split(":", 1)[1]: v for k, v in state.items()
               if k.startswith("Station:")}

    # prices per fuel type
    by_fuel = {}
    for fuel in sorted(FUELS):
        data = gql_prices(token, fuel)
        errs = data.get("errors") or []
        if errs:
            raise RuntimeError(f"GraphQL errors for fuel={fuel}: {errs[0]['message']}")
        loc = (data.get("data") or {}).get("locationByArea") or {}
        results = ((loc.get("stations") or {}).get("results")) or []
        by_fuel[fuel] = {r["id"]: (r.get("prices") or [{}])[0] for r in results}
        time.sleep(1)

    now = datetime.now(timezone.utc)
    stations = []
    for sid, det in details.items():
        addr = det.get("address") or {}
        brands = [b.get("name") for b in det.get("brands", []) if b.get("name")]
        brand = brands[0] if brands else (det.get("name") or "Unknown")
        name = det.get("name") or brand
        fuels = {}
        for fuel, fname in FUELS.items():
            p = (by_fuel.get(fuel) or {}).get(sid, {})
            cr, ca = p.get("credit") or {}, p.get("cash") or {}
            posted = cr.get("postedTime") or ca.get("postedTime")
            pt = parse_time(posted)
            fuels[fname] = {
                "card": cr.get("price"),
                "cash": ca.get("price"),
                "posted_at": posted,
                "age_hours": round((now - pt).total_seconds() / 3600, 1) if pt else None,
            }
        stations.append({
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
        })

    # rank: non-membership, must have a regular CARD price
    eligible = [s for s in stations
                if not s["membership"] and s["fuels"]["regular"]["card"] is not None]
    eligible.sort(key=lambda s: s["fuels"]["regular"]["card"])
    top = []
    for rank, s in enumerate(eligible[:TOP_N], 1):
        reg = s["fuels"]["regular"]
        top.append({**s, "rank": rank,
                    "regular_card": reg["card"],
                    "price_posted_at": reg["posted_at"],
                    "price_age_hours": reg["age_hours"]})

    payload = {"fetched_at": now.isoformat(), "area": AREA,
               "station_count_raw": len(stations),
               "membership_excluded": sum(1 for s in stations if s["membership"]),
               "stations": top}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    ts = now.strftime("%Y%m%d-%H%M")
    (RAW_DIR / f"raw-{ts}.json").write_text(json.dumps(
        {"fetched_at": payload["fetched_at"], "stations": stations}, indent=1))
    (DATA_DIR / "latest.json").write_text(json.dumps(payload, indent=1))
    (HIST_DIR / f"top10-{ts}.json").write_text(json.dumps(payload, indent=1))
    # prune history to last 30
    for old in sorted(HIST_DIR.glob("top10-*.json"))[:-30]:
        old.unlink()
    for old in sorted(RAW_DIR.glob("raw-*.json"))[:-30]:
        old.unlink()

    print(f"fetched {len(stations)} stations, "
          f"{payload['membership_excluded']} membership excluded, "
          f"top {len(top)} written to {DATA_DIR / 'latest.json'}")
    for s in top:
        print(f"  {s['rank']}. {s['brand']} — {s['address']}, {s['city']} "
              f"${s['regular_card']:.2f} ({s['price_age_hours']}h ago)")


# ---------------- verify ----------------
CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


@check("ten_stations")
def _c(payload, raw):
    n = len(payload["stations"])
    return n == TOP_N, f"{n} stations (want {TOP_N})"


@check("required_fields")
def _c(payload, raw):
    need = ["brand", "address", "city", "lat", "lng", "regular_card",
            "price_posted_at", "fuels"]
    bad = [s["rank"] for s in payload["stations"]
           if any(s.get(f) is None for f in need)]
    return not bad, f"stations missing fields: {bad}" if bad else "all present"


@check("coords_in_nashville")
def _c(payload, raw):
    lat0, lat1, lng0, lng1 = BBOX
    bad = [s["brand"] for s in payload["stations"]
           if not (lat0 <= s["lat"] <= lat1 and lng0 <= s["lng"] <= lng1)]
    return not bad, f"out of bounds: {bad}" if bad else "all in bbox"


@check("prices_sane")
def _c(payload, raw):
    bad = [s["brand"] for s in payload["stations"]
           if not (2.00 <= s["regular_card"] <= 6.00)]
    return not bad, f"implausible prices: {bad}" if bad else "all $2–$6"


@check("no_membership_leak")
def _c(payload, raw):
    bad = [s["brand"] for s in payload["stations"]
           if MEMBERSHIP_RE.search(s["brand"] or "")
           or MEMBERSHIP_RE.search(s["name"] or "")]
    return not bad, f"{len(bad)} membership stations leaked" if bad else "none"


@check("sorted_ascending")
def _c(payload, raw):
    prices = [s["regular_card"] for s in payload["stations"]]
    return prices == sorted(prices), "rank order matches price order"


@check("matches_raw_top10")
def _c(payload, raw):
    if not raw:
        return False, "no raw pull to compare against"
    elig = [s for s in raw["stations"]
            if not s["membership"]
            and s["fuels"]["regular"]["card"] is not None]
    elig.sort(key=lambda s: s["fuels"]["regular"]["card"])
    expected = [s["station_id"] for s in elig[:TOP_N]]
    actual = [s["station_id"] for s in payload["stations"]]
    return expected == actual, ("top-10 matches raw recompute"
                                if expected == actual
                                else f"mismatch: expected {expected}, got {actual}")


@check("prices_fresh")
def _c(payload, raw):
    now = datetime.now(timezone.utc)
    ages = []
    for s in payload["stations"]:
        pt = parse_time(s.get("price_posted_at"))
        ages.append((now - pt).total_seconds() / 3600 if pt else 1e9)
    worst = max(ages)
    if worst > 72:
        return False, f"stale: oldest price {worst:.1f}h old"
    if worst > 36:
        return True, f"WARN: oldest price {worst:.1f}h old"
    return True, f"oldest price {worst:.1f}h old"


@check("fetch_fresh")
def _c(payload, raw):
    now = datetime.now(timezone.utc)
    ft = parse_time(payload.get("fetched_at"))
    age = (now - ft).total_seconds() / 3600 if ft else 1e9
    return age <= 26, f"data fetched {age:.1f}h ago"


def do_verify():
    latest = DATA_DIR / "latest.json"
    if not latest.exists():
        raise SystemExit("no latest.json — run fetch first")
    payload = json.loads(latest.read_text())
    raws = sorted(RAW_DIR.glob("raw-*.json")) if RAW_DIR.exists() else []
    raw = json.loads(raws[-1].read_text()) if raws else None

    results = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn(payload, raw)
        except Exception as e:
            ok, detail = False, f"check crashed: {e}"
        status = "PASS" if ok and not detail.startswith("WARN") else \
                 "WARN" if ok else "FAIL"
        results.append({"check": name, "status": status, "detail": detail})

    passed = all(r["status"] != "FAIL" for r in results)
    report = {"verified_at": datetime.now(timezone.utc).isoformat(),
              "fetched_at": payload.get("fetched_at"),
              "passed": passed, "checks": results,
              "cheapest": payload["stations"][0]["regular_card"]
              if payload["stations"] else None}
    (DATA_DIR / "verify-report.json").write_text(json.dumps(report, indent=1))

    print("\n== verify report ==")
    for r in results:
        print(f"  [{r['status']}] {r['check']}: {r['detail']}")
    print(f"== {'PASS' if passed else 'FAIL'} ==\n")
    if not passed:
        sys.exit(1)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("fetch", "verify"):
        print("usage: nashville_gas.py [fetch|verify]")
        sys.exit(2)
    if sys.argv[1] == "fetch":
        do_fetch()
    else:
        do_verify()


if __name__ == "__main__":
    main()
