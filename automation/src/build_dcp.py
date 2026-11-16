import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

CSV_FIELDS = ["variable", "entity", "date", "value", "unit", "period"]
TOPIC_MAX_MEMBERS = 40


def _strip_dcid(s):
    return s[5:] if s.startswith("dcid:") else s


def _fmt(value):
    return str(int(value)) if float(value).is_integer() and abs(value) < 1e15 else repr(round(value, 10))


def _q(text):
    return '"' + " ".join(str(text).split()).replace('"', "'") + '"'


class Writer:
    def __init__(self, registry):
        self.r = registry
        self.ns = registry.ns
        self.sources_of = {}
        self.feeds_of = {}
        for f in registry.feeds.values():
            for k in f.variables:
                self.sources_of.setdefault(k, set()).add(f.source_key)
                self.feeds_of.setdefault(k, []).append(f)

    def _own(self, dcid):
        return dcid.startswith(self.ns)

    def node(self, dcid, *lines):
        return "\n".join([f"Node: {'ddd' if self._own(dcid) else 'dcid'}:{dcid}", *lines]) + "\n"

    @staticmethod
    def ref(dcid):
        return f"dcid:{dcid}"

    @staticmethod
    def ql(prop, text):
        return f"{prop}: {_q(text)}"

    def source_node(self, src):
        lines = ["typeOf: dcid:Source", self.ql("name", src["name"]), self.ql("url", src["url"]),
                 self.ql("domain", re.sub(r"^https?://(www\.)?", "", src["url"]).split("/")[0])]
        lines += [self.ql("alternateName", a) for a in sorted({x for x in [src.get("short")] + src.get("aliases", [])
                                                                if x and x != src["name"]})]
        return self.node(src["dcid"], *lines)

    def dataset_node(self, feed):
        ds, src = feed.dataset, self.r.sources[feed.source_key]
        lines = ["typeOf: dcid:Dataset", self.ql("name", ds["name"]), self.ql("description", ds["description"]),
                 self.ql("url", ds["url"])]
        if ds.get("license"):
            lines.append(self.ql("license", ds["license"]))
        lines.append(f"isPartOf: {self.ref(src['dcid'])}")
        return self.node(feed.dataset_dcid, *lines)

    def schema_mcf(self):
        r = self.r
        used_sources = sorted({f.source_key for f in r.feeds.values()})
        first_source = r.sources[used_sources[0]] if used_sources else None
        blocks = [self.node(r.schema_provenance, "typeOf: dcid:Provenance",
                            self.ql("name", f"{r.instance['name']} schema"),
                            self.ql("description", f"Shared schema nodes of {r.instance['name']}: sources, datasets, "
                                                   "statistical variable groups and units."),
                            *( [f"source: {self.ref(first_source['dcid'])}"] if first_source else []),
                            self.ql("url", r.instance["site_url"]))]
        for key in used_sources:
            if self._own(r.sources[key]["dcid"]):
                blocks.append(self.source_node(r.sources[key]))
        done = set()
        for fid in sorted(r.feeds):
            f = r.feeds[fid]
            if f.dataset_dcid not in done and self._own(f.dataset_dcid):
                blocks.append(self.dataset_node(f))
                done.add(f.dataset_dcid)
        blocks += self.topic_blocks()
        blocks.append(self.node(r.root_group, "typeOf: dcid:StatVarGroup", self.ql("name", r.instance["name"]),
                                "specializationOf: dcid:dc/g/Root"))
        for gdcid, (name, parent) in sorted(r.groups.items()):
            blocks.append(self.node(gdcid, "typeOf: dcid:StatVarGroup", self.ql("name", name),
                                    f"specializationOf: {self.ref(parent)}"))
        for udcid, u in sorted(r.units.items()):
            blocks.append(self.node(udcid, "typeOf: dcid:UnitOfMeasure", self.ql("name", u["name"]),
                                    self.ql("description", u["description"] or u["name"])))
        return "\n".join(blocks)

    def topic_blocks(self):
        r, by_ds = self.r, {}
        for fid in sorted(r.feeds):
            f = r.feeds[fid]
            if self._own(f.dataset_dcid):
                by_ds.setdefault(f.dataset_dcid, (f, []))[1].extend(k for k in f.variables if k in r.variables)
        blocks = []
        for ds_dcid, (f, keys) in sorted(by_ds.items()):
            groups = defaultdict(list)
            for k in dict.fromkeys(keys):
                groups[r.variables[k].unit or ""].append(r.variables[k])
            groups = {u: vs for u, vs in groups.items() if 2 <= len(vs) <= TOPIC_MAX_MEMBERS}
            if not groups:
                continue
            name, suffix = f.dataset["name"], ds_dcid.split("Dataset_", 1)[-1]
            topic = f"{self.ns}Topic_{suffix}"
            peers = []
            for i, (unit, vs) in enumerate(sorted(groups.items()), start=1):
                pdcid = f"{self.ns}PeerGroup_{suffix}_{i}"
                uname = (r.units.get(unit) or {}).get("name") if unit else None
                peers.append(pdcid)
                blocks.append(self.node(pdcid, "typeOf: dcid:StatVarPeerGroup",
                                        self.ql("name", f"{name} ({uname})" if uname and len(groups) > 1 else name),
                                        *[f"member: {self.ref(v.dcid)}" for v in sorted(vs, key=lambda v: v.dcid)]))
            blocks.append(self.node(topic, "typeOf: dcid:Topic", self.ql("name", name),
                                    self.ql("description", f.dataset.get("description") or name),
                                    self.ql("searchDescription", name),
                                    *[f"relevantVariable: {self.ref(p)}" for p in peers]))
        return blocks

    def measured_properties_mcf(self):
        blocks = []
        for key, v in sorted(self.r.variables.items()):
            if v.custom_property:
                blocks.append(self.node(v.measured_property, "typeOf: dcid:Property", self.ql("name", v.name),
                                        "rangeIncludes: dcid:Number",
                                        self.ql("description", f"Measured property of the variable '{v.name}'.")))
        return "\n".join(blocks)

    def statvars_mcf(self):
        blocks = []
        for key, v in sorted(self.r.variables.items()):
            if v.existing:
                continue
            lines = ["typeOf: dcid:StatisticalVariable", self.ql("name", v.name), self.ql("description", v.description),
                     f"populationType: dcid:{v.population_type}",
                     f"measuredProperty: {self.ref(v.measured_property)}", f"statType: dcid:{v.stat_type}"]
            if v.constraints:
                lines.append("constraintProperties: " + ", ".join(f"dcid:{p}" for p in sorted(v.constraints)))
                lines += [f"{p}: dcid:{val}" for p, val in sorted(v.constraints.items())]
            lines.append(f"memberOf: {self.ref(self.group_of(v))}")
            lines += [self.ql("searchDescription", t) for t in self.search_texts(key, v)]
            blocks.append(self.node(v.dcid, *lines))
        return "\n".join(blocks)

    def search_texts(self, key, v):
        texts = [v.name]
        for f in sorted(self.feeds_of.get(key, ()), key=lambda f: f.id):
            title = (f.dataset or {}).get("name")
            cats = [c for c in ((f.meta or {}).get("Domain"), (f.meta or {}).get("Sub-domain"))
                    if c and c.strip() and c.strip().lower() != "uncategorized"]
            texts += [t for t in [title] + cats + [" ".join(cats)] if t]
        for sk in sorted(self.sources_of.get(key, ())):
            src = self.r.sources[sk]
            for n in [src["name"], src.get("short")] + src.get("aliases", []):
                if n:
                    texts.append(f"{v.name} {n}")
        return list(dict.fromkeys(texts))[:25]

    def group_of(self, v):
        from registry import group_dcid
        return group_dcid(self.ns, v.group)

    def schema_files(self):
        r = self.r
        return {
            "schema.mcf": self.schema_mcf(),
            "measured_properties.mcf": self.measured_properties_mcf(),
            "statvars.mcf": self.statvars_mcf(),
            "config.json": json.dumps({"inputFiles": [
                {"pattern": "*.mcf", "provenance": f"ddd:{r.schema_provenance}"}]}, indent=2) + "\n",
        }

    def provenance_description(self, feed):
        return " ".join(str(feed.provenance.get("description") or feed.dataset.get("description", "")).split())

    def provenance_mcf(self, feed, observations, refresh):
        ds, src = feed.dataset, self.r.sources[feed.source_key]
        dates = sorted({o["date"] for o in observations})
        lines = ["typeOf: dcid:Provenance",
                 self.ql("name", feed.provenance.get("name") or feed.id),
                 self.ql("description", self.provenance_description(feed)),
                 f"source: {self.ref(src['dcid'])}",
                 f"isPartOf: {self.ref(feed.dataset_dcid)}",
                 "provenanceCategory: dcid:StatisticsProvenance",
                 self.ql("url", ds["url"]),
                 *([self.ql("license", ds["license"])] if ds.get("license") else []),
                 self.ql("lastDataRefreshDate", refresh["refreshed"]),
                 self.ql("earliestDataImportDate", refresh["first_imported"])]
        if refresh.get("content_url"):
            lines.append(self.ql("sourceDataUrl", refresh["content_url"]))
        public = self.r.instance.get("public_data_url")
        if public:
            lines.append(self.ql("downloadUrl", f"{public.rstrip('/')}/{feed.id}/{feed.id}.csv"))
        if dates:
            lines += [self.ql("earliestObservationDate", dates[0]), self.ql("latestObservationDate", dates[-1])]
        return self.node(feed.provenance_dcid, *lines)

    def observations_csv(self, observations):
        units = {v.dcid: v.unit for v in self.r.variables.values()}
        rows = sorted((self.ref(o["statvar"]), self.ref(o["place"]), o["date"], _fmt(o["value"]),
                       self.ref(units[o["statvar"]]) if units.get(o["statvar"]) else "", o.get("period") or "")
                      for o in observations)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(CSV_FIELDS)
        w.writerows(rows)
        return buf.getvalue()

    def import_config(self, feed):
        prov = f"ddd:{feed.provenance_dcid}"
        return {"inputFiles": [
            {"pattern": f"{feed.id}.mcf", "provenance": prov},
            {"pattern": f"{feed.id}.csv", "provenance": prov, "columnMappings": {
                "dcid:variableMeasured": "variable", "dcid:observationAbout": "entity",
                "dcid:observationDate": "date", "dcid:value": "value", "dcid:unit": "unit",
                "dcid:observationPeriod": "period"}},
        ]}

    def summary(self, feed, observations, stats):
        by_var = defaultdict(list)
        for o in observations:
            by_var[o["statvar"]].append(o)
        per_var = {}
        for key in feed.variables:
            v = self.r.variables[key]
            rows = by_var.get(v.dcid, [])
            dates = sorted({o["date"] for o in rows})
            latest = dates[-1] if dates else None
            per_var[v.dcid] = {"key": key, "name": v.name, "unit": v.unit, "observation_rows": len(rows),
                               "places": len({o["place"] for o in rows}), "first_date": dates[0] if dates else None,
                               "latest_date": latest,
                               "places_in_latest_date": len({o["place"] for o in rows if o["date"] == latest})}
        return {"import": feed.id, "provenance": f"ddd:{feed.provenance_dcid}", "dataset": feed.dataset_dcid,
                "source": self.r.sources[feed.source_key]["name"], "observation_rows": len(observations),
                "places": len({o["place"] for o in observations}), "per_variable": per_var,
                "parse": {k: v for k, v in stats.items() if k not in ("observations",)}}

    def feed_files(self, feed, observations, refresh, stats):
        return {
            f"{feed.id}.csv": self.observations_csv(observations),
            f"{feed.id}.mcf": self.provenance_mcf(feed, observations, refresh),
            "config.json": json.dumps(self.import_config(feed), indent=2) + "\n",
            "validation_summary.json": json.dumps(self.summary(feed, observations, stats), indent=2) + "\n",
        }


def write_folder(folder: Path, files):
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.iterdir():
        if stale.is_file() and stale.name not in files:
            stale.unlink()
    digest = hashlib.sha256()
    for name in sorted(files):
        (folder / name).write_text(files[name], encoding="utf-8", newline="")
        digest.update(name.encode())
        digest.update(files[name].encode("utf-8"))
    return digest.hexdigest()


def csv_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_published_csv(text):
    if not text:
        return {}
    return {(_strip_dcid(r["variable"]), _strip_dcid(r["entity"]), r["date"]): float(r["value"])
            for r in csv.DictReader(io.StringIO(text))}


def foreign_nodes(files, ns):
    return sorted({line.split(":", 2)[2].strip() for t in files.values() for line in t.splitlines()
                   if line.startswith("Node: dcid:")})


def undefined_references(files, ns):
    texts = list(files.values())
    defined = {line.split(":", 2)[2].strip() for t in texts for line in t.splitlines()
               if line.startswith("Node: ddd:")}
    referenced = {m.group(1) for t in texts for m in re.finditer(rf"dcid:({re.escape(ns)}\w*)", t)}
    return sorted(referenced - defined)


def today():
    return date.today().isoformat()
