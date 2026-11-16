import re
from urllib.parse import urlparse

WIDTH = 78
ID_WIDTH = 36

TOPICS = {
    "E1": "Download",
    "E2": "No usable rows",
    "E3": "Sheet layout",
    "E4": "Unknown place",
    "E5": "Unreadable date or number",
    "E6": "Value out of range",
    "E7": "Conflicting values",
    "E8": "Date out of range",
    "E9": "Too much data disappeared",
    "C1": "Metadata: dataset name",
    "C2": "Metadata: description",
    "C3": "Metadata: dataset page",
    "C4": "Metadata: licence",
    "C5": "Metadata: source",
    "C6": "Metadata: instance",
    "V10": "Schema",
    "V12": "Schema",
}

FIXES = {
    "network": "Usually temporary. It is retried at the next refresh; the last good version stays on the website.",
    "server": "A problem on the publisher's side. It is retried at the next refresh; the last good version stays "
              "on the website.",
    "not_found": "The address no longer works. Check the link in the sheet (or config) for this dataset.",
    "licence": "Add the publisher's licence URL for these datasets in their sheet. The data is already on the "
               "website; only the Croissant metadata waits for the licence.",
    "E2": "Check that the Data tab has rows with a place, a date and a value.",
    "E3": "Check the column names in the sheet against the template or the dataset's config.",
    "E4": "Fix the place spelling in the sheet, or add an alias in config/places.yaml.",
    "E5": "Use dates like 2020, 2020-05 or 2020-05-31 and plain numbers in the value column.",
    "E6": "Check the values; if they are genuinely right, add an exclude with a reason (never widen the limits).",
    "E7": "The sheet has two different values for the same place, date and variable; keep one.",
    "E8": "Check the dates; for projections set allow_future: true for this dataset.",
    "E9": "If the publisher really removed data, raise max_row_drop_share for this dataset for one run.",
    "C": "Complete the About/metadata fields for this dataset in its sheet.",
    "V": "A configuration problem in the shared schema; see work/validation_summary.json.",
}


def rule(char="-"):
    return char * WIDTH


def banner(title, char="="):
    return f"{rule(char)}\n {title}\n{rule(char)}"


def section(title):
    return f"\n{title}\n{rule()}"


def step(number, total, title):
    return f"\nSTEP {number} of {total}  {title}\n{rule()}"


def num(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def code_of(msg):
    m = re.match(r"(?:config: )?([ECV]\d+)\b", msg or "")
    return m.group(1) if m else ""


def host(url):
    try:
        return urlparse(url).netloc or url
    except ValueError:
        return url


def network_reason(text):
    t = text or ""
    if "name resolution" in t or "Name or service not known" in t or "nodename nor servname" in t:
        return "network", "no internet connection (the web address could not be looked up)"
    if "timed out" in t or "timeout" in t.lower():
        return "network", "the connection timed out"
    if "Connection refused" in t or "Connection reset" in t or "Network is unreachable" in t:
        return "network", "the connection failed"
    m = re.search(r"HTTP Error (\d{3})(?:: ([^>'\"]+))?", t)
    if m:
        status = int(m.group(1))
        label = f"HTTP {status}" + (f" {m.group(2).strip()}" if m.group(2) else "")
        if status == 404 or status == 410:
            return "not_found", f"the file was not found at that address ({label})"
        if status in (401, 403):
            return "not_found", f"access was refused ({label}); the file may not be shared publicly"
        if status >= 500:
            return "server", f"the publisher's server had an error ({label})"
        return "server", f"the server answered {label}"
    return "", ""


def explain(msg):
    code = code_of(msg)
    body = re.sub(r"^(?:config: )?[ECV]\d+\s*", "", msg or "").strip()
    if code == "E1":
        body = re.sub(r"^(fetch failed|could not read the data):\s*", "", body)
        kind, reason = network_reason(body)
        url = re.search(r"https?://\S+", body)
        where = f" from {host(url.group(0))}" if url else ""
        if kind:
            return {"key": f"E1:{kind}:{reason}", "headline": f"Could not download{where}: {reason}",
                    "fix": FIXES[kind]}
        return {"key": "E1read", "headline": f"Could not read the data{where}: {short(body, 160)}",
                "fix": "See logs/pipeline-details.log for the technical error."}
    if code == "C4" and body.startswith("PENDING"):
        return {"key": "C4:pending", "headline": "No licence URL recorded", "fix": FIXES["licence"]}
    topic = TOPICS.get(code, "Configuration" if (msg or "").startswith("config:") else "Problem")
    fix = FIXES.get(code) or FIXES.get(code[:1]) or "See work/validation_summary.json for details."
    return {"key": f"{code}:{body}", "headline": f"{topic}: {short(body, 220)}", "fix": fix}


def short(text, limit=140):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def date_span(stats):
    a, b = stats.get("first_date"), stats.get("last_date")
    if not a:
        return ""
    return a if a == b else f"{a} to {b}"


def outcome(entry):
    if entry.get("status") != "ACCEPTED":
        return "FAILED"
    if entry.get("published_now"):
        return "UPDATED"
    if entry.get("data_changed"):
        return "NEW DATA"
    return "UNCHANGED"


def change_text(entry):
    d = entry.get("changes_vs_published") or {}
    if not d.get("previous_rows"):
        return "first load" if entry.get("published_now") else ""
    parts = []
    if d.get("added"):
        parts.append(f"+{num(d['added'])} new")
    if d.get("changed"):
        parts.append(f"{num(d['changed'])} revised")
    if d.get("removed"):
        parts.append(f"-{num(d['removed'])} removed")
    return ", ".join(parts)


def feed_block(fid, entry, position="", seconds=None):
    state = outcome(entry)
    prefix = f"  [{position}] " if position else "  "
    stats = entry.get("stats") or {}
    facts = []
    if state != "FAILED":
        facts.append(f"{num(stats.get('observations', 0))} values")
        facts.append(f"{num(stats.get('places', 0))} place{'s' if stats.get('places') != 1 else ''}")
        if date_span(stats):
            facts.append(date_span(stats))
        if change_text(entry):
            facts.append(change_text(entry))
    if seconds is not None:
        facts.append(f"{seconds:.0f}s")
    lines = [f"{prefix}{fid:<{ID_WIDTH}} {state:<9}  {' · '.join(facts)}".rstrip()]
    pad = " " * len(prefix)
    if entry.get("dataset"):
        lines.append(f"{pad}{short(entry['dataset'], WIDTH - len(pad))}")
    for mark, text in ([("✗", explain(m)["headline"]) for m in entry.get("errors", [])]
                       + [("!", short(m, 300)) for m in entry.get("warnings", [])
                          if not (code_of(m) == "C4" and "PENDING" in m)]
                       + [("i", short(m, 300)) for m in entry.get("notes", [])]):
        wrapped = _wrap(text, pad + "    ")
        lines.append(f"{pad}  {mark} {wrapped[0].strip()}")
        lines.extend(wrapped[1:])
    if len(lines) > 2:
        lines.append("")
    return "\n".join(lines)


def source_lines(registries):
    out = []
    for rid, r in sorted((registries or {}).items()):
        is_sheet = rid.startswith("sheet:")
        name = rid[6:] if is_sheet else rid
        kind = "data sheet" if is_sheet else "registry sheet"
        status = r.get("status", "")
        if status == "OK":
            size = (f"{num(r.get('data_rows'))} data rows" if is_sheet
                    else f"{num(r.get('datasets', r.get('datasets_loaded')))} datasets listed")
            changed = r.get("changed_since_last_run")
            if not is_sheet:
                c = r.get("changes") or {}
                changed = any(c.values()) if c else None
                if c and changed:
                    changed_txt = ", ".join(f"{v} {k}" for k, v in c.items() if v)
                else:
                    changed_txt = "no changes since last run"
            else:
                changed_txt = "changed since last run" if changed else "no changes since last run"
            out.append(f"  ✓ {name:<{ID_WIDTH - 2}} OK         {kind}, downloaded · {size} · {changed_txt}")
        elif status.startswith("LAST COPY"):
            out.append(f"  - {name:<{ID_WIDTH - 2}} SAVED COPY {kind} · using the copy downloaded earlier · "
                       f"{num(r.get('datasets_loaded'))} dataset(s)")
        else:
            _, reason = network_reason(r.get("error", ""))
            reason = reason or short(r.get("error", "unknown error"), 160)
            if status == "USING LAST COPY":
                saved = f" from {r['last_copy_from']}" if r.get("last_copy_from") else ""
                out.append(f"  ✗ {name:<{ID_WIDTH - 2}} SAVED COPY {kind}: could not download")
                out.append(f"      reason: {reason}")
                out.append(f"      → using the copy saved{saved} · {num(r.get('datasets_loaded'))} dataset(s)")
            else:
                out.append(f"  ✗ {name:<{ID_WIDTH - 2}} FAILED     {kind}: could not download")
                out.append(f"      reason: {reason}")
                out.append("      → no saved copy exists, so its datasets are skipped this run")
    return out or ["  (no sheets configured in config/sheets.json)"]


def _attention(report):
    groups = {}
    for fid, e in sorted(report.get("feeds", {}).items()):
        for m in e.get("errors", []):
            info = explain(m)
            g = groups.setdefault(info["key"], {"headline": info["headline"], "fix": info["fix"], "feeds": [],
                                        "severity": 0})
            g["feeds"].append(fid)
        for m in e.get("warnings", []):
            info = explain(m)
            if info["key"] == "C4:pending":
                g = groups.setdefault("C4:pending", {"headline": "", "fix": info["fix"], "feeds": [],
                                                     "severity": 2})
                g["feeds"].append(fid)
            else:
                g = groups.setdefault(f"W:{fid}:{m}", {"headline": f"Warning: {short(m, 220)}",
                                                       "fix": "Check the values in the sheet; the data was "
                                                              "still loaded.",
                                                       "feeds": [], "severity": 1})
                g["feeds"].append(fid)
    for key, g in groups.items():
        if key.startswith("E1:"):
            kind, reason = key.split(":", 2)[1:]
            g["headline"] = (f"{len(g['feeds'])} dataset(s) could not be downloaded: {reason}"
                             if len(g["feeds"]) > 1 else g["headline"])
        if key == "C4:pending":
            g["headline"] = (f"{len(g['feeds'])} dataset(s) have no licence URL, so no Croissant metadata is "
                             "published for them")
    for p in report.get("config_problems", []):
        groups[f"CFG:{p}"] = {"headline": f"Configuration: {short(p, 220)}",
                              "fix": "Fix the file named in the message.", "feeds": [], "severity": 0}
    for p in report.get("schema_errors", []):
        groups[f"SCH:{p}"] = {"headline": f"Schema: {short(p, 220)}", "fix": FIXES["V"], "feeds": [],
                              "severity": 0}
    return sorted(groups.values(), key=lambda g: (g["severity"], -len(g["feeds"]), g["headline"]))


def _wrap(text, indent, width=WIDTH):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(indent) + len(cur) + 1 + len(w) > width:
            lines.append(indent + cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(indent + cur)
    return lines


def _feed_list(feeds, indent, limit=12):
    shown = ", ".join(feeds[:limit]) + (f", … and {len(feeds) - limit} more" if len(feeds) > limit else "")
    return _wrap(shown, indent)


def summary(report, title="SUMMARY"):
    feeds = report.get("feeds", {})
    counts = {"UPDATED": [], "UNCHANGED": [], "NEW DATA": [], "FAILED": []}
    for fid, e in sorted(feeds.items()):
        counts[outcome(e)].append(fid)
    publish = str(report.get("mode", "")).startswith("publish")
    out = ["", banner(title)]
    out.append(f"  Datasets checked .......... {len(feeds)}")
    if publish:
        out.append(f"    updated on the website .. {len(counts['UPDATED'])}")
        out.append(f"    no change ............... {len(counts['UNCHANGED']) + len(counts['NEW DATA'])}")
    else:
        passed = len(counts["UPDATED"]) + len(counts["UNCHANGED"]) + len(counts["NEW DATA"])
        out.append(f"    passed the checks ....... {passed}   (check mode: nothing was published)")
    out.append(f"    failed .................. {len(counts['FAILED'])}"
               + ("   (not on the website: see the reasons below and RULES.md)" if counts["FAILED"] and publish else ""))
    out.append(f"  Datasets on the website ... {len(report.get('live_imports', []))}")
    if report.get("disabled_feeds"):
        out.append(f"  Switched off (enabled: false) {len(report['disabled_feeds'])}: "
                   + ", ".join(report["disabled_feeds"][:8]))
    if counts["UPDATED"]:
        out.append("")
        out.append("  NEW DATA PUBLISHED")
        for fid in counts["UPDATED"]:
            ch = change_text(feeds[fid])
            out.append(f"    • {fid:<{ID_WIDTH}} {ch}")
    groups = _attention(report)
    out.append("")
    if not groups:
        out.append("  NEEDS YOUR ATTENTION: nothing. Everything went through cleanly.")
    else:
        out.append("  NEEDS YOUR ATTENTION")
        for i, g in enumerate(groups, 1):
            head = f"  {i:>2}. "
            lines = _wrap(g["headline"], " " * len(head))
            out.extend([head + lines[0].strip()] + lines[1:])
            if g["feeds"] and not (len(g["feeds"]) == 1 and g["feeds"][0] in g["headline"]):
                label = "Dataset: " if len(g["feeds"]) == 1 else "Datasets: "
                lines = _feed_list(g["feeds"], " " * (len(head) + len(label)))
                out.append(" " * len(head) + label + lines[0].strip())
                out.extend(lines[1:])
            fix = _wrap("What to do: " + g["fix"], " " * len(head))
            out.extend(fix)
    out.append("")
    out.append("  Full details: work/validation_summary.json (all checks) · work/feed_reports/<dataset>.json")
    out.append("               logs/pipeline-details.log (technical messages, retries, tracebacks)")
    out.append(rule("="))
    return "\n".join(out)


def item(label, status, detail=""):
    mark = {"changed": "✓", "unchanged": "✓", "updated": "✓", "ok": "✓", "skipped": "-"}.get(status.lower(), "✗")
    line = f"  {mark} {label + ' ':.<34} {status}"
    return f"{line}  {detail}".rstrip() if detail else line
