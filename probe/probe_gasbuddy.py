#!/usr/bin/env python3
"""TEMPORARY probe: can a GitHub Actions runner reach GasBuddy?

Uses the WPR recipe (curl_cffi browser impersonation + gbcsrf + GraphQL).
Single area, short backoffs. Prints a machine-readable RESULT line.
"""
import json
import re
import sys
import time

from curl_cffi import requests as crequests

ENTRY_URLS = ["https://www.gasbuddy.com/home", "https://www.gasbuddy.com/"]
PROFILES = ["chrome131", "chrome", "safari17_0"]
BACKOFFS = [5, 10, 15]
GRAPHQL_URL = "https://www.gasbuddy.com/graphql"

QUERY = """query LocationBySearchTerm($lat: Float, $lng: Float, $search: String) {
  locationBySearchTerm(lat: $lat, lng: $lng, search: $search) {
    stations(fuel: 1, lat: $lat, lng: $lng, maxAge: 0) {
      results { name prices { cash { price } fuelProduct } }
    }
  }
}"""


def establish_session():
    attempt = 0
    for url in ENTRY_URLS:
        for profile in PROFILES:
            attempt += 1
            if attempt > 6:
                return None, None
            try:
                s = crequests.Session(impersonate=profile)
                r = s.get(url, timeout=30)
                body = r.text or ""
                if r.status_code == 200 and "window.gbcsrf" in body:
                    m = re.search(r"window\.gbcsrf\s*=\s*[\"']([^\"']+)[\"']", body)
                    if m:
                        print(f"[session] OK attempt {attempt}: {profile} @ {url}",
                              flush=True)
                        return s, m.group(1)
                    print(f"[session] attempt {attempt}: 200 no gbcsrf ({profile})",
                          flush=True)
                else:
                    print(f"[session] attempt {attempt}: HTTP {r.status_code} "
                          f"({profile} @ {url})", flush=True)
            except Exception as e:
                print(f"[session] attempt {attempt}: {type(e).__name__}: {e}",
                      flush=True)
            if attempt < 6:
                time.sleep(BACKOFFS[min(attempt - 1, len(BACKOFFS) - 1)])
    return None, None


def main():
    session, csrf = establish_session()
    if not session:
        print("RESULT: FAIL_NO_SESSION")
        sys.exit(1)
    headers = {
        "Content-Type": "application/json",
        "apollo-require-preflight": "true",
        "Origin": "https://www.gasbuddy.com",
        "Referer": "https://www.gasbuddy.com/home",
        "gbcsrf": csrf,
    }
    payload = {
        "operationName": "LocationBySearchTerm",
        "query": QUERY,
        "variables": {"lat": 36.12, "lng": -86.79, "search": "berry hill"},
    }
    try:
        r = session.post(GRAPHQL_URL, headers=headers, json=payload, timeout=30)
    except Exception as e:
        print(f"[graphql] error {type(e).__name__}: {e}", flush=True)
        print("RESULT: FAIL_GRAPHQL_ERROR")
        sys.exit(1)
    print(f"[graphql] HTTP {r.status_code}", flush=True)
    if r.status_code != 200:
        print(f"[graphql] body head: {r.text[:200]!r}", flush=True)
        print("RESULT: FAIL_GRAPHQL_HTTP")
        sys.exit(1)
    try:
        data = r.json()
    except Exception:
        print("RESULT: FAIL_GRAPHQL_NOT_JSON")
        sys.exit(1)
    if "errors" in data:
        print(f"[graphql] errors: {json.dumps(data['errors'])[:300]}", flush=True)
        print("RESULT: FAIL_GRAPHQL_ERRORS")
        sys.exit(1)
    loc = (data.get("data") or {}).get("locationBySearchTerm") or {}
    stations = ((loc.get("stations") or {}).get("results")) or []
    print(f"[graphql] stations: {len(stations)}", flush=True)
    for st in stations[:5]:
        reg = next((p for p in st.get("prices") or []
                    if p.get("fuelProduct") == "regular_gas"), None)
        price = (reg.get("cash") or {}).get("price") if reg else None
        print(f"  - {st.get('name')}: ${price}", flush=True)
    print(f"RESULT: {'PASS' if stations else 'EMPTY'}")
    sys.exit(0 if stations else 2)


if __name__ == "__main__":
    main()
