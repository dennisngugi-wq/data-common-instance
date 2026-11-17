import json
import logging
import re
import time
import urllib.error
import urllib.request

import settings
from places import norm

KEEP_OWN = {"new", "none", "no", "own", "-"}
DCID = re.compile(r"^[A-Za-z0-9_./\-]+$")
BATCH = 40


def _post(path, body):
    req = urllib.request.Request(
        settings.DC_API_ROOT.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": settings.DC_API_KEY,
                 "User-Agent": settings.USER_AGENT})
    for attempt in range(1, settings.HTTP_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == settings.HTTP_RETRIES or getattr(exc, "code", 500) in (400, 401, 403, 404):
                raise
            time.sleep(5 * attempt)


def same_name(a, b):
    return bool(norm(a)) and norm(a) == norm(b)


def by_name(labels, post=_post):
    out = {}
    for i in range(0, len(labels), BATCH):
        chunk = labels[i:i + BATCH]
        doc = post("/v2/resolve", {"nodes": chunk, "resolver": "indicator"})
        for ent in doc.get("entities", []):
            hit = next((c for c in ent.get("candidates", [])
                        if "StatisticalVariable" in (c.get("typeOf") or []) and same_name(c.get("name"), ent["node"])),
                       None)
            out[ent["node"]] = {"dcid": hit["dcid"], "name": hit["name"], "how": "same name"} if hit else {}
        for label in chunk:
            out.setdefault(label, {})
    return out


def by_dcid(dcids, post=_post):
    out = {}
    for i in range(0, len(dcids), BATCH):
        chunk = dcids[i:i + BATCH]
        doc = post("/v2/node", {"nodes": chunk, "property": "->[typeOf, name]"})
        for dcid in chunk:
            arcs = (doc.get("data", {}).get(dcid) or {}).get("arcs", {})
            types = {n.get("dcid") for n in arcs.get("typeOf", {}).get("nodes", [])}
            names = [n.get("value") for n in arcs.get("name", {}).get("nodes", []) if n.get("value")]
            out[dcid] = {"dcid": dcid, "name": names[0] if names else dcid, "how": "given in the sheet"} \
                if "StatisticalVariable" in types else {"error": "not a statistical variable in Data Commons"}
    return out


def decide(labels, asked, known, post=_post):
    out, problems = {}, []
    names, dcids = [], {}
    for label in labels:
        want = " ".join(str(asked.get(label, "")).split())
        prev = known.get(label) or {}
        if want.lower() in KEEP_OWN:
            out[label] = {"asked": want}
        elif want:
            if prev.get("asked") == want and prev.get("dcid"):
                out[label] = prev
            elif not DCID.match(want):
                problems.append(f"Variables: {want!r} (Data Commons variable of {label!r}) is not a valid id")
            else:
                dcids[label] = want
        elif "dcid" in prev or prev.get("checked"):
            out[label] = {k: v for k, v in prev.items() if k != "asked"}
        else:
            names.append(label)
    if not settings.DC_API_KEY and post is _post:
        problems += [f"Variables: {want!r} (Data Commons variable of {label!r}) could not be checked: DC_API_KEY "
                     "is not set" for label, want in dcids.items()]
        return out, problems
    try:
        if dcids:
            found = by_dcid(sorted(set(dcids.values())), post)
            for label, want in dcids.items():
                hit = found.get(want) or {}
                if hit.get("dcid"):
                    out[label] = {**hit, "asked": want}
                else:
                    problems.append(f"Variables: {want!r} (Data Commons variable of {label!r}) is "
                                    f"{hit.get('error', 'unknown to Data Commons')}")
        if names:
            for label, hit in by_name(names, post).items():
                out[label] = {**hit, "checked": True}
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        logging.warning("Data Commons variable lookup failed: %s", exc)
        problems.append(f"variables could not be matched with Data Commons this time ({exc}); "
                        "retried at the next refresh")
    seen = {}
    for label in labels:
        dcid = (out.get(label) or {}).get("dcid")
        if dcid and dcid in seen:
            problems.append(f"Variables: {seen[dcid]!r} and {label!r} are both the Data Commons variable {dcid}; "
                            "write 'new' in the column Data Commons variable for the one that is different")
        elif dcid:
            seen[dcid] = label
    return out, problems
