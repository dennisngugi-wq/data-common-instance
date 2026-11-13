# DataCommon Project: our own Data Commons instance

A local **Custom Data Commons** that looks and works like datacommons.org, but serves **our** data. Every night at
**00:00 Africa/Nairobi** it re-reads every source (Google Sheets, Excel files, CSV/JSON on websites and APIs,
statistics-office portals), validates it, publishes only what passes and reloads the site.

- Site: **http://localhost:8090** · MCP server: **http://localhost:8090/mcp**
- Croissant 1.1 JSON-LD for every licensed dataset: `/browser/<dataset dcid>` (in the page) and `/croissant/<dataset dcid>`

## Run it on a new machine

**You need:** Linux (Ubuntu 22.04+ tested) or WSL2, **Docker Engine** with the Compose plugin, git, about
**15 GB** free disk (images ~9 GB + data) and **8 GB RAM** (the website alone uses ~4 GB), internet access, and a
free **Data Commons API key** from https://apikeys.datacommons.org.

```bash
# 1. Docker Engine (skip if `docker compose version` already works)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"      # then log out and back in
sudo systemctl enable --now docker

# 2. Get the project
git clone https://github.com/dennisngugi-wq/data-common-instance.git
cd data-common-instance

# 3. Settings: put your key in env.list (no quotes, no spaces around =)
cp env.list.example env.list
nano env.list                         # DC_API_KEY=<your key>
cp .env.example .env                  # optional: change SITE_PORT, refresh time (start.sh fills in the paths)

# 4. Start: downloads the images, builds the pipeline, fetches and validates every dataset, loads them, starts the site
bash scripts/start.sh
docker compose logs -f scheduler      # follow the first load (15-30 min the first time; Ctrl+C stops following only)
```

Then open **http://localhost:8090**. The result of each refresh is in `work/last_refresh.txt`. The site and the
nightly refresh restart by themselves after a reboot (`restart: unless-stopped`); if not, run `bash scripts/start.sh`.

Notes for a fresh clone:
- The repository holds code and configuration, not the database: the first start rebuilds all data from the sources.
- `insp_ebola_drc` reads a local file (`data/inbox/insp_ebola/ebola_drc_provinces.csv`) that is not in the repository
  (no licence from the publisher). Without it that one dataset is reported as missing; the others load normally.
- If Docker Desktop is also installed, opening it switches the `docker` command to Desktop's own engine; the scripts
  always use Docker Engine, and `docker context use default` switches your terminal back.
- Port 8090 already taken? Set `SITE_PORT` in `.env`.

## Everyday commands

```bash
bash scripts/start.sh                # start (or rebuild and start) the instance
bash scripts/start.sh --stop         # stop (data is kept)
bash scripts/refresh-now.sh          # refresh now instead of at midnight (starts within a minute)
bash scripts/check-feed.sh my_feed   # try a new feed file without publishing anything
docker compose logs -f scheduler     # follow a refresh
docker ps -a --filter name=dcproject # containers; "Exited (0)" dataset jobs are normal (they run, then stop)
```

## Feeding data in (no code)

| You have | Do this |
|---|---|
| **A sheet listing many datasets** (one row per dataset, Croissant-style columns, e.g. the ZIMSTAT sheet) | Add `config/registries/<id>.yaml` (copy `zimstat.yaml`). Every run downloads the **whole workbook, all tabs**, keeps them in `local_store/registries/<id>/` with a change log, and loads **each row as its own dataset**. A new row = a new dataset the next night. |
| **A Google Sheet with numbers** | Copy `config/feeds/templates/google_sheet.yaml` to `config/feeds/<id>.yaml`; share the sheet "Anyone with the link: Viewer". |
| **An Excel file** (download link, or dropped in `data/inbox/`) | `templates/excel_file.yaml` (years-as-columns tables, title rows, scaling supported). |
| **CSV / JSON API / HTML table on a website** | `templates/csv_or_api.yaml`; starter examples: `owid_co2_per_capita.yaml` (CSV), `worldbank_population.yaml` (API). |
| **A new organisation** | Add it once to `config/sources.yaml` with its `aliases` (e.g. KNBS = Kenya National Bureau of Statistics = KenyaBureauOfStatistics). Every spelling maps to one source, and searching any of them finds its data. |

Every dataset must carry Croissant metadata: official name, a 50-5000 character description, landing page URL, the
licence URL that the publisher states (never guessed) and the source organisation (= Croissant creator). A dataset
without a confirmed licence can be loaded (`unlicensed: load_without_croissant`), but gets no Croissant JSON-LD until
its licence URL is filled in.

## The data: a brief history

| Date | What |
|---|---|
| 2026-11-13 | Instance created (namespace `DCProject`, port 8090). First feeds: **World Bank WDI total population** (JSON API, 216 places, 1960-2025) and **Our World in Data CO2 emissions per capita** (CSV, 219 places, 1750-2025; one reviewed exclusion, Sint Maarten 1956). Both CC BY 4.0. |
| 2026-11-13 | **ZIMSTAT registry**: the team sheet listing 99 Zimbabwe National Statistics Agency datasets (all 4 tabs mirrored); each row is loaded as its own dataset from ZIMSTAT's JSON-stat API: population, labour force, prices (CPI, PPI), national accounts, livestock, crops, trade, migration, vital statistics. |
| 2026-11-13 | **INSP Ebola (DRC)**: provincial snapshot of the INSP situation report of 2026-07-14 (Ituri, Nord-Kivu, Sud-Kivu). |
| 2026-11-13 | Search improvements: only answers with a variable that really matches the question, local place names (DRC, Nord-Kivu, Zimbabwe provinces), suggestions that keep up while a page loads. |

**Now:** 100 datasets live from 4 sources, 148,865 observations. Two ZIMSTAT datasets (`DF_LF_28`,
`MIG_STA_ARR_ALL`) are retried every night because ZIMSTAT's API returns HTTP 500 for them. 98 datasets have no
licence confirmed by their publisher yet: their data is loaded, but their Croissant JSON-LD is withheld until the
licence URL is added (licences are never guessed).

**In this repository:** only the two datasets with a confirmed licence are included as data, in `data/published/`
(see its README for attribution). Everything else is rebuilt from the sources on the first start.

## How a refresh works (one main instance, one container per dataset)

All containers belong to the compose project **`dcproject`** (the main instance). In Docker Desktop / Portainer each
dataset appears as its own container under it, with its own status and logs; a new dataset adds a new container.

```
dcproject-scheduler            midnight / start-up / refresh-now trigger
 ├─ dcproject-sync-registries  download every registry workbook, mirror all tabs, log added/removed/changed rows
 ├─ dcproject-data-<feed>      one per dataset: fetch -> map -> validate -> publish (or keep the last good version)
 ├─ dcproject-finalize         shared schema (once), run report, knowledge base, merge for the loader, place index,
 │                             homepage questions -> prints CHANGED / UNCHANGED
 ├─ loader                     only if CHANGED: load into local_dc_db_next/
 └─ dcproject-website-1        swapped to the new database only after "Runner finished successfully"
```

Validation (a failing dataset is never published; the others still are):

| Rule | Meaning |
|---|---|
| C1-C6 | Croissant: name, description 50-5000 chars, landing URL, licence URL + evidence (or PENDING), creator, download URL |
| E1 | source unreachable or unreadable (last good version stays live, retried next night) |
| E2-E8 | no data; missing columns; unknown places; non-numbers / bad dates; outside min/max; two values for one variable+place+date; dates outside earliest_year..today (projections need `allow_future`) |
| E9 | rows vanish vs. what is published (> 20% by default): protects against an emptied sheet or truncated download |
| V10 | every project dcid referenced in the schema is defined |
| V12 | **no node outside our namespace (`DCProject...`) is ever written**: existing Data Commons nodes (`dc/s/WorldBank`, `Count_Person`, ...) are referenced, never redefined, so loading this instance into another Data Commons can never change its data |
| warnings | large revisions, ambiguous places (both skipped), unmapped labels, skipped non-place codes |

Reviewed exclusions go in the feed file (`exclude:` with a reason); never widen a min/max to let a value through.

### Places
Names and codes are matched to Data Commons places: `config/places.yaml` aliases → built-in gazetteer (World,
continents, UN M49 regions, 215 countries) → for sub-national places, the official list of children in Data Commons
(e.g. Zimbabwe's 10 provinces) → the resolve API (cached in `local_store/state/place_cache.json`). The resolve API
alone is not trusted for provinces: it maps "Bulawayo" to the city, not Bulawayo Province. Codes that are not areas
of the publisher (e.g. destination countries of Zimbabwean labour migrants, hospitals) are skipped and reported.

### Registry datasets (JSON-stat / SDMX)
One statistical variable per combination of the dimensions that vary (e.g. sex × age), named
`<dataset>: <member labels>`, grouped Source → sub-domain → dataset in the variable explorer; `UNIT_MEASURE` becomes
the unit; quarterly periods are stored as the quarter's first month with `observationPeriod P3M` (the Data Commons
convention); cubes that mix frequencies get one variable per frequency.

## Files

| Path | What |
|---|---|
| `config/instance.yaml` | name, namespace (`DCProject`: never change after the first load), site URL, validation limits |
| `config/sources.yaml`, `config/places.yaml` | organisations (+ aliases), place aliases |
| `config/feeds/*.yaml`, `config/feeds/templates/` | one dataset per file |
| `config/registries/*.yaml` | sheets that list datasets |
| `automation/src/` | pipeline (`run_pipeline.py`), readers (`fetch.py`, `jsonstat.py`), `normalize.py`, `build_dcp.py` (DCP imports), `build_local_dc.py`, `croissant.py`, `knowledge.py`, `registries.py` |
| `automation/tests/` | `docker compose run --rm pipeline python -m unittest discover -s automation/tests` |
| `automation/src/readback_local.py` | every published value read back through the site API (READBACK PASS) |
| `site/` | website customization (see `site/README.md`) |
| `data/published/` | snapshot of the licensed datasets (CSV + MCF, CC BY 4.0) |
| `data/inbox/` | local files read by feeds (git-ignored) |
| `local_store/` | published DCP imports (GCS-bucket layout), state, backups, run reports, registry mirrors |
| `work/` | raw downloads, per-dataset reports, `validation_summary.json`, Croissant previews, logs |

## Images
`gcr.io/datcom-ci/datacommons-services:stable` and `datacommons-data:stable` (SQLite, runs fully locally).
Release **1.1.7** (Oct 2026) moved to the Spanner-based DCP stack: its website needs a GCP project + Spanner and its
loader no longer writes SQLite, so it cannot serve a local folder. It does contain the upstream Croissant code; the
`stable` image does not, so `site/app_env/custom.py` backports it. When this instance moves to GCP (DCP), the
`local_store/ingestion/input/` imports are already in the DCP layout (schema import first).

## Docker Desktop
On Linux, Docker Desktop runs its own engine in a VM and cannot see Docker Engine containers. `scripts/start.sh` always
uses Docker Engine; `DOCKER_CONTEXT=desktop-linux bash scripts/start.sh` runs the instance inside Docker Desktop
instead, after raising Desktop's memory to at least 8 GB (Settings > Resources). `scripts/desktop-copy.sh` makes a
separate one-click copy for Docker Desktop on port 8091.

## Author

Built and maintained by **Dennis Ngugi** ([@dennisngugi-wq](https://github.com/dennisngugi-wq)).
