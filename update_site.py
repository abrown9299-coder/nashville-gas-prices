#!/usr/bin/env python3
"""Build gas-site/index.html from template.html + fresh compact JSON.

Usage: update_site.py <data.json> [out.html]
Reads the compact extractor output ({"u": ..., "a": [...]}) and injects it
into the template's __GAS_DATA_JSON__ placeholder. The JSON is embedded as a
JS literal exactly like the original artifact build, so no JS changes needed.
"""
import json
import sys

PLACEHOLDER = "__GAS_DATA_JSON__"


def main():
    data_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shareable_gas_data.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/home/hatch/workspace/gas-site/index.html"
    template_path = "/home/hatch/workspace/gas-site/template.html"

    raw = open(data_path).read()
    data = json.loads(raw)  # validate
    assert isinstance(data.get("u"), str) and isinstance(data.get("a"), list)
    assert len(data["a"]) == 25, f"expected 25 views, got {len(data['a'])}"
    for slug, name, stations in data["a"]:
        assert len(stations) == 10, f"{slug}: expected 10 stations, got {len(stations)}"
        prices = [s[6] for s in stations]
        assert all(isinstance(p, (int, float)) and 2.0 < p < 6.0 for p in prices), f"{slug}: bad price"
        assert prices == sorted(prices), f"{slug}: not sorted ascending"
    assert "</script" not in raw.lower(), "unsafe sequence in data"

    template = open(template_path).read()
    assert template.count(PLACEHOLDER) == 1, "placeholder missing or duplicated"
    html = template.replace(PLACEHOLDER, raw.strip())
    open(out_path, "w").write(html)
    print(f"wrote {out_path} ({len(html)} bytes), u={data['u']}, "
          f"views={len(data['a'])}, stations={sum(len(a[2]) for a in data['a'])}")


if __name__ == "__main__":
    main()
