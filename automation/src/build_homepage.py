#!/usr/bin/env python3
"""Write site/config/home_page/sample_questions.json from the datasets that are live, so the datacommons.org
homepage (and the animated hint in the search bar) only suggests questions this instance can answer.

One category per source (up to 6), three questions each: "<variable or dataset> in <a place it has data for>".
Prints CHANGED / UNCHANGED. The website reads the file at start-up (the scheduler restarts it after a load).
"""

import argparse
import csv
import io
import json
import sys
from pathlib import Path

import registry as registry_mod
import registries
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
        if not text:
            continue
        places = {r["entity"][5:] for r in csv.DictReader(io.StringIO(text))}
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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store-dir", default=str(settings.PROJECT_ROOT / "local_store"))
    p.add_argument("--out", default=str(settings.PROJECT_ROOT / "site" / "config" / "home_page" / "sample_questions.json"))
    a = p.parse_args(argv)
    text = json.dumps(questions(a.store_dir), indent=2, ensure_ascii=False) + "\n"
    out = Path(a.out)
    changed = not out.exists() or out.read_text(encoding="utf-8") != text
    if changed:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(f"homepage questions -> {out} ({'CHANGED' if changed else 'UNCHANGED'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
