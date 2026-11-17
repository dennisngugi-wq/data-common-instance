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

    config = json.loads((Path(args.local_dc) / "config.json").read_text(encoding="utf-8"))
    expected, units = {}, {}
    for f in sorted(Path(args.local_dc).glob("*.csv")):
        prov = config["inputFiles"][f.name]["provenance"]
        for r in csv.DictReader(f.open(encoding="utf-8")):
            expected[(prov, r["variable"], r["entity"], r["date"])] = float(r["value"])
            units.setdefault((prov, r["variable"]), set()).add(r["unit"])
    ours = {k[0] for k in expected}
    variables = sorted({k[1] for k in expected})

    served, served_units = {}, {}
    for v in variables:
        entities = sorted({k[2] for k in expected if k[1] == v})
        q = ([("variable.dcids", v)] + [("entity.dcids", e) for e in entities]
             + [("select", s) for s in ("entity", "variable", "value", "date", "facet")])
        url = f"{args.url}/core/api/v2/observation?" + urllib.parse.urlencode(q)
        data = json.load(urllib.request.urlopen(url, timeout=120))
        facets = data.get("facets", {})
        for entity, body in data["byVariable"][v].get("byEntity", {}).items():
            for facet in body.get("orderedFacets", []):
                meta = facets.get(facet["facetId"], {})
                prov = meta.get("importName")
                if prov not in ours:
                    continue
                served_units.setdefault((prov, v), set()).add(meta.get("unit", ""))
                for obs in facet["observations"]:
                    served[(prov, v, entity, obs["date"])] = float(obs["value"])

    missing = [k for k in expected if k not in served]
    wrong = [k for k in expected if k in served and abs(served[k] - expected[k]) > 1e-9 * max(1, abs(expected[k]))]
    extra = [k for k in served if k not in expected]
    bad_units = [k for k in units if served_units.get(k, set()) != units[k]]
    print(f"expected {len(expected)}  served {len(served)}  missing {len(missing)}  wrong {len(wrong)}  "
          f"extra {len(extra)}  unit mismatches {len(bad_units)}")
    ok = not (missing or wrong or extra or bad_units)
    if not ok:
        print("examples:", missing[:3], wrong[:3], extra[:3], bad_units[:3])
    print("READBACK PASS" if ok else "READBACK FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
