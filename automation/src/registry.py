import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import settings

FEED_ID = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
KEY = re.compile(r"^[A-Za-z0-9_]{1,64}$")
DCID = re.compile(r"^[A-Za-z0-9_./\-:]+$")
URL = re.compile(r"^https?://[^\s]+$")
FETCH_TYPES = {"csv", "google_sheet", "excel", "json", "html_table", "jsonstat"}


def slug(text):
    words = re.findall(r"[A-Za-z0-9]+", str(text))
    return "".join(w[:1].upper() + w[1:] for w in words) or "Other"


@dataclass
class Variable:
    key: str
    dcid: str
    name: str
    description: str
    group: str
    population_type: str
    measured_property: str
    custom_property: bool
    unit: str | None
    stat_type: str
    constraints: dict
    min: float | None
    max: float | None
    existing: bool
    raw: dict = field(repr=False, default_factory=dict)


@dataclass
class Feed:
    id: str
    path: Path
    enabled: bool
    source_key: str
    dataset: dict
    provenance: dict
    fetch: dict
    mapping: dict
    variables: list
    max_row_drop_share: float
    allow_future: bool
    dataset_dcid: str = ""
    provenance_dcid: str = ""
    dynamic: bool = False
    registry: str = ""
    meta: dict = field(default_factory=dict)


def group_chain(ns, path):
    parts = [p.strip() for p in str(path).split("/") if p.strip()] or ["Other"]
    out, parent = [], f"{ns}Group_Root"
    for i, name in enumerate(parts):
        dcid = f"{ns}Group_{slug('/'.join(parts[:i + 1])) if i == 0 else slug(parts[0]) + '_' + slug('_'.join(parts[1:i + 1]))}"
        out.append((dcid, name, parent))
        parent = dcid
    return out


def group_dcid(ns, path):
    return group_chain(ns, path)[-1][0]


@dataclass
class Registry:
    instance: dict
    ns: str
    sources: dict
    feeds: dict
    variables: dict
    units: dict
    groups: dict
    aliases: dict
    problems: dict
    disabled: list

    def restrict(self, feed_ids):
        feeds = {k: v for k, v in self.feeds.items() if k in feed_ids}
        keys = {k for f in feeds.values() for k in f.variables}
        variables = {k: v for k, v in self.variables.items() if k in keys}
        groups = {}
        for v in variables.values():
            if not v.existing:
                groups.update({d: (n, p) for d, n, p in group_chain(self.ns, v.group)})
        units = {k: u for k, u in self.units.items() if any(v.unit == k for v in variables.values())}
        return Registry(instance=self.instance, ns=self.ns, sources=self.sources, feeds=feeds, variables=variables,
                        units=units, groups=groups, aliases=self.aliases, problems=self.problems,
                        disabled=self.disabled)

    def source_key_for(self, name):
        k = norm_name(name)
        if not k:
            return None
        for key, src in self.sources.items():
            if k in {norm_name(x) for x in [key, src["name"], src["short"]] + src["aliases"]}:
                return key
        return None

    @property
    def root_group(self):
        return f"{self.ns}Group_Root"

    @property
    def schema_import(self):
        return f"{self.ns.lower()}_schema"

    @property
    def schema_provenance(self):
        return f"{self.ns}Prov_Schema"


def _load_yaml(path, problems, owner):
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if data is not None else {}
    except (OSError, yaml.YAMLError) as exc:
        problems.setdefault(owner, []).append(f"cannot read {path.name}: {exc}")
        return None


def norm_name(text):
    from places import norm
    return norm(text).replace(" ", "")


def _text(value):
    return " ".join(str(value).split()) if value is not None else ""


def load(config_dir=None, include_disabled=()):
    config_dir = Path(config_dir or settings.CONFIG_DIR)
    problems = {}
    instance = _load_yaml(config_dir / "instance.yaml", problems, "config") or {}
    ns = str(instance.get("namespace") or "")
    if not KEY.match(ns):
        problems.setdefault("config", []).append("instance.yaml: namespace must be letters, digits or _")
        ns = ns if ns else "DCProject"
    instance.setdefault("name", "Data Commons")
    instance.setdefault("site_url", "http://localhost:8090")
    instance.setdefault("public_data_url", "")
    instance.setdefault("earliest_year", 1800)
    val = instance.setdefault("validation", {}) or {}
    instance["validation"] = val
    val.setdefault("max_row_drop_share", 0.2)
    val.setdefault("large_revision_share", 0.5)

    sources = {}
    for key, s in (_load_yaml(config_dir / "sources.yaml", problems, "config") or {}).items():
        s = s or {}
        msgs = []
        if not KEY.match(str(key)):
            msgs.append(f"sources.yaml: key {key!r} must be letters, digits or _")
        if not s.get("name"):
            msgs.append(f"sources.yaml: {key}: name is required")
        if not URL.match(str(s.get("url", ""))):
            msgs.append(f"sources.yaml: {key}: url must be a full http(s) URL")
        if s.get("dcid") and not DCID.match(str(s["dcid"])):
            msgs.append(f"sources.yaml: {key}: dcid {s['dcid']!r} is not a valid dcid")
        if msgs:
            problems.setdefault("config", []).extend(msgs)
            continue
        aliases_ = [_text(a) for a in (s.get("aliases") or []) if _text(a)]
        sources[str(key)] = {"key": str(key), "dcid": s.get("dcid") or f"{ns}Source_{key}", "name": _text(s["name"]),
                             "url": s["url"], "existing": bool(s.get("dcid")), "aliases": aliases_,
                             "short": _text(s.get("short") or "")}
    seen_names = {}
    for key, src in sources.items():
        for n in [src["name"], src["short"], key] + src["aliases"]:
            k = norm_name(n)
            if k and seen_names.setdefault(k, key) != key:
                problems.setdefault("config", []).append(
                    f"sources.yaml: {n!r} is used by both {seen_names[k]} and {key}: merge them into one source")

    from places import norm
    aliases = {}
    places_cfg = _load_yaml(config_dir / "places.yaml", problems, "config") or {}
    for alias, dcid in (places_cfg.get("aliases") or {}).items():
        aliases[norm(alias)] = str(dcid)

    feeds, variables, units, groups, disabled = {}, {}, {}, {}, []
    var_owner = {}
    import sheets
    custom = sheets.overrides(config_dir, "custom", problems)
    paths = {p.stem: p for p in sorted((config_dir / "feeds").glob("*.yaml"))}
    for fid in sorted(set(custom) - set(paths)):
        if custom[fid]["enabled"]:
            problems.setdefault(fid, []).append(
                f"sheets.json lists {fid!r} as custom, but config/feeds/{fid}.yaml (its column mapping) is missing")
    for fid, path in paths.items():
        data = _load_yaml(path, problems, fid)
        if data is None:
            continue
        if fid in custom:
            data["enabled"] = custom[fid]["enabled"]
            data["fetch"] = sheets.apply_link(data.get("fetch"), custom[fid])
        if not data.get("enabled", False) and fid not in include_disabled:
            disabled.append(fid)
            continue
        msgs = []
        if not FEED_ID.match(fid):
            msgs.append("file name must be lowercase letters, digits and _ (it becomes the import name)")
        src = data.get("source")
        if src not in sources:
            msgs.append(f"source {src!r} is not defined in config/sources.yaml")
        fetch = data.get("fetch") or {}
        if fetch.get("type") not in FETCH_TYPES:
            msgs.append(f"fetch.type must be one of {sorted(FETCH_TYPES)}")
        mapping = data.get("mapping") or {}
        if not isinstance(mapping.get("place"), dict):
            msgs.append("mapping.place is required, e.g. {column: Country, match: name}")
        if not (mapping.get("year_columns") or isinstance(mapping.get("date"), dict)):
            msgs.append("mapping.date is required, e.g. {column: Year} (or year_columns: true)")
        dataset = data.get("dataset") or {}
        for k in ("url", "license", "license_evidence", "citation"):
            dataset[k] = dataset.get(k) or ""
        provenance = data.get("provenance") or {}

        feed_vars, var_msgs = _feed_variables(fid, data, mapping, ns)
        msgs += var_msgs
        for v in feed_vars.values():
            if v.key in variables and _var_signature(variables[v.key]) != _var_signature(v):
                msgs.append(f"variable {v.key!r} is also defined in {var_owner[v.key]} with different settings; "
                            "define it identically or give it another key")
        if msgs:
            problems.setdefault(fid, []).extend(msgs)
            continue
        for v in feed_vars.values():
            variables.setdefault(v.key, v)
            var_owner.setdefault(v.key, path.name)
            if not v.existing:
                groups.update({d: (n, p) for d, n, p in group_chain(ns, v.group)})
            unit = v.raw.get("unit")
            if isinstance(unit, dict):
                units[v.unit] = {"name": _text(unit.get("name")), "description": _text(unit.get("description"))}
        feed = Feed(id=fid, path=path, enabled=True, source_key=src, dataset=dataset, provenance=provenance,
                    fetch=fetch, mapping=mapping, variables=sorted(feed_vars),
                    max_row_drop_share=float(data.get("max_row_drop_share",
                                                      instance["validation"]["max_row_drop_share"])),
                    allow_future=bool(data.get("allow_future", False)))
        feed.dataset_dcid = dataset.get("dcid") or f"{ns}Dataset_{fid}"
        feed.provenance_dcid = f"{ns}Prov_{fid}"
        feeds[fid] = feed

    by_dataset = {}
    for f in feeds.values():
        by_dataset.setdefault(f.dataset_dcid, []).append(f)
    for dcid, fs in by_dataset.items():
        keys = {(_text(f.dataset.get("name")), _text(f.dataset.get("description")), f.dataset.get("license"),
                 f.dataset.get("url"), f.source_key) for f in fs}
        if len(keys) > 1:
            for f in fs:
                problems.setdefault(f.id, []).append(
                    f"dataset {dcid} is described differently in {', '.join(x.path.name for x in fs)}")

    return Registry(instance=instance, ns=ns, sources=sources, feeds=feeds, variables=variables, units=units,
                    groups=groups, aliases=aliases, problems=problems, disabled=disabled)


def _var_signature(v):
    return (v.dcid, v.name, v.description, v.population_type, v.measured_property, v.unit, v.stat_type,
            tuple(sorted(v.constraints.items())), v.min, v.max, v.group)


def _feed_variables(fid, data, mapping, ns):
    msgs, out = [], {}
    defs = data.get("variables") or {}
    used = set()
    values = mapping.get("values") or {}
    for col, target in values.items():
        used.add(target["variable"] if isinstance(target, dict) else target)
    used |= set((mapping.get("variables_map") or {}).values())
    if mapping.get("variable"):
        used.add(mapping["variable"])
    if not used:
        msgs.append("mapping names no variable: use values:, variable_column + variables_map:, or variable:")
    for key in sorted(used):
        if key not in defs:
            msgs.append(f"variable {key!r} is used in mapping but not defined under variables:")
    for key, d in defs.items():
        d = d or {}
        if not KEY.match(str(key)):
            msgs.append(f"variable key {key!r} must be letters, digits or _")
            continue
        if key not in used:
            msgs.append(f"variable {key!r} is defined but the mapping never uses it")
            continue
        existing = bool(d.get("dcid"))
        name, desc = _text(d.get("name")), _text(d.get("description"))
        if not existing and (not name or not desc):
            msgs.append(f"variable {key}: name and description are required (they are shown on the site)")
        unit = d.get("unit")
        unit_dcid = None
        if isinstance(unit, dict):
            if not unit.get("key") or not KEY.match(str(unit["key"])) or not unit.get("name"):
                msgs.append(f"variable {key}: a custom unit needs key (letters/digits/_) and name")
            else:
                unit_dcid = f"{ns}Unit_{unit['key']}"
        elif unit:
            unit_dcid = str(unit)
        mp = str(d.get("measured_property") or "")
        custom_prop = not mp
        constraints = {str(k): str(v) for k, v in (d.get("constraints") or {}).items()}
        try:
            vmin = float(d["min"]) if d.get("min") is not None else None
            vmax = float(d["max"]) if d.get("max") is not None else None
        except (TypeError, ValueError):
            msgs.append(f"variable {key}: min/max must be numbers")
            continue
        out[key] = Variable(
            key=key, dcid=str(d.get("dcid") or f"{ns}_{key}"), name=name or str(d.get("dcid")), description=desc,
            group=_text(d.get("group") or "Other"), population_type=str(d.get("population_type") or "Thing"),
            measured_property=mp or f"{ns}_{key}_Value", custom_property=custom_prop and not existing,
            unit=unit_dcid, stat_type=str(d.get("stat_type") or "measuredValue"), constraints=constraints,
            min=vmin, max=vmax, existing=existing, raw=d)
    return out, msgs
