#!/usr/bin/env python3
import argparse
import csv
import io
import json
import sys
from pathlib import Path

import catalog
import croissant
import registry as registry_mod
import runlog
import settings

CSV_FIELDS = ["entity", "variable", "date", "value", "unit", "observationPeriod"]
LOADER_WRITES = ("typeOf", "name", "source", "url")


def _bare(v):
    return v[5:] if v.startswith("dcid:") else v


def _blocks(text):
    return [b.strip() for b in text.strip().split("\n\n") if b.strip()]


def local_schema(texts):
    out = []
    for text in texts:
        for block in _blocks(text):
            if "typeOf: dcid:Provenance" in block:
                continue
            out.append(block.replace("Node: ddd:", "Node: dcid:", 1))
    return "\n\n".join(out) + "\n"


def local_provenance(mcf_text, local_id):
    lines = [f"Node: dcid:{local_id}"]
    for line in mcf_text.strip().splitlines()[1:]:
        prop = line.split(":", 1)[0].strip()
        if prop not in LOADER_WRITES:
            lines.append(line)
    return "\n".join(lines) + "\n"


def local_csv(text):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_FIELDS)
    for r in csv.DictReader(io.StringIO(text)):
        w.writerow([_bare(r["entity"]), _bare(r["variable"]), r["date"], r["value"], _bare(r.get("unit") or ""),
                    r.get("period") or ""])
    return buf.getvalue()


def provenance_ids(reg, feed_ids):
    sources, order = {}, []
    for fid in sorted(feed_ids):
        f = reg.feeds[fid]
        src = reg.sources[f.source_key]
        sources.setdefault(src["name"], {"url": src["url"], "provenances": {}})
        sources[src["name"]]["provenances"][f.provenance.get("name") or fid] = f.dataset["url"]
    for sname, s in sources.items():
        for pname in s["provenances"]:
            order.append(pname)
    ids = {pname: f"c/p/{i}" for i, pname in enumerate(order, start=1)}
    return sources, ids


def build(store_dir, out_dir, reg=None):
    if reg is None:
        import registries
        from storage import LocalStore
        reg = registries.add_stored_feeds(registry_mod.load(), LocalStore(store_dir))
    from storage import LocalStore
    root = Path(store_dir) / settings.DCP_INPUT_ROOT
    schema_files = sorted((root / reg.schema_import).glob("*.mcf"))
    live = [fid for fid in sorted(reg.feeds) if (root / fid / f"{fid}.csv").exists()
            and croissant.publishable(reg.feeds[fid], reg)]
    catalogue = catalog.mcf(catalog.stored(LocalStore(store_dir)), reg)
    if live and not schema_files:
        raise SystemExit(f"No published schema in {root / reg.schema_import}: run the pipeline first")
    if not live and not catalogue:
        raise SystemExit(f"No published feed imports and no catalogue datasets in {root}")
    names = {fid: reg.feeds[fid].provenance.get("name") or fid for fid in live}
    if len(set(names.values())) != len(names):
        raise SystemExit(f"Two feeds share a provenance name: {names}")
    sources, ids = provenance_ids(reg, live)
    cat_records = catalog.stored(LocalStore(store_dir))
    pending = catalog.add_provenances(cat_records, reg, sources)
    ids = {p: f"c/p/{i}" for i, p in enumerate((p for s in sources.values() for p in s["provenances"]), start=1)}
    cat_prov = catalog.provenance_blocks(pending, reg, ids)

    schema = local_schema(p.read_text(encoding="utf-8") for p in schema_files) if schema_files else ""
    files = {"schema.mcf": schema + ("\n" if schema and catalogue else "") + catalogue}
    prov_blocks, input_files = [], {}
    for fid in live:
        files[f"{fid}.csv"] = local_csv((root / fid / f"{fid}.csv").read_text(encoding="utf-8"))
        mcf = root / fid / f"{fid}.mcf"
        if mcf.exists():
            prov_blocks.append(local_provenance(mcf.read_text(encoding="utf-8"), ids[names[fid]]))
        input_files[f"{fid}.csv"] = {"format": "variablePerRow", "provenance": names[fid],
                                     "columnMappings": {c: c for c in CSV_FIELDS}}
    files["provenance.mcf"] = "\n".join(prov_blocks + cat_prov)
    files["config.json"] = json.dumps({"inputFiles": input_files, "sources": sources}, indent=2) + "\n"

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    changed = []
    for stale in sorted(list(out.glob("*.csv")) + list(out.glob("*.mcf"))):
        if stale.name not in files:
            stale.unlink()
            changed.append(f"removed {stale.name}")
    for name, text in sorted(files.items()):
        path = out / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8", newline="")
            changed.append(name)
    rows = sum(max(t.count("\n") - 1, 0) for n, t in files.items() if n.endswith(".csv"))
    return changed, rows, live


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--store-dir", default=str(settings.STORE_DIR))
    p.add_argument("--out-dir", default=str(settings.LOCAL_DC_DIR))
    args = p.parse_args(argv)
    changed, rows, live = build(args.store_dir, args.out_dir)
    status = "changed" if changed else "unchanged"
    work = settings.WORK_DIR
    work.mkdir(parents=True, exist_ok=True)
    (work / "local_dc_status.txt").write_text(status.upper() + "\n")
    print(runlog.item("Database input files", status,
                      f"{rows:,} values from {len(live)} dataset(s)"
                      + (f" · {len(changed)} file(s) changed" if changed else "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
