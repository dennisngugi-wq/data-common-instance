# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# DataCommon Project customisation of the stock Data Commons website (mounted by docker-compose.yml):
#   1. instance name and namespace from config/instance.yaml;
#   2. place search from the site's own index when there is no Google Maps key;
#   3. questions without a place default to the World;
#   4. Croissant 1.1 JSON-LD on /browser/<dataset dcid> and /croissant/<dataset dcid> (backport of upstream
#      server/lib/croissant_metadata.py, which the :stable image predates);
#   5. search shows a variable only when it really matches the question (SEARCH_MIN_SCORE, default 0.7);
#   6. local place names the recogniser misses (DRC, Nord-Kivu, Ituri...);
#   7. parent places listed top-down (World › Africa › Kenya).

import importlib.abc
import importlib.util
import json
import logging
import os
import re
import sys
import time
import unicodedata

from server.app_env import _base
from server.app_env import local

# ---------------------------------------------------------------- instance settings
INSTANCE_FILE = '/workspace/instance.yaml'


def _instance():
  try:
    import yaml
    with open(INSTANCE_FILE, encoding='utf-8') as f:
      return yaml.safe_load(f) or {}
  except Exception:
    logging.exception('custom.py: %s not readable; using defaults', INSTANCE_FILE)
    return {}


_INST = _instance()
INSTANCE_NAME = str(_INST.get('name') or 'Custom Data Commons')
NAMESPACE = str(_INST.get('namespace') or 'DCProject')
SITE_URL = (os.environ.get('SITE_URL') or str(_INST.get('site_url') or 'http://localhost:8090')).rstrip('/')

# Place search without Google Maps (site/static/places.json, built by automation/src/build_place_index.py)
_MAPS_KEY = os.environ.get('MAPS_API_KEY', '').strip()
LOCAL_PLACE_SEARCH = (os.environ.get('LOCAL_PLACE_SEARCH', '').lower() == 'true' or
                      _MAPS_KEY in ('', 'not-configured', 'your_key_here'))
PLACE_INDEX = '/workspace/server/dist/custom_dc/custom/places.json'
PLACE_ID_PREFIX = 'dcid:'


class Config(_base.Config):
  CUSTOM = True
  NAME = INSTANCE_NAME  # changed: was "Custom Data Commons"
  OVERRIDE_CSS_PATH = '/custom_dc/custom/overrides.css'
  LOGO_PATH = "/custom_dc/custom/logo.svg"
  MIN_STAT_VAR_GEO_COVERAGE = 1
  SHOW_DISASTER = False
  USE_LLM = False
  USE_MEMCACHE = False
  LOCAL_PLACE_SEARCH = LOCAL_PLACE_SEARCH  # templates/base.html loads place_search.js when True
  # This instance's variable group first in the Statistical Variable Explorer.
  STAT_VAR_HIERARCHY_CONFIG = {
      "nodes": [{
          "dcid": "dc/g/Root"
      }],
      "topLevelPinnedOrder": {
          f"{NAMESPACE}Group_Root": 0,
          "dc/g/Agriculture": 1, "dc/g/Demographics": 2, "dc/g/Economy": 3, "dc/g/Education": 4,
          "dc/g/Energy": 5, "dc/g/Environment": 6, "dc/g/Health": 7, "dc/g/Housing": 8, "dc/g/Crime": 9,
          "dc/g/AboutDataCommons": 10, "dc/g/Uncategorized": 11, "dc/g/UN": 12, "dc/g/SDG": 13,
      },
  }


# ---------------------------------------------------------------- default place = World
def _patch_default_place(helpers):
  from server.lib.nl.common import constants as nl_constants
  stock = helpers._get_default_place

  def _get_default_place(request, is_special_dc, debug_logs):
    if request.args.get('default_place'):
      return stock(request, is_special_dc, debug_logs)
    return nl_constants.EARTH

  helpers._get_default_place = _get_default_place
  logging.info('custom.py: search default place set to Earth')


# ---------------------------------------------------------------- local place search
_places = None


def _norm(s):
  s = unicodedata.normalize('NFD', str(s or ''))
  s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
  return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()


def _place_index():
  global _places
  if _places is None:
    try:
      with open(PLACE_INDEX, encoding='utf-8') as f:
        _places = json.load(f)['places']
      for p in _places:
        p['_keys'] = [_norm(k) for k in [p['name']] + p.get('alt', [])]
    except Exception:
      logging.exception('custom.py: place index %s not readable', PLACE_INDEX)
      _places = []
  return _places


def search_places(query, limit=5):
  """Same matching as place_search.js: exact, name prefix, word prefix, then substring (lower is better)."""
  q = _norm(query)
  if not q:
    return []
  scored = []
  for p in _place_index():
    best = 99
    for i, k in enumerate(p['_keys']):
      s = 99
      if k == q:
        s = 0
      elif k.startswith(q):
        s = 1
      elif (' ' + k).find(' ' + q) >= 0:
        s = 2
      elif len(q) >= 3 and q in k:
        s = 3
      best = min(best, s + (0.5 if i else 0))
    if best < 99:
      scored.append((best, p.get('level', 6), len(p['name']), p))
  # equally good matches: the higher place first (World, continents, regions, countries, provinces, districts)
  scored.sort(key=lambda x: (x[0], x[1], x[2]))
  return [x[-1] for x in scored[:limit]]


def _patch_place_api(place):
  """/api/place/placeid2dcid: ids from place_search.js are "dcid:<dcid>"; return the dcid itself."""
  from flask import Response
  stock = place.findplacedcid

  def findplacedcid(place_ids):
    ours = {i: i[len(PLACE_ID_PREFIX):] for i in place_ids if i.startswith(PLACE_ID_PREFIX)}
    rest = [i for i in place_ids if not i.startswith(PLACE_ID_PREFIX)]
    if rest:
      ours.update(json.loads(stock(rest).get_data()))
    return Response(json.dumps(ours), 200, mimetype='application/json')

  place.findplacedcid = findplacedcid
  logging.info('custom.py: placeid2dcid accepts dcid: place ids')


def _patch_autocomplete(helpers):
  """Search box place suggestions (/api/autocomplete) from the local index instead of Google Maps."""
  from server.routes.shared_api.autocomplete.types import ScoredPrediction
  place = sys.modules.get('server.routes.shared_api.place')
  if place is not None:
    helpers.findplacedcid = place.findplacedcid

  def get_place_predictions(queries, lang, source):
    best = {}
    for q in queries:
      if _norm(q) in _STOP:   # "ebola cases in": the word "in" is not the start of India / Indonesia
        continue
      for rank, p in enumerate(search_places(q)):
        score = rank - len(q) * 0.01
        if p['dcid'] not in best or score < best[p['dcid']][0]:
          best[p['dcid']] = (score, q, p)
    return [
        # Only the name: "Kenya, Eastern Africa, Africa" would make the search see several places.
        ScoredPrediction(description=p['name'],
                         place_id=PLACE_ID_PREFIX + dcid, place_dcid=dcid, matched_query=q, score=score,
                         source=source) for dcid, (score, q, p) in best.items()
    ]

  helpers.get_place_predictions = get_place_predictions
  logging.info('custom.py: autocomplete places served from the local place index')


_GOOGLE_MAPS_SCRIPT = re.compile(
    rb'<script[^>]*src="https://maps\.googleapis\.com/maps/api/js[^"]*"[^>]*>\s*</script>')


# ---------------------------------------------------------------- indicator suggestions
# datacommons.org suggests indicators with Vertex AI Search, which a local instance does not have. Here they come from
# static/variables.json (automation/src/build_site_catalog.py), matched on the variable name and on every name/alias
# of its source ("KNBS ..." and "Kenya Bureau of Statistics ..." both work).
VARIABLES_FILE = '/workspace/server/dist/custom_dc/custom/variables.json'
_STOP = {'of', 'in', 'the', 'for', 'and', 'a', 'an', 'to', 'by', 'on', 'what', 'is', 'are', 'how', 'many', 'much',
         'show', 'me', 'tell', 'about', 'which', 'with', 'highest', 'lowest', 'most', 'total', 'data', 'vs',
         'compare', 'trend', 'trends', 'over', 'time', 'statistics', 'stats',
         'today', 'now', 'currently', 'current', 'latest', 'recent', 'recently', 'yesterday', 'this', 'week',
         'month', 'year'}
_indicators = None
_indicators_mtime = 0


def _indicator_index():
  global _indicators, _indicators_mtime
  try:
    mtime = os.path.getmtime(VARIABLES_FILE)
  except OSError:
    return []
  if _indicators is None or mtime != _indicators_mtime:
    try:
      with open(VARIABLES_FILE, encoding='utf-8') as f:
        doc = json.load(f)
      srcs = [set(_norm(s['keywords']).split()) for s in doc['sources']]
      _indicators = [{'dcid': d, 'name': n, 'name_words': _norm(n).split(), 'src_words': srcs[i]}
                     for d, n, i in doc['variables']]
      _indicators_mtime = mtime
    except Exception:
      logging.exception('custom.py: %s not readable', VARIABLES_FILE)
      _indicators = []
  return _indicators


def search_indicators(query, limit=6):
  """Every query word (stop words dropped; the last may be half-typed) must start a word of the variable's name or
  of its source's names/aliases. Name matches rank first, then shorter names."""
  words = [w for w in _norm(query).split() if w not in _STOP]
  if not words:
    return []
  scored = []
  for ind in _indicator_index():
    score = 0
    for n, w in enumerate(words):
      last = n == len(words) - 1
      ok = lambda x: x == w or ((last or len(w) >= 3) and x.startswith(w))  # noqa: E731
      if any(ok(x) for x in ind['name_words']):
        continue
      if any(ok(x) for x in ind['src_words']):
        score += 1
        continue
      break
    else:
      scored.append((score, len(ind['name']), ind))
  scored.sort(key=lambda x: (x[0], x[1]))
  return [ind for _, _, ind in scored[:limit]]


def _split_place(query):
  """Longest place name or alias at the end of the query: (indicator part, place name) or (query, None)."""
  q = _norm(query)
  best = None
  for p in _place_index():
    for k in p['_keys']:
      if len(k) >= 3 and (q == k or q.endswith(' ' + k)) and (best is None or len(k) > len(best[0])):
        best = (k, p['name'])
  if not best:
    return query, None
  words = query.split()
  rest = ' '.join(words[:max(0, len(words) - len(best[0].split()))])
  return re.sub(r'\s+(in|of|for)\s*$', '', rest, flags=re.I).strip(), best[1]


def _patch_stat_var_autocomplete(stat_vars):
  from server.routes.shared_api.autocomplete.types import ScoredPrediction

  def analyze_query_concepts(query):
    phrase, place = _split_place(query)
    phrase = phrase.strip()
    if not phrase or not search_indicators(phrase):
      return None
    return {'cleaned_query': phrase, 'original_phrase': phrase, 'has_place': place is not None,
            'place_name': place}

  def search_stat_vars(search_query):
    # Suggest only for the whole phrase typed (without the place): answering the stock per-word lookups made a pick
    # replace part of the question ("new ebola" -> "new Confirmed Ebola cases").
    import flask
    full, _ = _split_place(flask.request.args.get('query', '') or '')
    want = [w for w in _norm(full).split() if w not in _STOP]
    got = [w for w in _norm(search_query).split() if w not in _STOP]
    if want and got != want:
      return []
    typed = _norm(full)
    return [ScoredPrediction(description=ind['name'], place_id=None, place_dcid=ind['dcid'], matched_query=None,
                             score=rank, source=None)
            for rank, ind in enumerate(i for i in search_indicators(search_query) if _norm(i['name']) != typed)]

  stat_vars.analyze_query_concepts = analyze_query_concepts
  stat_vars.search_stat_vars = search_stat_vars
  logging.info('custom.py: indicator suggestions served from %d local variables', len(_indicator_index()))


# ---------------------------------------------------------------- pages and crawler files
CATALOG_FILE = '/workspace/server/dist/custom_dc/custom/catalog.json'
EXTRA_FILE = '/workspace/server/dist/custom_dc/custom/croissant_extra.json'


def _json_file(path, default):
  try:
    with open(path, encoding='utf-8') as f:
      return json.load(f)
  except Exception:
    return default


def _add_pages(static):
  """/drilldown (templates/drilldown.html): a variable from the top place down through its sub-divisions.
  /sitemap.xml and /robots.txt: let search engines find every dataset page (with its Croissant JSON-LD).
  /data (the stock route) renders templates/data.html: every source and dataset of this instance."""
  from flask import Response
  from server.lib.render import render_page

  def drilldown():
    return render_page('static/homepage.html', 'drilldown.html')

  def sitemap():
    urls = [f'{SITE_URL}/', f'{SITE_URL}/data', f'{SITE_URL}/drilldown']
    for src in _json_file(CATALOG_FILE, {}).get('sources', []):
      for ds in src.get('datasets', []):
        if ds.get('license'):          # only datasets with a Croissant payload are worth indexing as datasets
          urls.append(f"{SITE_URL}/browser/{ds['dcid']}")
    body = ''.join(f'<url><loc>{u}</loc></url>' for u in urls)
    return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/'
                    f'sitemap/0.9">{body}</urlset>', 200, mimetype='application/xml')

  def robots():
    return Response(f'User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n', 200, mimetype='text/plain')

  static.bp.add_url_rule('/drilldown', 'dcp_drilldown', drilldown)
  static.bp.add_url_rule('/sitemap.xml', 'dcp_sitemap', sitemap)
  static.bp.add_url_rule('/robots.txt', 'dcp_robots', robots)
  _add_croissant_route(static)
  logging.info('custom.py: /drilldown, /sitemap.xml, /robots.txt added')


# ---------------------------------------------------------------- Croissant 1.1 JSON-LD (backport)
CROISSANT_CONTEXT = {
    "@language": "en", "@vocab": "https://schema.org/", "sc": "https://schema.org/",
    "cr": "http://mlcommons.org/croissant/", "rai": "http://mlcommons.org/croissant/RAI/",
    "prov": "http://www.w3.org/ns/prov#", "dct": "http://purl.org/dc/terms/", "citeAs": "cr:citeAs",
    "column": "cr:column", "conformsTo": "dct:conformsTo", "data": {"@id": "cr:data", "@type": "@json"},
    "dataType": {"@id": "cr:dataType", "@type": "@vocab"}, "examples": {"@id": "cr:examples", "@type": "@json"},
    "extract": "cr:extract", "field": "cr:field", "fileProperty": "cr:fileProperty", "fileObject": "cr:fileObject",
    "fileSet": "cr:fileSet", "format": "cr:format", "includes": "cr:includes", "isLiveDataset": "cr:isLiveDataset",
    "jsonPath": "cr:jsonPath", "key": "cr:key", "md5": "cr:md5", "parentField": "cr:parentField", "path": "cr:path",
    "recordSet": "cr:recordSet", "references": "cr:references", "regex": "cr:regex", "repeated": "cr:repeated",
    "replace": "cr:replace", "separator": "cr:separator", "source": "cr:source", "subField": "cr:subField",
    "transform": "cr:transform"
}
_RECORD_COLUMNS = ["variableMeasured", "observationAbout", "unit", "measurementMethod", "observationPeriod",
                   "provenance", "observationDate", "value", "scalingFactor"]
_croissant_cache = {}
_CACHE_SECONDS = 600


def _first(props, name):
  vals = props.get(name) or []
  return vals[0] if vals else None


def build_croissant(dcid):
  """Croissant JSON-LD for a Dataset node, or {} (not a Dataset, or required fields missing). Same steps and
  validation as upstream build_dataset_metadata; url/publisher point to this instance; adds sameAs (the
  dataset's own landing page) and dateCreated (earliestDataImportDate), both recommended in the design doc."""
  import server.lib.fetch as fetch
  import server.services.datacommons as dc

  hit = _croissant_cache.get(dcid)
  if hit and time.time() - hit[0] < _CACHE_SECONDS:
    return hit[1]
  doc = {}
  try:
    node = fetch.multiple_property_values([dcid], ['typeOf', 'name', 'description', 'license', 'url'])
    props = node.get(dcid, {})
    if 'Dataset' in (props.get('typeOf') or []):
      doc = _croissant_for_dataset(dcid, props, fetch, dc)
  except Exception:
    logging.exception('custom.py: Croissant metadata for %s failed', dcid)
    doc = {}
  _croissant_cache[dcid] = (time.time(), doc)
  return doc


def _croissant_for_dataset(dcid, props, fetch, dc):
  doc = {
      "@context": CROISSANT_CONTEXT, "@type": "Dataset", "conformsTo": "http://mlcommons.org/croissant/1.1",
      "url": f"{SITE_URL}/browser/{dcid}", "isLiveDataset": True,
      "publisher": {"@type": "Organization", "name": INSTANCE_NAME, "url": SITE_URL},
  }
  if _first(props, 'name'):
    doc["name"] = _first(props, 'name')
  if _first(props, 'description'):
    doc["description"] = _first(props, 'description')
  if _first(props, 'license'):
    doc["license"] = _first(props, 'license')
  if _first(props, 'url'):
    doc["sameAs"] = _first(props, 'url')

  # creator: the Source the Dataset isPartOf
  resp = dc.v2node([dcid], '->isPartOf')
  sources = ((((resp.get('data') or {}).get(dcid) or {}).get('arcs') or {}).get('isPartOf') or {}).get('nodes', [])
  if sources:
    s = sources[0]
    creator = {"@type": "Organization", "name": s.get('name', s.get('value', ''))}
    if s.get('dcid'):
      creator["@id"] = s['dcid']
      urls = fetch.property_values([s['dcid']], 'url').get(s['dcid']) or []
      if urls:
        creator["url"] = urls[0]
      doc["prov:wasAttributedTo"] = {"@id": s['dcid']}
    doc["creator"] = [creator]

  # provenances that are part of this Dataset: datePublished, fallbacks, FileObject/RecordSet
  resp = dc.v2node([dcid], '<-isPartOf')
  provs = ((((resp.get('data') or {}).get(dcid) or {}).get('arcs') or {}).get('isPartOf') or {}).get('nodes', [])
  prov_ids = [p['dcid'] for p in provs if p.get('dcid')]
  pprops = fetch.multiple_property_values(prov_ids, [
      'name', 'url', 'downloadUrl', 'description', 'provenanceCategory', 'license', 'lastDataRefreshDate',
      'earliestDataImportDate', 'earliestObservationDate', 'latestObservationDate'
  ]) if prov_ids else {}
  licences, refreshed, created, files, records = set(), '', '', [], []
  first_obs, last_obs = [], []
  for pid in prov_ids:
    p = pprops.get(pid, {})
    if not set(p.get('provenanceCategory') or []) & {'StatisticsProvenance', 'AggregatedStatisticsProvenance'}:
      continue
    if 'description' not in doc and _first(p, 'description'):
      doc["description"] = _first(p, 'description')
    licences.update(p.get('license') or [])
    refreshed = max([refreshed] + (p.get('lastDataRefreshDate') or []))
    for d in p.get('earliestDataImportDate') or []:
      created = d if not created or d < created else created
    first_obs += p.get('earliestObservationDate') or []
    last_obs += p.get('latestObservationDate') or []
    if _first(p, 'downloadUrl'):
      name = _first(p, 'name') or pid
      fo = {"@type": "cr:FileObject", "@id": name, "name": name, "contentUrl": _first(p, 'downloadUrl'),
            "encodingFormat": "text/csv"}
      if _first(p, 'description'):
        fo["description"] = _first(p, 'description')
      if _first(p, 'url'):
        fo["prov:wasDerivedFrom"] = {"@id": _first(p, 'url')}
      files.append(fo)
      records.append({"@type": "cr:RecordSet", "@id": f"{name}-records", "name": f"{name}-records", "field": [{
          "@type": "cr:Field", "@id": f"{name}-records/{c}", "name": f"{name}-records/{c}",
          "dataType": "sc:Number" if c in ("value", "scalingFactor") else "sc:Text",
          "source": {"fileObject": {"@id": name}, "extract": {"column": c}}} for c in _RECORD_COLUMNS]})
  if 'license' not in doc and licences:
    lic = sorted(licences)
    doc["license"] = lic if len(lic) > 1 else lic[0]
  if refreshed:
    doc["datePublished"] = refreshed
  if created:
    doc["dateCreated"] = created
  if first_obs and last_obs:   # design doc (PDF, 2026-07-24): earliest/latestObservationDate -> temporalCoverage
    doc["temporalCoverage"] = f"{min(first_obs)}/{max(last_obs)}"
  if files:
    doc["distribution"], doc["recordSet"] = files, records

  # keywords, spatialCoverage, variableMeasured: from the pipeline's catalogue (no graph property for them yet);
  # they are what Google Dataset Search matches searches against
  extra = _json_file(EXTRA_FILE, {}).get(dcid) or {}
  for key in ('keywords', 'spatialCoverage', 'variableMeasured'):
    if extra.get(key):
      doc[key] = extra[key]
  doc["isAccessibleForFree"] = True

  # upstream _validate_dataset
  desc = doc.get("description") or ""
  missing = [k for k in ("name", "url", "license", "datePublished", "creator") if not doc.get(k)]
  if missing or not 50 <= len(desc) <= 5000:
    logging.info('custom.py: no Croissant JSON-LD for %s: missing %s, description length %d', dcid, missing,
                 len(desc))
    return {}
  return doc


def _add_croissant_route(static):
  """/croissant/<dcid>: the JSON-LD alone (for mlcroissant, loaders, validators); 404 when not a valid Dataset."""
  from flask import Response

  def croissant(dcid):
    doc = build_croissant(dcid)
    if not doc:
      return Response(json.dumps({"error": f"{dcid} is not a Dataset with complete Croissant metadata"}), 404,
                      mimetype='application/json')
    return Response(json.dumps(doc, indent=2), 200, mimetype='application/ld+json')

  static.bp.add_url_rule('/croissant/<path:dcid>', 'dcp_croissant', croissant)
  logging.info('custom.py: /croissant/<dcid> added')


# ---------------------------------------------------------------- search must not answer a different question
# Stock search shows the closest variable even when nothing is close (cosine 0.5). Real questions score 0.82-1.0;
# questions about data we do not have score ~0.55 ("ebola incidences in kenya" -> COVID vaccines).
SEARCH_MIN_SCORE = float(os.environ.get('SEARCH_MIN_SCORE', '0.7'))


def _patch_search_threshold(params):
  stock = params.sv_threshold_override

  def sv_threshold_override(dargs):
    return max(stock(dargs) or 0.0, SEARCH_MIN_SCORE)

  params.sv_threshold_override = sv_threshold_override


def _no_match_message(data):
  """detect-and-fulfill JSON with no variable matched: tell the user, instead of a silent place overview."""
  ctx = (data.get('context') or [{}])[-1] if isinstance(data.get('context'), list) else {}
  insight = ctx.get('insightCtx') or {}
  if insight.get('variables'):
    return None
  sv = (data.get('debug') or {}).get('sv_matching') or {}
  scores = sv.get('CosineScore') or []
  if scores and scores[0] >= SEARCH_MIN_SCORE:
    return None
  import flask
  q = (flask.request.args.get('q') or '').strip()
  places = [p.get('name') for p in ctx.get('places') or [] if p.get('name')]
  msg = f'This Data Commons has no data matching "{q}" yet.' if q else 'No matching data in this Data Commons yet.'
  if places:
    msg += f' Showing an overview of {", ".join(places)} instead.'
  return msg + ' The full list of datasets is on the Data sources page (/data).'


# ---------------------------------------------------------------- place names the recogniser misses
# Whole-word, case-insensitive; the right side is a name the stock recogniser resolves (checked on this instance).
QUERY_PLACE_ALIASES = {
    'drc': 'democratic republic of the congo',
    'dr congo': 'democratic republic of the congo',
    'rdc': 'democratic republic of the congo',
    # The recogniser knows neither "North Kivu" nor "Nord-Kivu": map straight to the place (dcid:...).
    'nord-kivu': 'dcid:wikidataId/Q130625', 'nord kivu': 'dcid:wikidataId/Q130625',
    'north kivu': 'dcid:wikidataId/Q130625',
    'sud-kivu': 'south kivu', 'sud kivu': 'south kivu',
    'ituri': 'ituri province',
    # Zimbabwe: these names only mean the province (ZIMSTAT publishes by province). Harare, Bulawayo and Masvingo
    # are also cities, so they are left to the recogniser ("Harare Province" finds the province data).
    'manicaland': 'manicaland province', 'midlands': 'midlands province',
    'mashonaland central': 'mashonaland central province', 'mashonaland east': 'mashonaland east province',
    'mashonaland west': 'mashonaland west province', 'matabeleland north': 'matabeleland north province',
    'matabeleland south': 'matabeleland south province',
}
_ALIAS_RE = re.compile(r'\b(' + '|'.join(sorted((re.escape(k) for k in QUERY_PLACE_ALIASES), key=len, reverse=True))
                       + r')\b(?!\s+province)', re.IGNORECASE)


def _patch_place_detection(place):
  stock = place.detect_from_query_dc

  def detect_from_query_dc(orig_query, debug_logs, *args, **kwargs):
    forced = []  # (text in the query, dcid) for aliases that go straight to a place

    def sub(m):
      target = QUERY_PLACE_ALIASES[m.group(1).lower()]
      if target.startswith('dcid:'):
        forced.append((m.group(1), target[5:]))
        return ' '
      return target

    query = re.sub(r'\s+', ' ', _ALIAS_RE.sub(sub, orig_query)).strip()
    det = stock(query, debug_logs, *args, **kwargs)
    if forced:
      try:
        places, parents = place.get_place_from_dcids([d for _, d in forced], debug_logs)
        if places:
          det.places_found = places + [p for p in det.places_found if p.dcid not in {x.dcid for x in places}]
          det.main_place = places[0]
          det.parent_places = parents.get(places[0].dcid, [])
          det.query_places_mentioned = [t for t, _ in forced] + list(det.query_places_mentioned or [])
          det.query_original = orig_query
      except Exception:
        logging.exception('custom.py: place alias %s', forced)
    return det

  place.detect_from_query_dc = detect_from_query_dc


# ---------------------------------------------------------------- "by country" charts for our own data
# The search checks a sample of the first 15 child places before drawing "<variable> in countries of the World", so
# data for a few countries only (Ebola in the DRC) never showed. Places that hold this instance's data go first.
DATA_PLACES_FILE = '/workspace/server/dist/custom_dc/custom/data_places.json'  # from build_site_catalog.py
_data_places = None


def _patch_sample_child_places(nl_utils):
  stock = nl_utils._get_sample_child_places

  def _get_sample_child_places(main_place_dcid, contained_place_type):
    global _data_places
    result = stock(main_place_dcid, contained_place_type)
    if not contained_place_type:
      return result
    try:
      if _data_places is None:
        _data_places = set(_json_file(DATA_PLACES_FILE, []))
      from server.lib import fetch
      kids = fetch.descendent_places([main_place_dcid], contained_place_type).get(main_place_dcid) or []
      ours = [k for k in kids if k in _data_places]
      if ours:
        n = nl_utils.NUM_CHILD_PLACES_FOR_EXISTENCE
        result = (ours + [k for k in result if k not in set(ours)])[:max(n, len(ours[:n]))]
    except Exception:
      logging.exception('custom.py: sample child places')
    return result

  nl_utils._get_sample_child_places = _get_sample_child_places


def _patch_places_to_check(base):
  """Same for explore answers ("ebola cases in the world"): fulfillment.base checks the first 15 child places."""
  stock = base.get_places_to_check

  def get_places_to_check(state, places):
    global _data_places
    result = stock(state, places)
    try:
      if state.place_type and len(places) == 1:
        if _data_places is None:
          _data_places = set(_json_file(DATA_PLACES_FILE, []))
        from server.lib.nl.common import utils as nl_utils
        key = places[0].dcid + state.place_type.value
        for p in nl_utils.get_all_child_places(places[0].dcid, state.place_type.value, state.uttr.counters):
          if p.dcid in _data_places and p.dcid not in result:
            result[p.dcid] = key
    except Exception:
      logging.exception('custom.py: places to check')
    return result

  base.get_places_to_check = get_places_to_check


# ---------------------------------------------------------------- questions that name no place
# Most of this instance's variables cover one country (ZIMSTAT: Zimbabwe), so a question with no place that matches
# one of them is answered for that country (site/static/var_home.json, written by build_site_catalog.py).
VAR_HOME_FILE = '/workspace/server/dist/custom_dc/custom/var_home.json'
_var_home = None


def _patch_context(context):
  stock = context.merge_with_context

  def merge_with_context(uttr, default_place=None):
    global _var_home
    try:
      if not uttr.places and (default_place is None or default_place.dcid == 'Earth') and uttr.svs:
        if _var_home is None:
          _var_home = _json_file(VAR_HOME_FILE, {})
        home = next((_var_home[sv] for sv in uttr.svs[:3] if sv in _var_home), None)
        if home:
          from server.lib.nl.detection import place as nl_place
          places, _ = nl_place.get_place_from_dcids([home], {})
          if places:
            default_place = places[0]
    except Exception:
      logging.exception('custom.py: default place from variable')
    return stock(uttr, default_place)

  context.merge_with_context = merge_with_context


# ---------------------------------------------------------------- "today", "now", "this week" ...
# Stock search returns a blank page for questions it classifies as Temporal; answer with the latest figures instead.
def _patch_fulfiller(fulfiller):
  stock = fulfiller._perform_classification_checks
  from server.lib.nl.detection import types as dtypes

  def _perform_classification_checks(uttr):
    temporal = [c for c in uttr.classifications if c.type == dtypes.ClassificationType.TEMPORAL]
    if temporal:
      uttr.classifications = [c for c in uttr.classifications if c.type != dtypes.ClassificationType.TEMPORAL]
      uttr.counters.info('dcp_temporal_as_latest', True)
    return stock(uttr)

  fulfiller._perform_classification_checks = _perform_classification_checks


def _patch_responses():
  """HTML responses: remove the Google Maps script when there is no key; add the Croissant JSON-LD to
  /browser/<dataset> pages (in <head>, where crawlers look for it)."""
  import flask
  stock = flask.Flask.process_response
  if getattr(stock, '_dcp', False):
    return

  def process_response(self, response):
    response = stock(self, response)
    try:
      if (response.mimetype == 'application/json' and response.status_code == 200 and
          (flask.request.path.startswith('/api/place/related-places/') or flask.request.path == '/api/place/parent')):
        # Parent places top-down ("World › Africa", not "Africa, World"): stock lists them nearest first.
        data = json.loads(response.get_data())
        if isinstance(data, list):
          data = data[::-1]
        elif isinstance(data, dict) and isinstance(data.get('parentPlaces'), list):
          data['parentPlaces'] = data['parentPlaces'][::-1]
        response.set_data(json.dumps(data))
        return response
      if (response.mimetype == 'application/json' and response.status_code == 200 and
          flask.request.path == '/api/explore/detect-and-fulfill'):
        data = json.loads(response.get_data())
        msg = _no_match_message(data)
        info = (((data.get('debug') or {}).get('counters') or {}).get('INFO') or {})
        if not msg and info.get('dcp_temporal_as_latest'):
          msg = 'Showing the latest figures available ("today", "now" and similar are read as the latest date with data).'
        if msg:
          data['userMessages'] = [msg] + [m for m in data.get('userMessages') or []
                                          if not m.startswith('Could not recognize any topic')]
          response.set_data(json.dumps(data))
        return response
      if (response.mimetype == 'text/html' and not response.direct_passthrough and not response.is_streamed and
          response.status_code == 200):
        body = response.get_data()
        changed = False
        if LOCAL_PLACE_SEARCH and b'maps.googleapis.com/maps/api/js' in body:
          body, changed = _GOOGLE_MAPS_SCRIPT.sub(b'', body), True
        path = flask.request.path
        if path.startswith('/browser/') and len(path) > len('/browser/') and b'</head>' in body:
          doc = build_croissant(path[len('/browser/'):])
          if doc:
            tag = ('<script type="application/ld+json">' +
                   json.dumps(doc).replace('</', '<\\/') + '</script>\n').encode('utf-8')
            body, changed = body.replace(b'</head>', tag + b'</head>', 1), True
        if changed:
          response.set_data(body)
    except Exception:
      logging.exception('custom.py: response patch failed')
    return response

  process_response._dcp = True
  flask.Flask.process_response = process_response


# Module -> patch, applied when the module is first imported (the routes cannot be imported here: circular imports).
_PATCHES = {
    'server.routes.explore.helpers': (_patch_default_place, 'set World as the default search place'),
    'server.routes.static': (_add_pages, 'add /drilldown, /sitemap.xml, /robots.txt, /croissant/<dcid>'),
    'server.routes.shared_api.autocomplete.stat_vars': (_patch_stat_var_autocomplete,
                                                        'serve indicator suggestions locally'),
    'server.lib.nl.explore.params': (_patch_search_threshold, 'raise the search match threshold'),
    'server.lib.nl.detection.place': (_patch_place_detection, 'add place-name aliases to search'),
    'server.lib.nl.common.utils': (_patch_sample_child_places, 'check places with our data first'),
    'server.lib.nl.fulfillment.base': (_patch_places_to_check, 'check places with our data in explore answers'),
    'server.lib.nl.detection.context': (_patch_context, 'answer place-less questions for the data\'s country'),
    'server.lib.nl.fulfillment.fulfiller': (_patch_fulfiller, 'answer "today" questions with the latest data'),
}
if LOCAL_PLACE_SEARCH:
  _PATCHES['server.routes.shared_api.place'] = (_patch_place_api, 'accept dcid: place ids')
  _PATCHES['server.routes.shared_api.autocomplete.helpers'] = (_patch_autocomplete,
                                                               'serve autocomplete places locally')
_patch_responses()
_DEFERRED = []  # modules still loading when this file runs (circular imports): patched on the first request


def _apply(name, patch, what):
  try:
    patch(sys.modules[name])
    logging.info('custom.py: %s', what)
  except Exception:
    logging.exception('custom.py: could not %s', what)


for _name in [n for n in _PATCHES if n in sys.modules]:  # already imported: patch now (or once it has loaded)
  _patch, _what = _PATCHES.pop(_name)
  if getattr(getattr(sys.modules[_name], '__spec__', None), '_initializing', False):
    _DEFERRED.append((_name, _patch, _what))
  else:
    _apply(_name, _patch, _what)
if _DEFERRED:
  import flask as _flask
  _stock_preprocess = _flask.Flask.preprocess_request

  def _preprocess_request(self):
    while _DEFERRED:
      _apply(*_DEFERRED.pop(0))
    return _stock_preprocess(self)

  _flask.Flask.preprocess_request = _preprocess_request


class _PatchOnImport(importlib.abc.MetaPathFinder):

  def find_spec(self, name, path, target=None):
    if name not in _PATCHES:
      return None
    patch, what = _PATCHES.pop(name)
    if name.endswith('autocomplete.helpers') and 'server.routes.shared_api.place' in _PATCHES:
      importlib.import_module('server.routes.shared_api.place')
    if not _PATCHES and self in sys.meta_path:
      sys.meta_path.remove(self)
    spec = importlib.util.find_spec(name)
    if spec is None or spec.loader is None:
      return spec
    stock_exec = spec.loader.exec_module

    def exec_module(module):
      stock_exec(module)
      try:
        patch(module)
      except Exception:  # never stop the site over a customization; the stock behaviour stays
        logging.exception('custom.py: could not %s', what)

    spec.loader.exec_module = exec_module
    return spec


if not any(type(f).__name__ == '_PatchOnImport' for f in sys.meta_path):
  sys.meta_path.insert(0, _PatchOnImport())


class LocalConfig(Config, local.Config):
  pass


class ComposeConfig(Config, local.Config):
  pass
