#!/usr/bin/env python3
import argparse
import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8090")
    p.add_argument("--local-dc", default=str(ROOT / "local_dc"))
    args = p.parse_args(argv)

    expected, units = {}, {}
    for f in sorted(Path(args.local_dc).glob("*.csv")):
        for r in csv.DictReader(f.open(encoding="utf-8")):
            expected[(r["variable"], r["entity"], r["date"])] = float(r["value"])
            units[r["variable"]] = r["unit"]
    variables = sorted({k[0] for k in expected})
    entities = sorted({k[1] for k in expected})

    served, served_units = {}, {}
    for v in variables:
        q = ([("variable.dcids", v)] + [("entity.dcids", e) for e in entities]
             + [("select", s) for s in ("entity", "variable", "value", "date")])
        url = f"{args.url}/core/api/v2/observation?" + urllib.parse.urlencode(q)
        data = json.load(urllib.request.urlopen(url, timeout=120))
        for entity, body in data["byVariable"][v].get("byEntity", {}).items():
            for facet in body.get("orderedFacets", []):
                for obs in facet["observations"]:
                    served[(v, entity, obs["date"])] = float(obs["value"])
        served_units[v] = sorted({f.get("unit", "") for f in data.get("facets", {}).values()})

    missing = [k for k in expected if k not in served]
    wrong = [k for k in expected if k in served and abs(served[k] - expected[k]) > 1e-9 * max(1, abs(expected[k]))]
    extra = [k for k in served if k not in expected]
    bad_units = [v for v in variables if served_units[v] != [units[v]]]
    print(f"expected {len(expected)}  served {len(served)}  missing {len(missing)}  wrong {len(wrong)}  "
          f"extra {len(extra)}  unit mismatches {len(bad_units)}")
    ok = not (missing or wrong or extra or bad_units)
    if not ok:
        print("examples:", missing[:3], wrong[:3], extra[:3], bad_units[:3])
    print("READBACK PASS" if ok else "READBACK FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
