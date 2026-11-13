"""Croissant 1.1 readiness: check each feed's metadata and preview the JSON-LD the website will publish.

Rules (knowledge/sources/croissant-dc-integration.md and upstream website server/lib/croissant_metadata.py):
  - Dataset: name; description 50-5000 characters; url (canonical landing page, absolute https/http);
    license = a licence URL (never inferred: license_evidence must say where it is stated)
  - creator = the Source: name + url
  - datePublished = latest lastDataRefreshDate of the dataset's provenances (set by the pipeline)
  - Provenance: provenanceCategory StatisticsProvenance, isPartOf the Dataset, licence
  - contentUrl/downloadUrl only for a real file (instance.public_data_url), never a landing page
A feed failing these rules is not published (rule C1..C6 in the run report).

The preview (work/croissant/<dataset>.json) mirrors what the site builds from the knowledge graph on
/browser/<dataset dcid>, so it can be checked with mlcroissant or the Rich Results Test before the site is up.
"""

import re

CONFORMS_TO = "http://mlcommons.org/croissant/1.1"
LANDING_HINT = re.compile(r"\.(csv|xlsx?|json|zip)(\?|$)", re.I)
LICENSE_HOSTS = ("creativecommons.org", "opendatacommons.org", "spdx.org", "opensource.org")


def check_feed(feed, registry):
    ds, errors, warnings = feed.dataset, [], []
    src = registry.sources.get(feed.source_key) or {}
    name, desc = " ".join(str(ds.get("name", "")).split()), " ".join(str(ds.get("description", "")).split())
    if not name:
        errors.append("C1 dataset.name is required (the official title)")
    if not 50 <= len(desc) <= 5000:
        errors.append(f"C2 dataset.description must be 50-5000 characters (now {len(desc)})")
    url = str(ds.get("url", ""))
    if not re.match(r"^https?://\S+$", url):
        errors.append("C3 dataset.url must be the full URL of the dataset's landing page")
    elif LANDING_HINT.search(url):
        warnings.append("dataset.url looks like a file download; it should be the human-readable landing page")
    lic = str(ds.get("license", ""))
    if not lic and getattr(feed, "license_pending", False):
        warnings.append("C4 PENDING: no licence URL for this dataset (never guessed). The data is loaded, but no "
                        "Croissant JSON-LD is published for it until the publisher's licence URL is recorded.")
    elif not re.match(r"^https?://\S+$", lic):
        errors.append("C4 dataset.license must be the URL of the licence (e.g. "
                      "https://creativecommons.org/licenses/by/4.0/); never guess it")
    elif not ds.get("license_evidence"):
        errors.append("C4 dataset.license_evidence is required: where the publisher states this licence")
    elif not any(h in lic for h in LICENSE_HOSTS):
        warnings.append(f"licence URL {lic} is not a standard (CC/ODC/SPDX) licence: check it is the licence text")
    if not (src.get("name") and src.get("url")):
        errors.append("C5 the source (creator) needs a name and url in config/sources.yaml")
    public = registry.instance.get("public_data_url", "")
    if public and not re.match(r"^https?://\S+$", public):
        errors.append("C6 instance.public_data_url must be a full URL of the published CSV folder")
    return errors, warnings


def preview(registry, feeds, refresh_by_feed):
    """{dataset dcid: JSON-LD} as the site would emit it (base + extended when a public download exists)."""
    out = {}
    inst = registry.instance
    site = inst["site_url"].rstrip("/")
    by_ds = {}
    for f in feeds:
        if f.dataset.get("license"):     # no licence -> no payload (upstream validation drops it too)
            by_ds.setdefault(f.dataset_dcid, []).append(f)
    for dcid, fs in sorted(by_ds.items()):
        f0 = fs[0]
        src = registry.sources[f0.source_key]
        dates = [refresh_by_feed[f.id]["refreshed"] for f in fs if refresh_by_feed.get(f.id)]
        doc = {
            "@context": {"@language": "en", "@vocab": "https://schema.org/", "sc": "https://schema.org/",
                         "cr": "http://mlcommons.org/croissant/", "prov": "http://www.w3.org/ns/prov#",
                         "dct": "http://purl.org/dc/terms/", "conformsTo": "dct:conformsTo",
                         "isLiveDataset": "cr:isLiveDataset", "citeAs": "cr:citeAs"},
            "@type": "Dataset",
            "conformsTo": CONFORMS_TO,
            "name": " ".join(f0.dataset["name"].split()),
            "description": " ".join(f0.dataset["description"].split()),
            "url": f"{site}/browser/{dcid}",
            "sameAs": f0.dataset["url"],
            "license": f0.dataset["license"],
            "isLiveDataset": True,
            "creator": [{"@type": "Organization", "@id": src["dcid"], "name": src["name"], "url": src["url"]}],
            "prov:wasAttributedTo": {"@id": src["dcid"]},
            "publisher": {"@type": "Organization", "name": inst["name"], "url": site},
        }
        if dates:
            doc["datePublished"] = max(dates)
        if f0.dataset.get("citation"):
            doc["citeAs"] = f0.dataset["citation"]
        if inst.get("public_data_url"):
            doc["distribution"] = [{
                "@type": "cr:FileObject", "@id": f.provenance.get("name") or f.id,
                "name": f.provenance.get("name") or f.id,
                "contentUrl": f"{inst['public_data_url'].rstrip('/')}/{f.id}/{f.id}.csv",
                "encodingFormat": "text/csv"} for f in fs]
        out[dcid] = doc
    return out
