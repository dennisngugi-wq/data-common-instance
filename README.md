# DataCommon Project: our own Data Commons instance

A local **Custom Data Commons** that looks and works like datacommons.org, but serves **our** data. Every night at
**00:00 Africa/Nairobi** the pipeline re-reads the sheets you provide, checks every row against the Croissant rules
(`RULES.md`), publishes only what passes, reloads the site, and writes a list of everything that failed, with the
column, the rule and the fix.

- **Site address:** wherever you run it.
  - On the machine that runs it: `http://localhost:<SITE_PORT>` (`SITE_PORT` is set in `.env`; the default is 8090).
  - From another computer on the same network: `http://<that machine's IP>:<SITE_PORT>`.
  - Hosted (Google Cloud VM, Codespaces, your own server): the public address, which you also set as `SITE_URL` in `.env`.
- MCP server: the site address followed by `/mcp`.
- Croissant 1.1 JSON-LD for every dataset: `/browser/<dataset dcid>` (in the page) and `/croissant/<dataset dcid>`

## The idea in one minute

1. You provide a spreadsheet: an **Excel (`.xlsx`) or CSV catalogue**, one row per dataset (country × indicator).
2. You drop it in `imports/data/inbox/files/` and start a refresh.
3. The pipeline checks each row. A row that breaks any rule is **not loaded anywhere** (not in the graph, the search,
   the Data sources page or the dataset pages). A row that passes becomes **Source → Dataset → Provenance** nodes.
4. Every run writes `imports/data/outbox/<file> - failures to fix.xlsx`: one line per broken rule, with the line in
   your file, the column, the rule, the current value and the fix needed. Fix the sheet, drop it in again, refresh.

Data comes only from sheets you provide. The pipeline never scrapes, transcribes reports or invents values. The one
authorised exception is **CAT-RESOLVE**: when a row's data link is a web page, the pipeline looks at that listed page
and its documented data API to find the real download (see "Finding the real download").

## Where things are

| You want to | Go to |
|---|---|
| Add a catalogue | Put the `.xlsx` or `.csv` in `imports/data/inbox/files/` (nothing else to edit) |
| Load it now | `bash pipeline/scripts/refresh-now.sh` (or `docker compose exec scheduler refresh`) |
| See what failed and how to fix it | `imports/data/outbox/<file> - failures to fix.xlsx` |
| See your sheet with the result per row | `imports/data/outbox/<file> - checked.xlsx` (`.xlsx` input only) |
| Know the rules | [`RULES.md`](RULES.md): the pipeline rules, from the Croissant design doc |
| Read what happened | `pipeline/logs/pipeline.log` (latest run), `pipeline/logs/catalogue.log` (every row), `pipeline/logs/history/` (every run), `pipeline/logs/last_refresh.txt` |
| Know when a refresh failed | `pipeline/logs/ALERT.txt` (only while there is a problem), `pipeline/logs/alerts.log` (history) |
| Validate a provenance sheet | `pipeline/src/provenance_check.py` (see "Checking a provenance sheet") |
| Change the pipeline | `pipeline/src/` (code), `pipeline/tests/` (tests) |
| Change the website | `pipeline/site/` (see `pipeline/site/README.md`) |
| Settings | `pipeline/config/` (instance, sources, places, registries, custom feeds, `data_links.yaml`) |
| Notes and decisions | `../../mcp agent/knowledge/` (`INDEX.md`, `decisions.md`) |

## Folder layout

```
datacommon-project/
├── RULES.md                      the rules every dataset must pass (the law of the pipeline)
├── docker-compose.yml, .env, env.list
├── imports/                      everything that comes in or goes out (data, not code)
│   ├── data/inbox/files/         your catalogue files (.xlsx, .csv); git-ignored
│   ├── data/inbox/links.txt      optional: Google Sheet / download links
│   ├── data/outbox/              failures to fix, checked copies; git-ignored
│   ├── data/archive/             files you moved out of the inbox; git-ignored
│   ├── templates/                DataCommon_dataset_template.xlsx (sheets with values)
│   ├── local_store/              validated rows, resolve cache, state, backups, reports; git-ignored
│   ├── local_dc/                 database input (schema.mcf, provenance.mcf); git-ignored
│   └── local_dc_db/              the live database; git-ignored
└── pipeline/                     everything that runs
    ├── src/                      code (catalog.py, resolve.py, build_local_dc.py, ...)
    ├── config/                   instance.yaml, sources.yaml, places.yaml, sheets.json, feeds/, registries/
    ├── site/                     website customisation
    ├── docker/                   scheduler.sh and the `refresh` command
    ├── scripts/                  start.sh, refresh-now.sh, launch.sh, check-feed.sh, gcp-deploy.sh, ...
    ├── tests/                    unit tests
    ├── logs/, work/              refresh logs, raw downloads, run reports; git-ignored
    └── Dockerfile, requirements.txt
```

## Commands you run yourself

First time on a machine, run `bash pipeline/scripts/start.sh` (see "Run it on a new machine"): it writes the paths into
`.env`, builds the images and starts everything. After that:

```bash
export DOCKER_CONTEXT=default                      # use Docker Engine, not Docker Desktop
docker compose up -d scheduler                     # start the background service if it is not running (it restarts after a reboot)
cp "my catalogue.xlsx" imports/data/inbox/files/   # drop a catalogue file in the inbox
bash pipeline/scripts/refresh-now.sh               # run the ingestion (starts within a minute, takes about 6 minutes)
tail -f pipeline/logs/pipeline.log                 # follow it; the result is in pipeline/logs/last_refresh.txt
```

When it finishes, open the site (address above) and take the failures list from `imports/data/outbox/`.

Other commands:

```bash
bash pipeline/scripts/start.sh                     # start (or rebuild and start) the instance
bash pipeline/scripts/start.sh --stop              # stop (data is kept)
bash pipeline/scripts/launch.sh                    # start what is not running, then open the site
bash pipeline/scripts/check-feed.sh my_sheet       # try a values sheet from sheets.json without publishing
docker compose --profile jobs run --rm -T pipeline python -m unittest discover -s pipeline/tests   # the tests
docker ps -a --filter name=dcproject               # containers; "Exited (0)" dataset jobs are normal
```

The website starts only if a database exists. After a full reset (an empty `imports/local_dc_db/`) it stays down until a
refresh has loaded at least one accepted row.

## The catalogue format

Every catalogue file looks like `Country × Indicator Dataset Exploration - Final Output.xlsx`: one row per dataset,
on any tab (or in any CSV) that has the columns below. Tabs without them (for example `Tracker`) are ignored. CSV may be
UTF-8 or Excel's Windows encoding. Row numbers in every report are the line numbers of your file.

| Column | Meaning | Rule |
|---|---|---|
| Country | place name | required (CAT2) |
| **DCID** | the Data Commons place id of the country, e.g. `country/KEN`, `country/NGA`, never the country name | required (CAT2); must look like `country/XXX` (CAT3) |
| Domain, Indicator | topic and what is measured. Dataset name = `Indicator (Country)` | required (CAT2) |
| Dataset Description | `description` | required; **50 to 5000 characters** (C2) |
| Creator, Creator URL | the original publishing organisation, name and full URL | required; full http(s) URL (C5) |
| URL/Source Metadata URL | `url`: the dataset's landing page | required; full http(s) URL (C3) |
| distribution.contentURL | `contentUrl`: a real file or data API, **never a landing page** | required; full http(s) URL (C6) |
| distribution.encodingformat | the format: CSV, XLS, XLSX, JSON, API... | required; "Web based" alone means a web page (C6) |
| Geo Level | place level (National, ...) | required (CAT2) |
| License, License URL | licence stated by the publisher, and the link to its terms | required; full http(s) URL, **never guessed** (C4) |
| datePublished | publisher's release date | optional: `N/A` uses the refresh date and is reported |
| Temporal Coverage, Update Frequency | coverage years, frequency | recommended: a gap is a warning |
| Region, Comments | notes | not published |

Several links in one cell are all checked. Browser `data:` links, text and relative paths are rejected.
N2: the same country, indicator and data link twice: the first row is kept, the repeat is rejected with its line number.

## How a row becomes Source → Dataset → Provenance

This is the Croissant design: Source = `creator`, Dataset = `sc:Dataset`, each Provenance = one `cr:FileObject` +
`cr:RecordSet`. All nodes live in our namespace `DCProject` (V12: nothing outside it is ever written).

| Node | Built from | Properties |
|---|---|---|
| **Source** `DCProjectSource_cat_<creator>` (or a known source in `config/sources.yaml`) | Creator, Creator URL | `typeOf: Source`, `name`, `url` |
| **Dataset** `DCProjectDataset_cat_<id>` | Country, Indicator, description, URL, License URL | `typeOf: Dataset`, `name` (`Indicator (Country)`), `description`, `url`, `license`, `isPartOf` → Source |
| **Provenance** | contentURL, license, dates | `isPartOf` → Dataset, `provenanceCategory: StatisticsProvenance`, `description`, `license`, `downloadUrl` (= contentURL), `lastDataRefreshDate` (= datePublished, else the refresh date), `earliestDataImportDate`, `earliestObservationDate` / `latestObservationDate` from the years in Temporal Coverage |

`<id>` is made from the country code and the indicator and stays the same while those do. The place DCID in the sheet
is checked, never rewritten. `/croissant/<dcid>` and the JSON-LD on `/browser/<dcid>` are generated from these nodes
(Croissant 1.1, `conformsTo http://mlcommons.org/croissant/1.1`; the original publisher is the `creator`, this instance is
the `publisher`), never written by hand. A catalogue row carries no observation values, so the site shows the dataset and
its metadata; statistical variables appear when values are loaded from a data link in the template format.

## Finding the real download (CAT-RESOLVE)

If a row's data link is a web page or an inline `data:` link, the pipeline does not reject it at once. It looks only at
the listed page and its documented data API, in this order, and keeps the first candidate that really returns data (CSV,
XLS(X), JSON, JSON-stat; never HTML):

1. an inline `data:` link (decoded and saved under `pipeline/site/static/catalogue_data/`)
2. the Data Browser hub API (`POST .../databrowserhub/api/Core/nodes/<n>/datasets/<flow>/data` with `[]`)
3. **Knoema / Open Data for Africa** (`*.opendataforafrica.org`, `*.knoema.com`): the dataset id and `accesskey` are read
   from the page link and the site's pivot API (`POST /api/1.0/data/pivot/`) is used. This avoids the page, which blocks scripted access
4. Our World in Data `.csv`, FRED series `.csv`
5. CKAN `package_show` resources (HDX, openAFRICA)
6. file or API links found on the page

Links found by hand in a browser go in `pipeline/config/data_links.yaml` (key: the page link or `Country|Indicator`;
fields `download_url`, `format`, `method`, `request`). Results are cached in `imports/local_store/catalogue/resolved.json`
(a failed lookup is retried after 7 days). If nothing is found the row is rejected under C6. The licence still comes
from the sheet.

Known limits: a Knoema dataset the API calls "very large" needs a link filtered to the one indicator; a Knoema dataset
that says "doesn't exist or you don't have access" needs an `accesskey=` in the link; pages that only show an HTML
table, a chart or a PDF are not a download.

## The failures list

Each run writes, per input file, to `imports/data/outbox/`:

- **`<file> - failures to fix.xlsx`**: tab **To fix**: tab, line in file, country, indicator, **column**, **rule
  broken**, what is wrong, current value, **fix needed**, data link. Other tabs: Summary by rule, Warnings (loaded
  rows), Run. Filter by country or rule and hand the lines to whoever owns the sheet.
- `<file> - checked.xlsx` (`.xlsx` input): your sheet unchanged, plus columns Pipeline status (Loaded / Not loaded),
  Pipeline reason, the original link, how the download link was found and the download request.

`pipeline/logs/catalogue.log` has the same detail as text, and each run is kept in `pipeline/logs/history/`.

| Code | What it means |
|---|---|
| CAT2 | a required column is empty |
| CAT3 | the DCID is not a place id like `country/KEN` |
| C2 | description is not 50-5000 characters |
| C3 / C4 / C5 / C6 | Source URL / License URL / Creator URL / data link is not a full http(s) URL, or the data link is a web page |
| N2 | duplicate row (same country, indicator, data link) |

## Checking a provenance sheet

A provenance sheet (`Provenance_id`, `Dataset DCID`, `Source DCID`, `Source URL`, `SourceDataUrl/Distribution.contentUrl`,
`License Type`, `License URL`, ...) is a different layout from the catalogue: it is **checked**, not loaded.

```bash
docker compose --profile jobs run --rm -T pipeline python pipeline/src/provenance_check.py "imports/data/inbox/files/<file>.xlsx"
```

It probes every link (a few thousand take about 15 minutes) and writes `imports/data/outbox/<file> - validation.xlsx`
(tab **To fix**: row, Provenance_id, column, check, severity, finding, current value, recommended correction). Checks:
URL reachable; data link is a file or API, not a page or PDF; Source URL is the dataset's landing page, not a homepage;
License Type is the standard `dcid:Name` form; License URL is a licence page, not a privacy or contact page. A site that
blocks automated requests is a warning to check in a browser. Licences are never suggested, only where to find them.

## Values sheets (the DataCommon template)

Sheets that carry the numbers themselves still work: fill in `imports/templates/DataCommon_dataset_template.xlsx`
(tab **About**: dataset name, description, source, licence; tab **Data**: `Place | Date | Variable | Value | Unit`;
tab **Variables**, optional), put it in `imports/data/inbox/files/` or share a Google Sheet ("Anyone with the link:
Viewer") and paste its link in `imports/data/inbox/links.txt`. Try it with `bash pipeline/scripts/check-feed.sh <id>`.
A sheet may also be listed in `pipeline/config/sheets.json` (`{"id", "link", "enabled"}`; needed only to switch one
off, or for `registry` / `custom` kinds, which need `config/registries/<id>.yaml` or `config/feeds/<id>.yaml`).

- **No duplicates (N1-N4):** the same link is loaded once; the same dataset name and organisation once. A variable named
  exactly like a Data Commons variable uses it (`Total population` becomes `Count_Person`); the Variables tab column
  **Data Commons variable** overrides it (an id, or `new`). The decision is kept in
  `imports/local_store/sheets/<id>/dc_variables.json`. Other variables are created as `DCProject_...`.
- **Data rules (E1-E9, V10, V12):** unreachable source keeps the last good version (E1); no data, missing columns,
  unknown places, non-numbers, values outside min/max, two values for one variable+place+date, dates outside
  `earliest_year..today`, more than 20% of rows vanishing, undefined schema nodes, nodes outside `DCProject`. Never widen
  a min/max to let a value through: a reviewed exception goes in `exclude:` with a reason.
- **Places:** `pipeline/config/places.yaml` aliases → built-in gazetteer → official children in Data Commons → the
  resolve API (cached in `imports/local_store/state/place_cache.json`).

## How a refresh works

All containers belong to the compose project **`dcproject`**.

```
dcproject-scheduler            daily at REFRESH_AT_UTC (21:00 UTC = 00:00 Nairobi) / start-up / refresh trigger
 ├─ dcproject-sync-registries  download every registry workbook
 ├─ dcproject-data-<feed>      one per values sheet: fetch -> map -> validate -> publish (or keep the last good version)
 ├─ dcproject-finalize         shared schema, then catalog.py (catalogue rows, resolve, failures list),
 │                             build_local_dc, place index, site catalogue, homepage -> work/local_dc_status.txt
 ├─ loader                     only if CHANGED: load into local_dc_db_next/
 └─ dcproject-website-1        swapped to the new database only after "Runner finished successfully"
```

`pipeline/logs/pipeline.log` has a header, five numbered steps (read the links; check each dataset; combine and update
the website files; load the database; website), then a summary with NEEDS YOUR ATTENTION. Marks: `✓` fine, `✗` failed,
`!` warning, `i` note, `-` skipped. If a refresh fails (datasets cannot be combined, database cannot be built, API key
missing, or half the sources unreachable), the reason goes to `pipeline/logs/ALERT.txt` and `alerts.log`; add
`ALERT_WEBHOOK_URL=<address>` to `env.list` for a chat message. Rows that fail a rule are not alerts: they are in the
failures list.

Do not replace `pipeline/docker/scheduler.sh` or restart the scheduler while a refresh is running.

## Run it on a new machine

**You need:** Linux (Ubuntu 22.04+ tested) or WSL2, **Docker Engine** with the Compose plugin, git, about **15 GB**
free disk and **8 GB RAM**, internet access, and a free **Data Commons API key** from https://apikeys.datacommons.org.

```bash
curl -fsSL https://get.docker.com | sudo sh        # Docker Engine (skip if `docker compose version` works)
sudo usermod -aG docker "$USER"                    # then log out and back in
git clone https://github.com/dennisngugi-wq/data-common-instance.git
cd data-common-instance
cp env.list.example env.list && nano env.list      # DC_API_KEY=<your key>
cp .env.example .env                               # optional: SITE_PORT, refresh time
bash pipeline/scripts/start.sh                     # pulls images, builds, loads, starts the site
tail -f pipeline/logs/pipeline.log
```

The repository holds code and configuration, not data or the database. Put your catalogue in
`imports/data/inbox/files/` and refresh. If Docker Desktop is installed, opening it switches the `docker` command to its
own engine; `docker context use default` switches back. If the port is taken, set `SITE_PORT` in `.env`.

**GitHub Codespaces:** add the secret `DC_API_KEY` (Settings > Secrets and variables > Codespaces), create a codespace
(4 cores / 16 GB), then make the site port (`SITE_PORT`) public in the Ports tab. It stops after 30 minutes idle.

**Google Cloud:** `bash pipeline/scripts/gcp-deploy.sh` creates one always-on VM with the same Docker setup, a fixed IP
and a port-80 rule, and copies the project and `imports/data/inbox/`; `--status` and `--logs` show the result. Settings:
`GCP_ZONE`, `GCP_MACHINE`, `GCP_DISK_GB`, `GCP_VM`, `SITE_URL` (set it before sharing dataset pages: it goes into the
Croissant metadata).

## Images

`gcr.io/datcom-ci/datacommons-services:stable` and `datacommons-data:stable` (SQLite, runs fully locally). Release 1.1.7
moved to the Spanner-based DCP stack and cannot serve a local folder; the `stable` image has no Croissant code, so
`pipeline/site/app_env/custom.py` backports it.

## Author

Built and maintained by **Dennis Ngugi** ([@dennisngugi-wq](https://github.com/dennisngugi-wq)).
