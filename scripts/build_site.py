#!/usr/bin/env python3
"""Build index.html from template.html + compact shareable JSON.

Usage: build_site.py --data <shareable.json> --template <template.html> --out <index.html>
"""
import argparse
import json

PLACEHOLDER = "__GAS_DATA_JSON__"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    raw = open(a.data).read()
    data = json.loads(raw)
    assert isinstance(data.get("u"), str) and isinstance(data.get("a"), list)
    assert len(data["a"]) == 25, f"expected 25 views, got {len(data['a'])}"
    for slug, name, stations in data["a"]:
        assert len(stations) == 10, f"{slug}: expected 10 stations, got {len(stations)}"
        prices = [s[6] for s in stations]
        assert all(isinstance(p, (int, float)) and 2.0 < p < 6.0 for p in prices), f"{slug}: bad price"
        assert prices == sorted(prices), f"{slug}: not sorted ascending"
    assert "</script" not in raw.lower(), "unsafe sequence in data"

    template = open(a.template).read()
    assert template.count(PLACEHOLDER) == 1, "placeholder missing or duplicated"
    html = template.replace(PLACEHOLDER, raw.strip())
    open(a.out, "w").write(html)
    print(f"wrote {a.out} ({len(html)} bytes), u={data['u']}, "
          f"views={len(data['a'])}, stations={sum(len(v[2]) for v in data['a'])}")


if __name__ == "__main__":
    main()
