#!/usr/bin/env python3
"""Site catalogue files, regenerated after every run from what is published (site/static/, served at
/custom_dc/custom/...):

  catalog.json          sources -> datasets (name, description, licence or PENDING, landing page, data link,
                        coverage, places, variables, refresh date): the /data page and the sitemap
  variables.json        every variable (dcid, name, source) + each source's names/aliases: search suggestions and the drill-down picker
  data_places.json      every place that has at least one value in this instance (the search checks these first
                        when it decides whether a "by country / by province" chart has data; see custom.py)
  var_home.json         variable dcid -> the country that holds most of its data (a question that names no place,
                        e.g. "Food Poverty Line", is answered for that country instead of the World; see custom.py)
  croissant_extra.json  per dataset: keywords, spatialCoverage, variableMeasured (added to the Croissant JSON-LD;
                        the knowledge graph has no property for them yet)
Prints CHANGED / UNCHANGED.
"""

import argparse
import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path

import registries
import registry as registry_mod
import settings
from storage import LocalStore


def build(store_dir):
    store = LocalStore(store_dir)
    reg = registry_mod.load()
    registries.add_stored_feeds(reg, store)
    state = json.loads(store.read_text(settings.STATE_KEY) or "{}")
    for fid, saved in (state.get("dynamic") or {}).items():
        if fid in reg.feeds:
            reg.feeds[fid].variables = sorted(saved["variables"])
            for k, d in saved["variables"].items():
                reg.variables[k] = registry_mod.Variable(**d)
    with open(settings.SRC_DIR / "data" / "places_base.json", encoding="utf-8") as f:
        names = {p["dcid"]: p["name"] for p in json.load(f)["places"]}
    names.update({d: n for d, n in (state.get("places_used") or {}).items() if d not in names})

    sources, variables, extra, data_places = {}, {}, {}, set()
    try:
        with open(settings.PROJECT_ROOT / "site" / "static" / "places.json", encoding="utf-8") as f:
            ups = {p["dcid"]: p.get("up") or [] for p in json.load(f)["places"]}
    except (OSError, ValueError, KeyError):
        ups = {}

    def country_of(place):
        if place.startswith("country/"):
            return place
        return next((u for u in reversed(ups.get(place, [])) if u.startswith("country/")), None)

    home = {}   # variable dcid -> Counter of countries
    for fid in sorted(reg.feeds):
        text = store.read_text(f"{settings.DCP_INPUT_ROOT}/{fid}/{fid}.csv")
        if not text:
            continue
        f, st = reg.feeds[fid], state["feeds"].get(fid, {})
        src = reg.sources[f.source_key]
        rows = list(csv.DictReader(io.StringIO(text)))
        places = Counter(r["entity"][5:] for r in rows)
        data_places.update(places)
        for r in rows:
            c = country_of(r["entity"][5:])
            if c:
                home.setdefault(r["variable"][5:], Counter())[c] += 1
        dates = sorted({r["date"] for r in rows})
        var_dcids = sorted({r["variable"][5:] for r in rows})
        src_names = [x for x in [src["name"], src.get("short")] + src.get("aliases", []) if x]
        ds = {"feed": fid, "dcid": f.dataset_dcid, "name": f.dataset.get("name"),
              "description": f.dataset.get("description"), "url": f.dataset.get("url"),
              "license": f.dataset.get("license") or None, "data_url": st.get("content_url") or f.fetch.get("url"),
              "refreshed": st.get("refreshed"), "rows": len(rows), "places": len(places),
              "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
              "variables": len(var_dcids), "example_variable": var_dcids[0] if var_dcids else None,
              "example_place": places.most_common(1)[0][0] if places else None}
        entry = sources.setdefault(src["name"], {"name": src["name"], "url": src["url"], "dcid": src["dcid"],
                                                 "aliases": src_names[1:], "datasets": []})
        entry["datasets"].append(ds)
        by_dcid = {v.dcid: v for v in reg.variables.values()}
        for d in var_dcids:
            v = by_dcid.get(d)
            variables[d] = {"dcid": d, "name": v.name if v else d, "source": src["name"]}
        place_names = [names.get(p, p) for p, _ in places.most_common(60)]
        extra[f.dataset_dcid] = {
            "keywords": list(dict.fromkeys(src_names + [f.dataset.get("name")] +
                                           [x for x in (f.meta.get("Domain"), f.meta.get("Sub-domain")) if x] +
                                           place_names[:15]))[:40],
            "spatialCoverage": [{"@type": "Place", "name": n} for n in place_names[:60]],
            "variableMeasured": [variables[d]["name"] for d in var_dcids[:100]],
        }
    catalog = {"sources": sorted(sources.values(), key=lambda s: -len(s["datasets"]))}
    for s in catalog["sources"]:
        s["datasets"].sort(key=lambda d: d["name"] or "")
    src_list = [{"name": s["name"], "keywords": " ".join([s["name"]] + s["aliases"])} for s in catalog["sources"]]
    idx = {s["name"]: i for i, s in enumerate(src_list)}
    compact = {"sources": src_list,   # variables: [dcid, name, source index]
               "variables": [[v["dcid"], v["name"], idx[v["source"]]]
                             for v in sorted(variables.values(), key=lambda v: v["name"])]}
    # only variables that really belong to one country (most of their values); world-wide ones keep the World
    var_home = {v: c.most_common(1)[0][0] for v, c in sorted(home.items())
                if c and c.most_common(1)[0][1] >= 0.5 * sum(c.values())}
    return {"catalog.json": catalog, "variables.json": compact, "data_places.json": sorted(data_places),
            "var_home.json": var_home,
            "croissant_extra.json": extra}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store-dir", default=str(settings.PROJECT_ROOT / "local_store"))
    p.add_argument("--out-dir", default=str(settings.PROJECT_ROOT / "site" / "static"))
    a = p.parse_args(argv)
    changed = []
    for name, doc in build(a.store_dir).items():
        path = Path(a.out_dir) / name
        text = json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed.append(name)
    print(f"site catalogue: {'CHANGED ' + str(changed) if changed else 'UNCHANGED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
