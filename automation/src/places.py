import json
import logging
import re
import unicodedata
import urllib.error
import urllib.request

import settings

DCID_LIKE = re.compile(r"^[A-Za-z]+/[A-Za-z0-9_.\-/]+$|^(Earth|africa|asia|europe|northamerica|southamerica|oceania)$")


GENERIC_WORDS = {"province", "provincial", "metropolitan", "metro", "district", "county", "region", "state",
                 "council", "municipality"}


def _core(name):
    words = [w for w in norm(name).split() if w not in GENERIC_WORDS]
    return " ".join(words) or norm(name)


def norm(text):
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class PlaceResolver:
    def __init__(self, aliases=None, cache=None, allow_api=True):
        with open(settings.SRC_DIR / "data" / "places_base.json", encoding="utf-8") as f:
            self.base = json.load(f)["places"]
        self.by_dcid = {p["dcid"]: p for p in self.base}
        self.by_name, self.by_iso3 = {}, {}
        for p in self.base:
            for k in [p["name"]] + p.get("alt", []):
                self.by_name.setdefault(norm(k), p["dcid"])
            if p["dcid"].startswith("country/"):
                self.by_iso3[p["dcid"][8:]] = p["dcid"]
        self.aliases = aliases or {}
        self.cache = cache if cache is not None else {}
        self.allow_api = allow_api and bool(settings.DC_API_KEY)
        self.api_calls = 0
        self.ambiguous = {}
        self.used = {}

    def resolve_all(self, values, spec):
        match = spec.get("match", "auto")
        out, pending, codes = {}, [], []
        for raw in sorted({str(v).strip() for v in values if v is not None and str(v).strip()}):
            dcid = self._local(raw, match)
            if dcid:
                out[raw] = dcid
                continue
            out[raw] = None
            if match in ("iso3", "auto") and re.match(r"^[A-Za-z]{3}$", raw):
                codes.append(raw)
            elif match in ("name", "auto"):
                pending.append(raw)
        if codes:
            for raw, hit in self._api_iso3(codes).items():
                out[raw] = hit
            if match == "auto":
                pending += [c for c in codes if not out[c]]
        if pending and spec.get("type") and spec.get("within"):
            kids = self.children(spec["within"], spec["type"])
            for raw in list(pending):
                hit = kids.get(_core(raw))
                if hit:
                    out[raw] = hit
                    pending.remove(raw)
        if pending:
            hits = self._api(pending, spec.get("type"), spec.get("within"))
            if spec.get("type") and not spec.get("strict_type"):
                hits.update(self._api([p for p in pending if p not in hits], None, spec.get("within")))
            for raw, hit in hits.items():
                out[raw] = hit
        for raw, dcid in out.items():
            if dcid:
                self.used.setdefault(dcid, self.display_name(dcid, raw))
        return out

    def display_name(self, dcid, fallback=""):
        if dcid in self.by_dcid:
            return self.by_dcid[dcid]["name"]
        for hit in self.cache.values():
            if hit.get("dcid") == dcid and hit.get("name"):
                return hit["name"]
        return fallback or dcid

    def _local(self, raw, match):
        key = norm(raw)
        if key in self.aliases:
            return self.aliases[key]
        if raw in self.aliases:
            return self.aliases[raw]
        if match == "dcid":
            raw = raw[5:] if raw.startswith("dcid:") else raw
            return raw if DCID_LIKE.match(raw) or raw in self.by_dcid else None
        if match in ("iso3", "auto"):
            code = raw.upper()
            if code in self.by_iso3:
                return self.by_iso3[code]
            if match == "iso3":
                return None
        if match == "auto" and (DCID_LIKE.match(raw) or raw in self.by_dcid):
            return raw[5:] if raw.startswith("dcid:") else raw
        return self.by_name.get(key)

    def _api(self, names, type_of=None, within=None):
        result, todo = {}, []
        for name in names:
            ck = f"{type_of or ''}|{within or ''}|{norm(name)}"
            if ck in self.cache:
                result[name] = self.cache[ck]["dcid"]
            else:
                todo.append(name)
        if not todo:
            return result
        if not self.allow_api:
            logging.info("%d place names need the Data Commons resolve API, but DC_API_KEY is not set", len(todo))
            return result
        expr = "<-description" + (f"{{typeOf:{type_of}}}" if type_of else "") + "->dcid"
        for i in range(0, len(todo), 50):
            batch = todo[i:i + 50]
            queries = [f"{n}, {self.display_name(within)}" if within else n for n in batch]
            try:
                data = self._post("/v2/resolve", {"nodes": queries, "property": expr})
            except RuntimeError as exc:
                logging.warning("place resolve API failed: %s", exc)
                continue
            by_query = {e.get("node"): [c.get("dcid") for c in e.get("candidates", []) if c.get("dcid")]
                        for e in data.get("entities", [])}
            for name, query in zip(batch, queries):
                cands = by_query.get(query) or []
                if not cands:
                    continue
                if len(cands) > 1:
                    self.ambiguous[name] = cands[:5]
                ck = f"{type_of or ''}|{within or ''}|{norm(name)}"
                self.cache[ck] = {"dcid": cands[0], "name": name}
                result[name] = cands[0]
        return result

    def children(self, parent, type_of):
        ck = f"children|{type_of}|{parent}"
        if ck in self.cache:
            return self.cache[ck]
        if not self.allow_api:
            return {}
        try:
            data = self._post("/v2/node", {"nodes": [parent],
                                           "property": f"<-containedInPlace{{typeOf:{type_of}}}"}).get("data", {})
        except RuntimeError as exc:
            logging.warning("children of %s failed: %s", parent, exc)
            return {}
        nodes = ((data.get(parent) or {}).get("arcs", {}).get("containedInPlace", {}) or {}).get("nodes", [])
        kids = {}
        for n in nodes:
            if n.get("dcid") and n.get("name"):
                kids.setdefault(_core(n["name"]), n["dcid"])
                self.used.setdefault(n["dcid"], n["name"])
        self.cache[ck] = kids
        return kids

    def _api_iso3(self, codes):
        result, todo = {}, []
        for code in codes:
            ck = f"iso3|{code.upper()}"
            if ck in self.cache:
                result[code] = self.cache[ck]["dcid"]
            else:
                todo.append(code)
        if not todo or not self.allow_api:
            return result
        dcids = [f"country/{c.upper()}" for c in todo]
        try:
            data = self._post("/v2/node", {"nodes": dcids, "property": "->name"}).get("data", {})
        except RuntimeError as exc:
            logging.warning("place node API failed: %s", exc)
            return result
        for code, dcid in zip(todo, dcids):
            names = ((data.get(dcid) or {}).get("arcs", {}).get("name", {}) or {}).get("nodes", [])
            if names:
                self.cache[f"iso3|{code.upper()}"] = {"dcid": dcid, "name": names[0].get("value", code)}
                result[code] = dcid
        return result

    def _post(self, path, body):
        self.api_calls += 1
        req = urllib.request.Request(
            settings.DC_API_ROOT.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-API-Key": settings.DC_API_KEY,
                     "User-Agent": settings.USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(str(exc)) from exc
