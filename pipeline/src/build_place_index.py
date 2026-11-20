#!/usr/bin/env python3
import argparse
import json
import logging
import sys
from pathlib import Path

import runlog
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
    from places import PlaceResolver
    api = None
    todo, seen = [d for d in dcids if d not in cache], set()
    for _ in range(6):
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
    known = sorted((LEVELS[t], t) for t in types if t in LEVELS and t != "Place")
    return known[-1][1] if known else "Place"


def build(store_dir, out_path):
    with open(settings.SRC_DIR / "data" / "places_base.json", encoding="utf-8") as f:
        base = json.load(f)["places"]
    by_dcid = {p["dcid"]: p for p in base}
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
        name = info.get("name") or used[d]
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
        if not country:
            top = sorted((by_dcid[x]["level"], by_dcid[x]["path"] + [by_dcid[x]["name"]],
                          by_dcid[x]["up"] + [x]) for x in anc if x in by_dcid)
            path, up = (top[-1][1], top[-1][2]) if top else ([], [])
        p["path"], p["up"], p["level"] = path, up, lvl
        extra.append(p)

    places = base + extra
    for p in places:
        p["parent"] = SEP.join(p["path"])
    places.sort(key=lambda p: (p["level"], p["path"], p["name"]))
    doc = {"_about": "Generated by pipeline/src/build_place_index.py; do not edit.", "places": places}
    text = json.dumps(doc, ensure_ascii=False, indent=0) + "\n"
    old = out_path.read_text(encoding="utf-8") if out_path.exists() else None
    if old != text:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        return True, len(places)
    return False, len(places)


WORLD_TYPES = ["AdministrativeArea1", "AdministrativeArea2", "AdministrativeArea3", "City"]
WORLD_MAX_AGE_DAYS = 30


def _call(body, attempts=3):
    import time
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        settings.DC_API_ROOT.rstrip("/") + "/v2/node", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": settings.DC_API_KEY,
                 "User-Agent": settings.USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            if attempt == attempts:
                raise RuntimeError(str(exc)) from exc
            logging.warning("place list request failed (%s); retry %d in %ds", exc, attempt, 10 * attempt)
            time.sleep(10 * attempt)


def _children(nodes, typ):
    prop = f"<-containedInPlace+{{typeOf:{typ}}}"
    data = _call({"nodes": nodes, "property": prop}).get("data", {})
    return {n: ((v.get("arcs") or {}).get("containedInPlace+") or {}).get("nodes", []) for n, v in data.items()}


def _batched(nodes, typ, size):
    out = {}
    for i in range(0, len(nodes), size):
        out.update(_children(nodes[i:i + size], typ))
    return out


def _parent_labels(rows, country_of):
    from collections import Counter
    seen = Counter((name.lower(), country_of.get(d)) for d, (name, _) in rows.items())
    todo = sorted(d for d, (name, _) in rows.items() if seen[(name.lower(), country_of.get(d))] > 1)
    labels = {}
    for i in range(0, len(todo), 500):
        data = _call({"nodes": todo[i:i + 500], "property": "->containedInPlace"}).get("data", {})
        for d, v in data.items():
            nodes = ((v.get("arcs") or {}).get("containedInPlace") or {}).get("nodes", [])
            own = LEVELS.get(rows[d][1], 6)
            names = {}
            for n in nodes:
                types = n.get("types") or []
                for t in ("AdministrativeArea1", "AdministrativeArea2"):
                    if t in types and LEVELS[t] < own and n.get("name"):
                        names.setdefault(t, n["name"])
            parts = [names[t] for t in ("AdministrativeArea1", "AdministrativeArea2") if t in names]
            if parts:
                labels[d] = parts
    return labels


def build_world(out_path, force=False):
    import time
    from places import PlaceResolver
    if out_path.exists() and not force and time.time() - out_path.stat().st_mtime < WORLD_MAX_AGE_DAYS * 86400:
        return "unchanged", sum(1 for _ in out_path.open(encoding="utf-8"))
    api = PlaceResolver()
    if not api.allow_api:
        return "skipped", "DC_API_KEY is not set"
    with open(settings.SRC_DIR / "data" / "places_base.json", encoding="utf-8") as f:
        countries = {p["dcid"]: p["name"] for p in json.load(f)["places"] if p.get("type") == "Country"}
    try:
        rows, country_of = {}, {}
        for typ in WORLD_TYPES:
            for c, nodes in _batched(sorted(countries), typ, 10).items():
                for n in nodes:
                    d, name = n.get("dcid"), (n.get("name") or "").strip()
                    if not d or not name or d in rows and LEVELS.get(rows[d][1], 6) <= LEVELS.get(typ, 6):
                        continue
                    rows[d] = (name, typ)
                    country_of.setdefault(d, c)
    except RuntimeError as exc:
        logging.warning("world place index not refreshed: %s", exc)
        return "skipped", "Data Commons API not reachable; the last copy is kept"
    try:
        labels = _parent_labels(rows, country_of)
    except RuntimeError as exc:
        logging.warning("province labels for same-named places not added: %s", exc)
        labels = {}
    lines = []
    for d, (name, typ) in rows.items():
        parent = SEP.join([countries.get(country_of.get(d), "")] + labels.get(d, []))
        clean = [x.replace("\t", " ").replace("\n", " ") for x in (d, name, typ, parent)]
        lines.append("\t".join(clean))
    lines.sort()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return "updated", len(lines)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--store-dir", default=str(settings.STORE_DIR))
    p.add_argument("--out", default=str(settings.SITE_DIR / "static" / "places.json"))
    p.add_argument("--world-out", default=str(settings.SITE_DIR / "data" / "world_places.tsv"))
    p.add_argument("--refresh-world", action="store_true", help="rebuild the world place index now")
    a = p.parse_args(argv)
    changed, n = build(a.store_dir, Path(a.out))
    print(runlog.item("Place search index", "changed" if changed else "unchanged", f"{n:,} places"))
    status, info = build_world(Path(a.world_out), a.refresh_world)
    detail = f"{info:,} places in every country" if isinstance(info, int) else info
    if status == "unchanged":
        detail += f" (refreshed every {WORLD_MAX_AGE_DAYS} days)"
    print(runlog.item("World place search", status, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
