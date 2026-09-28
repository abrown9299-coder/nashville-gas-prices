#!/usr/bin/env python3
"""Build the compact shareable JSON at /tmp/shareable_gas_data.json from
the fetched gas data, with per-area carry-forward.

Policy (see refresh-policy-spec.md):
- An area is FRESH if its cheapest station's price is <= 72h old and it
  passes hard checks (no fetch error, 10 stations, sorted, no memberships,
  fields present, plausible prices).
- A stale/broken area is CARRIED FORWARD from the newest
  ~/workspace/gas-data/areas/history snapshot where it was fresh, with
  station ages advanced by elapsed time (labels stay truthful).
- Abort (exit 1, write nothing) if: >6 of 25 views need carry-forward, the
  best carry would be >168h old, metro data is unusable, area count != 24,
  or the fetch itself is >26h old.

Format: {"u": "YYYY-MM-DD HH:MM UTC",
         "a": [[slug, display_name, stations], ...]}   (24 areas + overall)
Each station: [name, brand, address, city, lat, lng, price, age_text]
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

GAS_DATA = Path(os.environ.get("GAS_DATA_ROOT", Path.home() / "workspace" / "gas-data"))
AREAS_JSON = GAS_DATA / "areas" / "latest.json"
HIST_DIR = GAS_DATA / "areas" / "history"
METRO_JSON = GAS_DATA / "latest.json"
OUT = Path(os.environ.get("SHARE_OUT", "/tmp/shareable_gas_data.json"))
MEMBERSHIP_RE = re.compile(r"sam'?s|costco|\bbj'?s\b", re.I)

STALE_H = 72        # cheapest station older than this -> area is stale
MAX_CARRY_H = 168  # never carry data older than 7 days
MAX_CARRIED = 6    # abort if more than this many views need carry-forward
EXPECTED_AREAS = 24


def slugify(display):
    s = display.lower()
    s = s.replace(" / ", "-")
    s = re.sub(r"[^a-z0-9\- ]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def age_text(age_hours):
    if age_hours is None:
        return "n/a"
    if age_hours < 1:
        return f"{int(round(age_hours * 60))}m ago"
    return f"{int(round(age_hours))}h ago"


def parse_time(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def hard_problems(area):
    """Return list of hard-failure reasons (empty = passes hard checks)."""
    probs = []
    if area.get("error"):
        probs.append(f"fetch error: {area['error']}")
        return probs
    sts = area.get("stations", [])
    if len(sts) == 0:
        probs.append("0 stations")
        return probs
    if len(sts) != 10:
        probs.append(f"{len(sts)} stations, expected 10")
    prices = [s.get("regular_card") for s in sts]
    if any(p is None or not (2.00 <= p <= 6.00) for p in prices):
        probs.append(f"implausible prices {prices}")
    if prices != sorted(prices):
        probs.append("not sorted ascending")
    leak = [s.get("brand") for s in sts
            if MEMBERSHIP_RE.search(s.get("brand") or "")
            or MEMBERSHIP_RE.search(s.get("name") or "")]
    if leak:
        probs.append(f"membership leaked: {leak}")
    missing = [s.get("rank") for s in sts
               if any(s.get(f) is None for f in
                       ("name", "brand", "address", "city", "lat", "lng",
                        "regular_card"))]
    if missing:
        probs.append(f"missing fields at ranks {missing}")
    return probs


def cheapest_age(sts):
    if not sts:
        return 1e9
    a = sts[0].get("price_age_hours")
    return a if a is not None else 1e9


def is_fresh(area):
    return not hard_problems(area) and cheapest_age(area.get("stations", [])) <= STALE_H


def carry_forward(name, now):
    """Newest history snapshot where `name` was fresh. Returns
    (stations, carried_age_h of cheapest) or raises SystemExit on abort."""
    snaps = sorted(HIST_DIR.glob("areas-*.json"), reverse=True)
    for snap in snaps:
        try:
            d = json.loads(snap.read_text())
        except Exception:
            continue
        ft = parse_time(d.get("fetched_at"))
        if not ft:
            continue
        elapsed_h = (now - ft).total_seconds() / 3600
        for a in d.get("areas", []):
            if a.get("name") != name:
                continue
            if not is_fresh(a):
                break  # this snapshot's version isn't fresh; try older
            sts = a["stations"]
            carried = []
            for s in sts:
                s2 = dict(s)
                base = s.get("price_age_hours")
                s2["price_age_hours"] = (base + elapsed_h) if base is not None else None
                carried.append(s2)
            age_now = cheapest_age(carried)
            if age_now > MAX_CARRY_H:
                raise SystemExit(
                    f"ABORT: best carry-forward for {name} would be "
                    f"{age_now:.1f}h old (>{MAX_CARRY_H}h)")
            # re-check hard problems on carried data (ages excluded)
            probs = hard_problems({**a, "stations": carried})
            if probs:
                break
            return carried, age_now
    raise SystemExit(f"ABORT: no fresh history snapshot found for {name}")


def compact_station(s):
    return [s["name"], s["brand"], s["address"], s["city"],
            s["lat"], s["lng"], float(s["regular_card"]),
            age_text(s.get("price_age_hours"))]


def main():
    now = datetime.now(timezone.utc)
    payload = json.loads(AREAS_JSON.read_text())
    ft = parse_time(payload.get("fetched_at"))
    fetch_age = (now - ft).total_seconds() / 3600 if ft else 1e9
    if fetch_age > 26:
        raise SystemExit(f"ABORT: fetch is {fetch_age:.1f}h old")

    areas = payload.get("areas", [])
    if len(areas) != EXPECTED_AREAS:
        raise SystemExit(f"ABORT: area count {len(areas)} != {EXPECTED_AREAS}")

    out_areas = []
    carried = []
    for a in areas:
        name = a["name"]
        probs = hard_problems(a)
        sts = a.get("stations", [])
        if not probs and cheapest_age(sts) <= STALE_H:
            out_areas.append([slugify(name), name,
                              [compact_station(s) for s in sts]])
            continue
        reason = "; ".join(probs) if probs else (
            f"cheapest {cheapest_age(sts):.1f}h old")
        csts, cage = carry_forward(name, now)
        out_areas.append([slugify(name), name,
                          [compact_station(s) for s in csts]])
        carried.append(f"{slugify(name)} ({reason}; carried at {cage:.1f}h)")

    # overall metro view (no usable metro history -> must be fresh or abort)
    metro = json.loads(METRO_JSON.read_text())
    msts = metro.get("stations", [])
    mprobs = hard_problems({"stations": msts})
    if mprobs or cheapest_age(msts) > STALE_H:
        raise SystemExit(
            f"ABORT: metro overall unusable: "
            f"{'; '.join(mprobs) or f'cheapest {cheapest_age(msts):.1f}h old'}")
    out_areas.append(["overall", "Cheapest Overall",
                      [compact_station(s) for s in msts]])

    if len(carried) > MAX_CARRIED:
        raise SystemExit(
            f"ABORT: {len(carried)} views need carry-forward "
            f"(>{MAX_CARRIED}): {carried}")

    out = {"u": now.strftime("%Y-%m-%d %H:%M UTC"), "a": out_areas}
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {OUT} ({len(out_areas)} views, u={out['u']})")
    print(f"FRESH: {len(out_areas) - len(carried)}/{len(out_areas)}")
    if carried:
        print("CARRIED:")
        for c in carried:
            print(f"  - {c}")
    else:
        print("CARRIED: none")


if __name__ == "__main__":
    main()
