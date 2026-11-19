#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

import catalog as catalog_mod
import croissant
import registries
import registry as registry_mod
import runlog
import settings
import topics as topics_mod
from storage import LocalStore

SUMMARY_MAX = 320


def licence_label(url):
    m = re.search(r"creativecommons\.org/(licenses|publicdomain)/([a-z-]+)/(\d+(?:\.\d+)?)", url or "")
    if m:
        return ("CC0 " if m.group(2) == "zero" else f"CC {m.group(2).upper()} ") + m.group(3)
    m = re.search(r"opendatacommons\.org/licenses/([a-z-]+)", url or "")
    return f"ODC {m.group(1).upper()}" if m else "Licence"


def summary(text):
    text = " ".join(str(text or "").split())
    if len(text) <= SUMMARY_MAX:
        return text
    cut = text[:SUMMARY_MAX]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + " ..."


def data_sources(catalog):
    by_topic = {}
    for s in catalog["sources"]:
        for d in s["datasets"]:
            if d.get("catalogue"):
                facts = "; ".join(x for x in [d["country"], d["domain"], d["format"],
                                              f"coverage {d['coverage']}" if d.get("coverage") else ""] if x) + "."
            else:
                span = d["first_date"] if d["first_date"] == d["last_date"] \
                    else f"{d['first_date']} to {d['last_date']}"
                facts = f"{d['variables']:,} variable{'s' if d['variables'] != 1 else ''}, {d['places']:,} " \
                        f"place{'s' if d['places'] != 1 else ''}, {span}; updated {d['refreshed']}."
            links = [f"Data made available under [{licence_label(d['license'])}]({d['license']})"]
            if d.get("url"):
                links.append(f"[Publisher's page]({d['url']})")
            links.append(f"[Croissant metadata](/croissant/{d['dcid']})")
            entry = {"label": d["name"], "url": f"/dataset/{d['dcid']}",
                     "description": f"{summary(d['description'])}\n\n{facts}\n\n{' · '.join(links)}"}
            for slug in d["topics"]:
                groups = by_topic.setdefault(slug, {})
                groups.setdefault(s["name"], {"label": s["name"], "url": s["url"], "description": None,
                                              "dataSources": []})["dataSources"].append(entry)
    return [{"title": topics_mod.TITLES[slug], "slug": slug,
             "dataSourceGroups": sorted(by_topic[slug].values(), key=lambda g: g["label"].lower())}
            for slug in topics_mod.ORDER if slug in by_topic]


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

    sources, variables, extra, var_keywords, data_places = {}, {}, {}, {}, set()
    try:
        with open(settings.PROJECT_ROOT / "site" / "static" / "places.json", encoding="utf-8") as f:
            ups = {p["dcid"]: p.get("up") or [] for p in json.load(f)["places"]}
    except (OSError, ValueError, KeyError):
        ups = {}

    def country_of(place):
        if place.startswith("country/"):
            return place
        return next((u for u in reversed(ups.get(place, [])) if u.startswith("country/")), None)

    home = {}
    for fid in sorted(reg.feeds):
        text = store.read_text(f"{settings.DCP_INPUT_ROOT}/{fid}/{fid}.csv")
        if not text or not croissant.publishable(reg.feeds[fid], reg):
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
        by_dcid = {v.dcid: v for v in reg.variables.values()}
        labels = [x for x in (f.meta.get("Sub-domain"), f.meta.get("Domain")) if x] or sorted(
            {by_dcid[d].group.split("/")[-1] for d in var_dcids if d in by_dcid and by_dcid[d].group})
        ds = {"feed": fid, "dcid": f.dataset_dcid, "name": f.dataset.get("name"),
              "description": f.dataset.get("description"), "url": f.dataset.get("url"),
              "license": f.dataset.get("license") or None, "data_url": st.get("content_url") or f.fetch.get("url"),
              "refreshed": st.get("refreshed"), "rows": len(rows), "places": len(places),
              "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
              "variables": len(var_dcids), "example_variable": var_dcids[0] if var_dcids else None,
              "example_place": places.most_common(1)[0][0] if places else None,
              "topics": topics_mod.classify(labels, f"{f.dataset.get('name')} {f.dataset.get('description')}")}
        entry = sources.setdefault(src["name"], {"name": src["name"], "url": src["url"], "dcid": src["dcid"],
                                                 "aliases": src_names[1:], "datasets": []})
        entry["datasets"].append(ds)
        kw_words = " ".join(x for x in src_names + [f.dataset.get("name"), f.meta.get("Domain"),
                                                    f.meta.get("Sub-domain")] if x)
        for d in var_dcids:
            v = by_dcid.get(d)
            variables[d] = {"dcid": d, "name": v.name if v else d, "source": src["name"]}
            var_keywords[d] = kw_words
        place_names = [names.get(p, p) for p, _ in places.most_common(60)]
        extra[f.dataset_dcid] = {
            "keywords": list(dict.fromkeys(src_names + [f.dataset.get("name")] +
                                           [x for x in (f.meta.get("Domain"), f.meta.get("Sub-domain")) if x] +
                                           place_names[:15]))[:40],
            "spatialCoverage": [{"@type": "Place", "name": n} for n in place_names[:60]],
            "variableMeasured": [variables[d]["name"] for d in var_dcids[:100]],
        }
    refreshed = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    for rec in catalog_mod.stored(store):
        src = catalog_mod.source_of(rec, reg)
        entry = sources.setdefault(src["name"], {"name": src["name"], "url": src["url"], "dcid": src["dcid"],
                                                 "aliases": [], "datasets": []})
        entry["datasets"].append({
            "feed": None, "catalogue": True, "dcid": catalog_mod.dataset_dcid(rec, reg), "name": rec["name"],
            "description": catalog_mod.full_description(rec), "url": rec["url"].split()[0],
            "license": rec["license_url"].split()[0], "license_name": rec["license"],
            "data_url": rec["content_url"].split()[0], "format": rec["format"], "country": rec["country"],
            "place": rec["dcid"], "domain": rec["domain"], "geo_level": rec["geo_level"],
            "coverage": rec.get("coverage"), "frequency": rec.get("frequency"),
            "published": rec["published"] if catalog_mod.known(rec.get("published")) else None,
            "refreshed": refreshed, "rows": 0, "places": 1, "first_date": None, "last_date": None, "variables": 0,
            "topics": topics_mod.classify([rec["domain"]], f"{rec['name']} {rec['description']}")})
        extra[catalog_mod.dataset_dcid(rec, reg)] = {
            "keywords": [x for x in (rec["indicator"], rec["country"], rec["domain"], src["name"]) if x],
            "spatialCoverage": [{"@type": "Place", "name": rec["country"]}]}
    catalog = {"sources": sorted(sources.values(), key=lambda s: -len(s["datasets"]))}
    for s in catalog["sources"]:
        s["datasets"].sort(key=lambda d: d["name"] or "")
    src_list = [{"name": s["name"], "keywords": " ".join([s["name"]] + s["aliases"])} for s in catalog["sources"]]
    idx = {s["name"]: i for i, s in enumerate(src_list)}
    compact = {"sources": src_list,
               "variables": [[v["dcid"], v["name"], idx[v["source"]]]
                             for v in sorted(variables.values(), key=lambda v: v["name"])],
               "keywords": {d: var_keywords[d] for d in sorted(var_keywords)}}
    var_home = {v: c.most_common(1)[0][0] for v, c in sorted(home.items())
                if c and c.most_common(1)[0][1] >= 0.5 * sum(c.values())}
    return {"catalog.json": catalog, "data_sources.json": data_sources(catalog), "variables.json": compact,
            "data_places.json": sorted(data_places),
            "var_home.json": var_home,
            "croissant_extra.json": extra}


def main(argv=None):
    p = argparse.ArgumentParser()
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
    print(runlog.item("Website dataset catalogue", "changed" if changed else "unchanged",
                      ", ".join(changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
