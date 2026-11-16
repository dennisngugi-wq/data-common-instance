#!/usr/bin/env python3
import argparse
import csv
import io
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

import croissant
import registry as registry_mod
import registries
import runlog
import settings
from storage import LocalStore

PREFERRED = ["country/KEN", "country/NGA", "country/ETH", "country/EGY", "country/ZWE", "Earth"]


def questions(store_dir):
    store = LocalStore(store_dir)
    reg = registry_mod.load()
    registries.add_stored_feeds(reg, store)
    state = json.loads(store.read_text(settings.STATE_KEY) or "{}")
    names = {**{p: n for p, n in (state.get("places_used") or {}).items()}, "Earth": "the world"}
    by_source = {}
    for fid in sorted(reg.feeds):
        text = store.read_text(f"{settings.DCP_INPUT_ROOT}/{fid}/{fid}.csv")
        if not text or not croissant.publishable(reg.feeds[fid], reg):
            continue
        places ={r["entity"][5:] for r in csv.DictReader(io.StringIO(text))}
        place = next((p for p in PREFERRED if p in places), sorted(places)[0] if places else None)
        if not place:
            continue
        feed = reg.feeds[fid]
        topic = feed.dataset["name"] if feed.dynamic else None
        if not topic:
            keys = feed.variables
            topic = reg.variables[keys[0]].name if keys and keys[0] in reg.variables else feed.dataset["name"]
        src = reg.sources[feed.source_key]["name"]
        place_name = names.get(place) or {"country/KEN": "Kenya", "country/NGA": "Nigeria", "country/ETH": "Ethiopia",
                                          "country/EGY": "Egypt", "country/ZWE": "Zimbabwe"}.get(place, place)
        by_source.setdefault(src, []).append(f"{topic} in {place_name}")
    out = [{"category": src, "questions": qs[:3]} for src, qs in sorted(by_source.items(), key=lambda kv: -len(kv[1]))]
    return out[:6]


TOPICS = [
    ("population", "Population & census", "family_restroom", ["population", "census"]),
    ("labour", "Jobs & income", "work", ["labour", "employ", "income", "wage"]),
    ("prices", "Prices & inflation", "sell", ["price", "inflation", "consumerprice"]),
    ("economy", "GDP & national accounts", "account_balance", ["nationalaccounts", "gdp", "grossdomestic"]),
    ("agriculture", "Agriculture & industry", "agriculture", ["agricultur", "crop", "livestock", "cattle",
                                                              "industrial", "manufactur"]),
    ("health", "Health & vital statistics", "health_and_safety", ["health", "ebola", "birth", "death", "vital"]),
    ("trade-migration", "Trade & migration", "swap_horiz", ["trade", "migra", "export", "import"]),
    ("environment", "Environment & climate", "eco", ["co2", "emission", "climate"]),
]


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "")).lower()
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def topics(catalog_path):
    try:
        catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    texts = [_norm(d.get("name", "") + " " + d.get("description", ""))
             for s in catalog.get("sources", []) for d in s.get("datasets", [])]
    out = []
    for tid, title, icon, terms in TOPICS:
        n = sum(1 for t in texts if any(term in t for term in terms))
        if n:
            url = "/data?topic=" + quote(title) + "&terms=" + quote("|".join(terms))
            out.append({"id": tid, "title": title, "description": f"{n} dataset{'s' if n != 1 else ''}",
                        "url": url, "browseUrl": url, "icon": icon})
    return out


def _write(path, obj):
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    out = Path(path)
    changed = not out.exists() or out.read_text(encoding="utf-8") != text
    if changed:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(runlog.item(f"Homepage {out.stem.replace('_', ' ')}", "changed" if changed else "unchanged"))


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--store-dir", default=str(settings.PROJECT_ROOT / "local_store"))
    home = settings.PROJECT_ROOT / "site" / "config" / "home_page"
    p.add_argument("--out", default=str(home / "sample_questions.json"))
    p.add_argument("--topics-out", default=str(home / "topics.json"))
    p.add_argument("--catalog", default=str(settings.PROJECT_ROOT / "site" / "static" / "catalog.json"))
    a = p.parse_args(argv)
    _write(a.out, questions(a.store_dir))
    _write(a.topics_out, topics(a.catalog))
    return 0


if __name__ == "__main__":
    sys.exit(main())
