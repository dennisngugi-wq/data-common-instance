# DataCommon Project: our own Data Commons instance

A local **Custom Data Commons** that looks and works like datacommons.org, but serves **our** data. Every night at
**00:00 Africa/Nairobi** it re-reads every source (Google Sheets, Excel files, CSV/JSON on websites and APIs,
statistics-office portals), validates it, publishes only what passes and reloads the site.

- Site: **http://localhost:8090** · MCP server: **http://localhost:8090/mcp**
- Croissant 1.1 JSON-LD for every licensed dataset: `/browser/<dataset dcid>` (in the page) and `/croissant/<dataset dcid>`

## Where things are

| You want to | Go to |
|---|---|
| Add data | `data/inbox/links.txt` (one `id link` per line) or `config/sheets.json`; Excel files in `data/inbox/` |
| Load it now | `docker compose exec scheduler refresh` (or `bash scripts/refresh-now.sh`) |
| Know what a dataset must have | [`RULES.md`](RULES.md): the pipeline rules, from the Croissant design doc |
| Read what happened | `logs/pipeline.log` (latest run), `logs/history/` (every run), `logs/pipeline-details.log`, `logs/loader.log`, `logs/last_refresh.txt` |
| See every check of a dataset | `work/feed_reports/<id>.json`, `work/validation_summary.json` |
| Change the pipeline | `automation/src/` (code), `automation/tests/` (tests) |
| Change the website | `site/` (see `site/README.md`) |
| Settings | `config/` (instance, sources, places, registries, custom feeds) |
| Notes and decisions | `../../mcp agent/knowledge/` (`INDEX.md`, `decisions.md`) |
| Data on disk | `local_store/` (validated datasets), `local_dc/` (database input), `local_dc_db/` (live database) |

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
tail -f logs/pipeline.log             # follow the first load (15-30 min the first time; Ctrl+C stops following only)
```

Then open **http://localhost:8090**. The result of each refresh is in `logs/last_refresh.txt`. The site and the
nightly refresh restart by themselves after a reboot (`restart: unless-stopped`); if not, run `bash scripts/start.sh`.

Notes for a fresh clone:
- The repository holds code and configuration, not the database: the first start rebuilds all data from the sources.
- `insp_ebola_drc` reads a local file (`data/inbox/insp_ebola/ebola_drc_provinces.csv`) that is not in the repository
  (no licence from the publisher). Without it that one dataset is reported as missing; the others load normally.
- If Docker Desktop is also installed, opening it switches the `docker` command to Desktop's own engine; the scripts
  always use Docker Engine, and `docker context use default` switches your terminal back.
- Port 8090 already taken? Set `SITE_PORT` in `.env`.

## Share it from GitHub Codespaces

A public link to a running copy of the site, without a server of your own:

1. Once: repository **Settings > Secrets and variables > Codespaces > New repository secret**, name `DC_API_KEY`,
   value your Data Commons API key.
2. **Code > Codespaces > Create codespace** on this branch (it asks for a 4-core / 16 GB machine). The site starts by
   itself; the first load takes 20-40 minutes (`tail -f logs/pipeline.log`).
3. **Ports** tab > port **8090** > right-click > **Port visibility > Public**, then share its address
   (`https://<codespace-name>-8090.app.github.dev`).

The codespace stops after 30 minutes without activity (the link stops working until it is started again) and uses
your Codespaces hours (free allowance, then paid). For an always-on link, deploy to a server.

## Everyday commands

The site and the nightly refresh start by themselves when the computer boots (Docker Engine is enabled and the
containers use `restart: unless-stopped`), so normally you just open http://localhost:8090.

**One click:** run `bash scripts/launch.sh --install` once. It adds **DataCommon Project** (with the DC logo) to the
app menu and the Desktop; clicking it starts anything that is not running and opens the site in the browser.

```bash
bash scripts/launch.sh               # start what is not running, then open the site (what the icon does)
bash scripts/start.sh                # start (or rebuild and start) the instance
bash scripts/start.sh --stop         # stop (data is kept)
docker compose exec scheduler refresh  # refresh now instead of at midnight (starts within a minute)
bash scripts/refresh-now.sh          # the same, from the project folder
bash scripts/check-feed.sh my_sheet  # try a sheet from config/sheets.json without publishing anything
tail -f logs/pipeline.log            # follow a refresh (see "Reading the refresh log" below)
docker ps -a --filter name=dcproject # containers; "Exited (0)" dataset jobs are normal (they run, then stop)
```

## Adding data: `data/inbox/links.txt` or `config/sheets.json`

All data comes from spreadsheets you provide (Google Sheets or Excel); the pipeline never scrapes or invents data.
The quickest way: add a line `<id> <link>` to **`data/inbox/links.txt`** (or drop a `.json` file with the same
entries in `data/inbox/`), then run `docker compose exec scheduler refresh`. Entries there are on by default.
The permanent list is [`config/sheets.json`](config/sheets.json); an id may appear only once across both:

```json
{ "sheets": [
    { "id": "kenya_county_population",
      "link": "https://docs.google.com/spreadsheets/d/1AbC.../edit#gid=0",
      "enabled": true,
      "note": "KNBS census 2019 tables" }
] }
```

1. **Fill in the template** [`templates/DataCommon_dataset_template.xlsx`](templates/) (for Google Sheets: upload
   it to Drive and open it with Google Sheets). Tab **About**: dataset name, description, source organisation,
   licence. Tab **Data**: `Place | Date | Variable | Value | Unit`, one row per number (or one column per variable).
   Tab **Variables** (optional): description, unit, min/max per variable.
2. **Share** a Google Sheet as "Anyone with the link: Viewer" and copy its link. An Excel file: put it in
   `data/inbox/` and use its path (`data/inbox/my_file.xlsx`) as the link, or a download link.
3. **Add an entry** to `config/sheets.json`: `id` (lowercase letters, digits, `_`; never change it later), `link`,
   `"enabled": true`.
4. **Try it** (publishes nothing, works while `enabled` is still false): `bash scripts/check-feed.sh <id>`.
   The report names any cell to fix (missing dataset name, unknown place, a value that is not a number, ...).
5. **Load it**: `docker compose exec scheduler refresh` (or wait for the nightly run). Then it is on the site: search,
   Explore, Data sources, the homepage topics.

Edit the sheet any time: the next run picks up the changes. `"enabled": false` stops loading it.

| `kind` | When | Extra file |
|---|---|---|
| `dataset` (default) | a sheet in the DataCommon template | none |
| `registry` | a sheet listing many datasets, one per row, each with a link to its data (e.g. the ZIMSTAT sheet) | `config/registries/<id>.yaml`: which columns hold what (written once) |
| `custom` | a sheet in its own layout that cannot be put in the template | `config/feeds/<id>.yaml`: the column mapping (written once; templates in `config/feeds/templates/`) |

The link and the on/off switch always live in `sheets.json`; the extra files only say how to read a sheet.
A new organisation is added automatically from the About tab (name + website); known ones (and their acronyms) are
matched through `config/sources.yaml`. Every dataset must meet the Croissant rules before it is loaded: official
name, factual description of 50-5000 characters, landing-page URL, creator, and a licence URL that the publisher
explicitly applies to the dataset, with where it is stated. Licences are never guessed or inferred: a dataset
without one is not loaded into the database, the search or the Data sources page. Its downloaded copy is kept, and it
loads on the next refresh after the licence is filled in on its sheet.

## The data: a brief history

| Date | What |
|---|---|
| 2026-11-13 | Instance created (namespace `DCProject`, port 8090). First feeds: **World Bank WDI total population** (JSON API, 216 places, 1960-2025) and **Our World in Data CO2 emissions per capita** (CSV, 219 places, 1750-2025; one reviewed exclusion, Sint Maarten 1956). Both CC BY 4.0. |
| 2026-11-13 | **ZIMSTAT registry**: the team sheet listing 99 Zimbabwe National Statistics Agency datasets (all 4 tabs mirrored); each row is loaded as its own dataset from ZIMSTAT's JSON-stat API: population, labour force, prices (CPI, PPI), national accounts, livestock, crops, trade, migration, vital statistics. |
| 2026-11-13 | **INSP Ebola (DRC)**: provincial snapshot of the INSP situation report of 2026-07-14 (Ituri, Nord-Kivu, Sud-Kivu). |
| 2026-11-13 | Search improvements: only answers with a variable that really matches the question, local place names (DRC, Nord-Kivu, Zimbabwe provinces), suggestions that keep up while a page loads. |

**Now:** 100 datasets live from 4 sources, 148,865 observations. Two ZIMSTAT datasets (`DF_LF_28`,
`MIG_STA_ARR_ALL`) are retried every night because ZIMSTAT's API returns HTTP 500 for them.
2026-11-16: 98 datasets (97 ZIMSTAT, INSP Ebola) have no licence confirmed by their publisher, so they are no longer
loaded; only the two licensed datasets are live until their licence URLs are recorded (never guessed).

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
 │                             homepage questions -> work/local_dc_status.txt CHANGED / UNCHANGED
 ├─ loader                     only if CHANGED: load into local_dc_db_next/
 └─ dcproject-website-1        swapped to the new database only after "Runner finished successfully"
```

### Reading the refresh log

`logs/pipeline.log` is written for people: a header, five numbered steps, then a summary.

```
STEP 1 of 5  Read the links (config/sheets.json, data/inbox/) and download the sheets
STEP 2 of 5  Check and prepare each dataset (102)     one line per dataset, e.g.
  [ 12/102] zimstat_df_pop_proj    UPDATED    58,653 values · 49 places · 2022 to 2042 · +2,394 new · 9s
            Population Projection by districts
STEP 3 of 5  Combine the datasets and update the website files
STEP 4 of 5  Load the data into the database          (skipped when nothing changed)
STEP 5 of 5  Website
SUMMARY      counts, NEW DATA PUBLISHED, NEEDS YOUR ATTENTION (each problem once, with its datasets and what to do)
RESULT       one line: did the website change, and how long the run took
```

Dataset states: `UPDATED` (new data now on the website), `UNCHANGED`, `FAILED` (breaks a rule in RULES.md: not on
the website; only a source that could not be reached keeps its last good version), `NEW DATA` (check mode only: would be updated). Marks: `✓` fine, `✗` failed, `!` warning, `i` note,
`-` skipped. Problems that hit many datasets (no internet, a missing licence) are grouped in the summary instead of
being repeated per dataset.

| File | What is in it |
|---|---|
| `logs/pipeline.log` | the readable log of the latest refresh |
| `logs/history/<date>_<time>.log` | the same log for every earlier refresh (last 60 kept) |
| `logs/pipeline-details.log` | technical messages: download retries, tracebacks, Docker output |
| `logs/loader.log` | the database loader (Data Commons' own output) |
| `logs/last_refresh.txt` | one line: when the last refresh ended and whether it worked |
| `work/feed_reports/<id>.json`, `work/validation_summary.json` | every check result, in full |

`docker compose logs -f scheduler` shows only the step headings.

Validation (full text in [`RULES.md`](RULES.md); a failing dataset is never published, the others still are):

| Rule | Meaning |
|---|---|
| C1-C6 | Croissant: name, description 50-5000 chars, landing URL, licence URL + evidence, creator, download URL; a dataset failing any of them is not loaded |
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
| `config/sheets.json` | **the list of sheets to load** (links + on/off) |
| `templates/DataCommon_dataset_template.xlsx` | the sheet layout loaded with no other configuration |
| `config/feeds/*.yaml`, `config/feeds/templates/` | column mappings of `custom` sheets (and the starter feeds) |
| `config/registries/*.yaml` | how to read `registry` sheets |
| `automation/src/` | pipeline (`run_pipeline.py`), readers (`fetch.py`, `jsonstat.py`), `normalize.py`, `build_dcp.py` (DCP imports), `build_local_dc.py`, `croissant.py`, `knowledge.py`, `registries.py` |
| `automation/tests/` | `docker compose run --rm pipeline python -m unittest discover -s automation/tests` |
| `RULES.md` | the rules every dataset must pass |
| `data/inbox/` | your links (`links.txt`, `*.json`) and Excel files (git-ignored) |
| `logs/` | refresh logs and their history (git-ignored) |
| `automation/src/readback_local.py` | every published value read back through the site API (READBACK PASS) |
| `site/` | website customization (see `site/README.md`) |
| `data/published/` | snapshot of the licensed datasets (CSV + MCF, CC BY 4.0) |
| `local_store/` | published DCP imports (GCS-bucket layout), state, backups, run reports, registry mirrors |
| `work/` | raw downloads, per-dataset reports, `validation_summary.json`, Croissant previews |

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
