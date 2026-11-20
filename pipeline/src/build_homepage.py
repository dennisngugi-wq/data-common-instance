#!/usr/bin/env python3
import argparse
import csv
import io
import json
import sys
import urllib.parse
from pathlib import Path

import croissant
import registry as registry_mod
import registries
import runlog
import settings
import topics as topics_mod
from storage import LocalStore

PREFERRED = ["country/KEN", "country/NGA", "country/ETH", "country/EGY", "country/ZWE", "Earth"]

# Curated topic pages: lead text, sub-topics, example questions and chart layouts live in this file so a
# refresh only has to fill in the indicators that are actually loaded (never invented data).
CONTENT_FILE = settings.CONFIG_DIR / "topics_content.json"
EXPLORE_SLUGS = {"economy": "economics"}
TITLES = {**topics_mod.TITLES, "economics": "Economics"}
ICONS = {**topics_mod.ICONS, "economics": "account_balance"}
TOPIC_IMAGES = {
    "economics": "/images/explore_landing/topic-economics.svg",
    "demographics": "/images/explore_landing/topic-demographics.svg",
    "education": "/images/explore_landing/topic-education.svg",
    "health": "/images/explore_landing/topic-health.svg",
    "environment": "/images/explore_landing/topic-sustainability.svg",
    "energy": "/images/explore_landing/topic-sustainability.svg",
}


def _json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


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


def loaded_variables(store_dir):
    store = LocalStore(store_dir)
    reg = registry_mod.load()
    registries.add_stored_feeds(reg, store)
    found = {}
    for fid in sorted(reg.feeds):
        text = store.read_text(f"{settings.DCP_INPUT_ROOT}/{fid}/{fid}.csv")
        if not text or not croissant.publishable(reg.feeds[fid], reg):
            continue
        by_name = {v.dcid: v.name for v in reg.variables.values()}
        for r in csv.DictReader(io.StringIO(text)):
            var, place = r["variable"][5:], r["entity"][5:]
            found.setdefault(var, {"name": by_name.get(var, var), "places": {}})["places"][place] = None
    return found


def tool_examples(variables, limit=6):
    countries = [(v, d) for v, d in sorted(variables.items(), key=lambda kv: kv[1]["name"])
                 if any(p.startswith("country/") for p in d["places"])][:limit]
    timeline, map_examples, vis_timeline, vis_map = [], [], [], []
    for var, d in countries:
        place = next(p for p in PREFERRED if p in d["places"]) if any(p in d["places"] for p in PREFERRED) \
            else next(p for p in d["places"] if p.startswith("country/"))
        enc = urllib.parse.quote
        slug = "".join(c if c.isalnum() else "_" for c in var).lower()
        timeline.append({"id": f"timeline_{slug}", "title": d["name"],
                         "url": f"/tools/timeline#place={enc(place, safe='')}&statsVar={enc(var, safe='')}"})
        map_examples.append({"id": f"map_{slug}", "title": d["name"],
                             "url": f"/tools/map#%26sv%3D{enc(var, safe='')}%26pd%3DEarth%26ept%3DCountry"})
    return {
        "timeline_vis_tool_examples.json": timeline,
        "map_vis_tool_examples.json": map_examples,
        "scatter_vis_tool_examples.json": [],
        "timeline_examples.json": {"timeline": []},
        "map_examples.json": {"map": []},
        "scatter_examples.json": {"scatter": []},
        "visualization_examples.json": {"map": [], "scatter": [], "timeline": []},
        "download_examples.json": [],
    }


def _count_label(n):
    """datacommons.org styles big variable counts as 67.5K; keep small counts exact."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".rstrip("0").rstrip(".")
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "K"
    return str(n)


def _source_spans(names):
    """'A, B, and C' as <span> pieces, matching the datacommons.org explore paragraph."""
    if not names:
        return ""
    parts = []
    for i, name in enumerate(names):
        if len(names) == 1:
            parts.append(f"<span>{name}</span>")
        elif i == len(names) - 1:
            parts.append(f"<span>and {name}</span>")
        else:
            parts.append(f"<span>{name}, </span>")
    return "".join(parts)


def _intro(slug, title, var_count, source_names, empty_message):
    sources = _source_spans(source_names)
    if var_count > 0:
        plural = "" if var_count == 1 else "s"
        return (f"Our {title.lower()} data spans over <strong title=\"{var_count:,}\">{_count_label(var_count)}</strong> "
                f"statistical variable{plural}. We collect our {title.lower()} information from sources such as: {sources}.")
    msg = empty_message or "No licensed datasets are loaded for this topic yet."
    tail = f" The data loaded in this Data Commons comes from sources such as: {sources}." if sources else ""
    return (f"Our {title.lower()} data does not yet include statistical variables from a licensed source. "
            f"{msg}{tail}")


CARDS_PER_TOPIC = 12
SOURCES_IN_INTRO = 8


def _spread(items, limit):
    by_country = {}
    for d in items:
        by_country.setdefault(d.get("country", ""), []).append(d)
    out = []
    while len(out) < limit and any(by_country.values()):
        for country in sorted(by_country):
            if by_country[country] and len(out) < limit:
                out.append(by_country[country].pop(0))
    return out


def catalogue_intro(title, n, countries, source_names):
    lower = title.lower()
    if not n:
        return f"No {lower} datasets are in the catalogue yet."
    where = f" for {len(countries)} countr{'y' if len(countries) == 1 else 'ies'}" if countries else ""
    return (f"Our {lower} catalogue spans <strong title=\"{n:,}\">{_count_label(n)}</strong> dataset"
            f"{'' if n == 1 else 's'}{where}. We collect our {lower} information from sources such as: "
            f"{_source_spans(source_names)}.")


def topics(catalog_path):
    """Topic pages for /explore/<topic>, with only the indicators this instance actually has."""
    catalog = _json(catalog_path, {})
    content = _json(CONTENT_FILE, {})
    default = content.get("default", {})
    variables = _json(settings.SITE_DIR / "static" / "variables.json", {})
    var_names = {d: n for d, n, _ in variables.get("variables", [])}
    source_names = [s.get("name", "") for s in variables.get("sources", [])]

    counts, indicators, var_counts, topic_sources = {}, {}, {}, {}
    topic_cat, topic_src_n = {}, {}
    for s in catalog.get("sources", []):
        for d in s.get("datasets", []):
            for slug in d.get("topics", []):
                if d.get("catalogue"):
                    topic_cat.setdefault(EXPLORE_SLUGS.get(slug, slug), []).append(
                        {"name": d["name"], "url": f"/dataset/{d['dcid']}", "country": d.get("country", ""),
                         "source": s.get("name", "")})
                    sn = topic_src_n.setdefault(EXPLORE_SLUGS.get(slug, slug), {})
                    sn[s.get("name", "")] = sn.get(s.get("name", ""), 0) + 1
                counts[slug] = counts.get(slug, 0) + 1
                var_counts[slug] = var_counts.get(slug, 0) + int(d.get("variables") or 0)
                topic_sources.setdefault(slug, set()).add(s.get("name", ""))
                dcid = d.get("example_variable")
                if dcid in var_names:
                    indicators.setdefault(slug, []).append({
                        "dcid": dcid, "name": var_names[dcid], "source": s.get("name", ""),
                        "dataset": d.get("dcid", ""),
                    })

    # datacommons.org calls the topic "economics"; our catalog calls it "economy". One page, one slug.
    if "economy" in counts:
        counts["economics"] = counts.pop("economy") + counts.get("economics", 0)
        var_counts["economics"] = var_counts.pop("economy", 0) + var_counts.get("economics", 0)
        indicators["economics"] = indicators.pop("economy", []) + indicators.get("economics", [])
        topic_sources["economics"] = topic_sources.pop("economy", set()) | topic_sources.get("economics", set())

    order = ["economics"] + [EXPLORE_SLUGS.get(s, s) for s in topics_mod.ORDER
                             if EXPLORE_SLUGS.get(s, s) != "economics"]
    slugs = [s for s in order if s == "economics" or s in counts]
    slugs += sorted(set(counts) - set(slugs))

    out = []
    for slug in slugs:
        c = content.get(slug, default)
        live = indicators.get(slug, [])
        live_dcids = {i["dcid"] for i in live}
        charts = []
        for chart in c.get("charts", []):
            used = [chart.get("variable")] + str(chart.get("variables", "")).split()
            used = [u for u in used if u]
            if used and all(u in live_dcids for u in used):
                charts.append(chart)
        n = counts.get(slug, 0)
        title = TITLES.get(slug, c.get("title", slug.title()))
        # Visualization-tool chips, datacommons.org style, one per live variable in this topic.
        seen, vis_chips = set(), []
        for i in live:
            if i["dcid"] in seen:
                continue
            seen.add(i["dcid"])
            fragment = urllib.parse.quote_plus(
                f'visType=timeline&place=Earth&placeType=Country&sv={json.dumps({"dcid": i["dcid"]})}')
            vis_chips.append({"label": f"{i['name']} in the world",
                              "url": f"/tools/visualization#{fragment}"})
        src_names = sorted(s for s in topic_sources.get(slug, set()) if s)
        out.append({
            "id": slug, "title": title,
            "description": (f"{n} dataset{'s' if n != 1 else ''}" if n else "No licensed indicators yet"),
            "url": f"/explore/{slug}", "browseUrl": f"/explore/{slug}",
            "icon": ICONS.get(slug, "category"),
            "image": c.get("image", TOPIC_IMAGES.get(slug, "/images/explore_landing/topic-equity.svg")),
            "lead": c.get("lead", default.get("lead", "")),
            "subTopics": c.get("subTopics", []),
            "examples": c.get("examples", []),
            "charts": charts,
            "indicators": live,
            "emptyMessage": c.get("emptyMessage", default.get("emptyMessage", "")),
            # datacommons.org explore-page structure
            "varCount": var_counts.get(slug, 0),
            "introHtml": catalogue_intro(
                title, len(topic_cat.get(slug, [])), {d["country"] for d in topic_cat.get(slug, [])},
                [n for n, _ in sorted(topic_src_n.get(slug, {}).items(), key=lambda kv: -kv[1])][:SOURCES_IN_INTRO])
            if topic_cat.get(slug) else _intro(slug, title, var_counts.get(slug, 0), src_names,
                                               c.get("emptyMessage", default.get("emptyMessage", ""))),
            "questions": [] if topic_cat.get(slug) else c.get("questions", default.get("questions", [])),
            "compare": [] if topic_cat.get(slug) else c.get("compare", default.get("compare", [])),
            "visChips": vis_chips,
            "cards": _spread(topic_cat.get(slug, []), CARDS_PER_TOPIC),
            "catalogueCount": len(topic_cat.get(slug, [])),
        })
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
    p.add_argument("--store-dir", default=str(settings.STORE_DIR))
    home = settings.SITE_DIR / "config" / "home_page"
    p.add_argument("--out", default=str(home / "sample_questions.json"))
    p.add_argument("--topics-out", default=str(home / "topics.json"))
    p.add_argument("--catalog", default=str(settings.SITE_DIR / "static" / "catalog.json"))
    a = p.parse_args(argv)
    templates = settings.SITE_DIR / "templates"
    for name, doc in tool_examples(loaded_variables(a.store_dir)).items():
        _write(templates / name, doc)
    _write(a.out, questions(a.store_dir))
    _write(a.topics_out, topics(a.catalog))
    return 0


if __name__ == "__main__":
    sys.exit(main())
