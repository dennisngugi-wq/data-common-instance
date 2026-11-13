#!/usr/bin/env python3
"""Write site/static/places.json: the place index behind the site's place search boxes (used when there is no
Google Maps key; see site/static/place_search.js and custom.py).

= the built-in gazetteer (World, continents, UN M49 sub-regions, 215 countries) + every other place the feeds
resolved (provinces, districts, cities...), from the pipeline state.

Every place carries its hierarchy TOP-DOWN, so the site can show and order places from the World downwards:
  path   ["World", "Africa", "Eastern Africa"]          names of the ancestors, highest first
  parent "World › Africa › Eastern Africa"               the same, for display
  up     ["Earth", "africa", "EasternAfrica"]                the same ancestors as dcids (the drill-down's trail)
  level  0 World, 1 continent, 2 region, 3 country, 4 first admin level (province/state/county), 5 second, 6 other
Ancestors of places outside the gazetteer come from the Data Commons graph (containedInPlace), cached in
<store>/places/parents.json so the nightly run gives the same answer every time.
Prints CHANGED / UNCHANGED.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import settings

SEP = " › "
LEVELS = {"Place": 0, "Continent": 1, "Region": 2, "Country": 3, "AdministrativeArea1": 4, "State": 4,
          "Province": 4, "County": 4, "AdministrativeArea2": 5, "District": 5, "AdministrativeArea3": 6,
          "City": 6, "Town": 6}
CACHE_KEY = "places/parents.json"


def _level(p):
    if p["dcid"] == "Earth":
        return 0
    return LEVELS.get(p.get("type"), 6) if p.get("type") != "Place" else 6


def _lookup(dcids, cache, stop):
    """dcid -> {"name", "types", "parents": [dcid]} from the graph (cached), following parents upwards until a place
    of the gazetteer (`stop`) is reached: the graph often links a district only to the area just above it."""
    from places import PlaceResolver
    api = None
    todo, seen = [d for d in dcids if d not in cache], set()
    for _ in range(6):  # levels: district -> ... -> country
        todo = [d for d in dict.fromkeys(todo) if d not in cache and d not in seen and d not in stop]
        if not todo:
            break
        api = api or PlaceResolver()
        if not api.allow_api:
            logging.info("%d places need their parents from the Data Commons API, but DC_API_KEY is not set", len(todo))
            break
        nxt = []
        for i in range(0, len(todo), 50):
            batch = todo[i:i + 50]
            seen.update(batch)
            try:
                up = api._post("/v2/node", {"nodes": batch, "property": "->containedInPlace"}).get("data", {})
                ty = api._post("/v2/node", {"nodes": batch, "property": "->[typeOf, name]"}).get("data", {})
            except RuntimeError as exc:
                logging.warning("place parents lookup failed: %s", exc)
                return cache
            for d in batch:
                arcs = (ty.get(d) or {}).get("arcs", {})
                parents = [n["dcid"] for n in ((up.get(d) or {}).get("arcs", {}).get("containedInPlace", {}) or {})
                           .get("nodes", []) if n.get("dcid")]
                cache[d] = {"types": [n.get("dcid") for n in (arcs.get("typeOf") or {}).get("nodes", [])],
                            "name": next((n.get("value") for n in (arcs.get("name") or {}).get("nodes", [])), ""),
                            "parents": parents}
                nxt += parents
        todo = nxt
    return cache


def _ancestors(d, cache, stop):
    out, stack = set(), list((cache.get(d) or {}).get("parents") or [])
    while stack:
        x = stack.pop()
        if x in out or x == d:
            continue
        out.add(x)
        if x not in stop:
            stack += (cache.get(x) or {}).get("parents") or []
    return out


def _own_type(types):
    """Most specific known type of a place (AdministrativeArea2 before AdministrativeArea1 before ... )."""
    known = sorted((LEVELS[t], t) for t in types if t in LEVELS and t != "Place")
    return known[-1][1] if known else "Place"


def build(store_dir, out_path):
    with open(settings.SRC_DIR / "data" / "places_base.json", encoding="utf-8") as f:
        base = json.load(f)["places"]
    by_dcid = {p["dcid"]: p for p in base}
    # gazetteer parents are stored bottom-up ("Eastern Africa, Africa"): turn them into a top-down path
    for p in base:
        up = [x.strip() for x in (p.get("parent") or "").split(",") if x.strip()]
        p["path"] = [] if p["dcid"] == "Earth" else (["World"] + list(reversed(up)) if up != ["World"] else ["World"])
        p["level"] = _level(p)
    name_to_dcid = {p["name"]: p["dcid"] for p in sorted(base, key=lambda p: -p["level"])}
    for p in base:
        p["up"] = [name_to_dcid.get(n, n) for n in p["path"]]

    store = Path(store_dir)
    state_path = store / settings.STATE_KEY
    used = json.loads(state_path.read_text(encoding="utf-8")).get("places_used", {}) if state_path.exists() else {}
    extra_ids = sorted(d for d in used if d not in by_dcid)
    cache_path = store / CACHE_KEY
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    before = json.dumps(cache, sort_keys=True)
    cache = _lookup(extra_ids, cache, by_dcid)
    if json.dumps(cache, sort_keys=True) != before:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    extra = []
    for d in extra_ids:
        info = cache.get(d) or {}
        typ = _own_type(info.get("types") or [])
        lvl = LEVELS.get(typ, 6)
        name = info.get("name") or used[d]          # the graph's name; the data's spelling stays searchable
        p = {"dcid": d, "name": name, "type": typ, "alt": [used[d]] if used[d] != name else []}
        anc = _ancestors(d, cache, by_dcid)
        country = next((by_dcid[x] for x in sorted(anc) if x in by_dcid and by_dcid[x]["type"] == "Country"), None)
        path = (country["path"] + [country["name"]]) if country else []
        up = (country["up"] + [country["dcid"]]) if country else []
        mids = []
        for x in anc:
            if x in by_dcid:
                continue
            xi = cache.get(x) or {}
            xl = LEVELS.get(_own_type(xi.get("types") or []), 6)
            if 4 <= xl < lvl and xi.get("name") and xi["name"] != name:
                mids.append((xl, xi["name"], x))
        mids = sorted(set(mids))
        path += [n for _, n, _ in mids]
        up += [x for _, _, x in mids]
        if not country:   # e.g. a territory missing from the gazetteer: hang it under its continent/region
            top = sorted((by_dcid[x]["level"], by_dcid[x]["path"] + [by_dcid[x]["name"]],
                          by_dcid[x]["up"] + [x]) for x in anc if x in by_dcid)
            path, up = (top[-1][1], top[-1][2]) if top else ([], [])
        p["path"], p["up"], p["level"] = path, up, lvl
        extra.append(p)

    places = base + extra
    for p in places:
        p["parent"] = SEP.join(p["path"])
    places.sort(key=lambda p: (p["level"], p["path"], p["name"]))
    doc = {"_about": "Generated by automation/src/build_place_index.py; do not edit.", "places": places}
    text = json.dumps(doc, ensure_ascii=False, indent=0) + "\n"
    old = out_path.read_text(encoding="utf-8") if out_path.exists() else None
    if old != text:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        return True, len(places)
    return False, len(places)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store-dir", default=str(settings.PROJECT_ROOT / "local_store"))
    p.add_argument("--out", default=str(settings.PROJECT_ROOT / "site" / "static" / "places.json"))
    a = p.parse_args(argv)
    changed, n = build(a.store_dir, Path(a.out))
    print(f"place index: {n} places -> {a.out} ({'CHANGED' if changed else 'UNCHANGED'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
