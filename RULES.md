# Pipeline rules

Every dataset must pass **all** of these rules before it is loaded. A dataset that fails any rule is **not in the
system at all**: not in the database, the search, the Data sources page, the dataset pages or the homepage. Its
downloaded copy is kept in `local_store/`, so it loads automatically on the next refresh after the problem is fixed
on its sheet. The rule code (C1, E5, ...) is what `work/pipeline.log` prints next to a failed dataset.

Source of the Croissant rules: `mcp agent/knowledge/originals/Croissant Metadata Integration In Data Commons
(go_dtulsyan-intern-design-doc).docx` (Croissant 1.1, MLCommons / schema.org). Code: `automation/src/croissant.py`.

## 1. Where data may come from

| Rule | |
|---|---|
| D1 | Only sheets you provide: Google Sheets linked in `data/inbox/links.txt` or `config/sheets.json`, or Excel files put in `data/inbox/files/`. The pipeline never transcribes reports or invents values, and never crawls sites on its own. One exception, authorised by the client: when a catalogue row's data link is a web page (or an inline `data:` link), the pipeline looks only at that listed page and its documented data API to find the real file or API download (rule CAT-RESOLVE). |
| D2 | A sheet missing something (licence, places, units) is reported; nothing is filled in by guessing. |

## 2. Croissant metadata (required, from the design doc)

Data Commons hierarchy: **Source → Dataset → Provenance**. Source = Croissant `creator`; Dataset = the `sc:Dataset`;
each Provenance = one `cr:FileObject` + `cr:RecordSet`.

| Rule | Croissant property | Requirement (as written in the doc) | Where it comes from |
|---|---|---|---|
| C1 | `name` | Official dataset title; do not rename for style. | About tab: dataset name |
| C2 | `description` | Factual, source-supported; **50 to 5000 characters**. No placeholder text. | About tab: description |
| C3 | `url` | Canonical dataset landing page (a web page, not a file). | About tab: dataset URL |
| C4 | `license` | Licence **explicitly applicable to the dataset**, as the canonical licence URL (prefer SPDX / Creative Commons / ODC), plus where the publisher states it. **Never inferred from public access, never guessed.** | About tab: licence URL + licence evidence |
| C5 | `creator` | The original publishing organisation (name + website). | About tab: source organisation, `config/sources.yaml` |
| C6 | `distribution.contentUrl` | An actual downloadable file or data-returning API, never a landing page. Our published CSV for each dataset, under `public_data_url` (must be a full URL when set). | `config/instance.yaml` |
| — | `datePublished` | Publisher's official release date; our last refresh date only as a fallback approved by Data Commons. | About tab / refresh date |
| — | `conformsTo` | `http://mlcommons.org/croissant/1.1` | fixed |
| — | `citeAs` | Only an official citation (BibTeX / DOI). Never written by us. Optional. | About tab: citation |
| — | `publisher` | This Data Commons instance (the aggregator); the original publisher stays the `creator`. | fixed |

## 3. The data itself

| Rule | Meaning |
|---|---|
| E1 | Source unreachable or unreadable: the last good version stays, retried next refresh. |
| E2 | No data rows. |
| E3 | Required columns missing (place, date, value). |
| E4 | Unknown place: every place must match a Data Commons place. |
| E5 | A value that is not a number, or a date that cannot be read. |
| E6 | A value outside the variable's min/max. Never widen min/max to let a value through; a reviewed exception goes in `exclude:` with a reason. |
| E7 | Two different values for the same variable, place and date. |
| E8 | A date before `earliest_year` or in the future (projections must be marked as such). |
| E9 | More than 20% of the published rows vanish (protects against an emptied sheet or a cut-off download). |

## 4. Schema safety

| Rule | Meaning |
|---|---|
| V10 | Every node the schema refers to is defined. |
| V12 | Nothing is ever written outside our namespace (`DCProject...`); existing Data Commons nodes are referenced, never redefined. |

## 5. No duplicates

| Rule | Meaning |
|---|---|
| N1 | A link or file is loaded once; a second entry with the same link is reported and ignored. |
| N2 | A dataset (same name and same organisation) is loaded once; the second sheet is reported and not loaded. |
| N3 | A variable whose name is exactly the name of a Data Commons variable uses that variable (referenced, never redefined: V12). The Variables tab column "Data Commons variable" overrides it: an id to choose the variable, `new` to keep a separate one. Two variables of one sheet cannot be the same Data Commons variable. |
| N4 | A decision under N3 is kept, so a variable's id never changes by itself. If Data Commons cannot be reached for a new variable, the sheet waits for the next refresh instead of creating a copy. |

## 6. Catalogue sheets (the standard Excel format)

Every Excel file in `data/inbox/files/` looks like `Country × Indicator Dataset Exploration - Final Output.xlsx`: one
row per dataset (country x indicator), on any tab that has the columns below. Tabs without them (e.g. `Tracker`) are
ignored. Code: `automation/src/catalog.py` (`python3 src/catalog.py`). Result per file: `local_store/catalogue/<file>/`
(`datasets.json` = accepted rows, `rejected.json` = rows with the rule that failed and the line number).

| Column | Maps to | Rule |
|---|---|---|
| Country, DCID | place | CAT2 required. CAT3: DCID is a Data Commons place id (`country/KEN`). |
| Domain, Indicator | topic, dataset name | CAT2 required. Dataset name = `Indicator (Country)`. |
| Dataset Description | `description` | CAT2 required; C2: 50-5000 characters. |
| Creator, Creator URl | `creator` | CAT2 required; C5: the original publisher, name and full URL. |
| URL/Source Metadata URL | `url` | CAT2 required; C3: full http(s) landing page. |
| distribution.contentURL | `distribution.contentUrl` | CAT2 required; C6: full http(s) link to a file or data API. Browser `data:` links, text or relative paths are rejected. Several links in one cell are all checked. |
| distribution.encodingformat | `encodingFormat` | CAT2 required. C6: it must name a file or API format (CSV, XLS, JSON, API...); "Web based" alone means the link is a web page, so the row is rejected. |
| License, License URL | `license` | CAT2 required; C4: licence URL is a full http(s) URL, stated by the publisher. Never guessed or filled in by us. |
| Geo Level | place level | CAT2 required. |
| datePublished | `datePublished` | Optional. `N/A` / `Not Found` is allowed and reported; the refresh date is then used (open point below). |
| Temporal Coverage, Update Frequency | coverage, frequency | Recommended: a missing value is a warning, not a rejection. |
| Region, Comments | notes | Not published. |

| Rule | Meaning |
|---|---|
| N2 | The same country, indicator and data link twice: the first row is kept, the repeat is rejected with its line number. |
| CAT-GRAPH | Each accepted row becomes Source > Dataset > Provenance in the graph, as in the Croissant design doc: Provenance (`isPartOf` Dataset, `provenanceCategory: StatisticsProvenance`, `downloadUrl` = contentURL, `license`, `description`, `lastDataRefreshDate` = datePublished, `earliestDataImportDate`, `earliestObservationDate`/`latestObservationDate` from Temporal Coverage years). `/croissant/<dcid>` and the JSON-LD on `/browser/<dcid>` are generated from these nodes (one `cr:FileObject` + `cr:RecordSet` per provenance), never written by hand. |
| CAT-RESOLVE | A row whose data link is a web page is not rejected at once: `automation/src/resolve.py` looks for the real download in this order: inline `data:` link (decoded and saved as a file served by the site), the portal's data API (Data Browser hub: `POST .../databrowserhub/api/Core/nodes/<n>/datasets/<flow>/data` with `[]`), Our World in Data `.csv`, FRED series `.csv`, CKAN `package_show` resources (HDX, openAFRICA), then file or API links found on the page. Every candidate must return data (CSV, XLS(X), JSON, JSON-stat), never HTML. Links found by hand in a browser are recorded in `config/data_links.yaml` (key: the page link or `Country|Indicator`; fields `download_url`, `format`, `method`, `request`). The result is cached in `local_store/catalogue/resolved.json` (a failed lookup is retried after 7 days). If nothing is found the row is rejected under C6. The licence still comes from the sheet. |
| CAT-GATE | A row failing any rule above is not loaded or shown anywhere. Fix the row in the sheet; it loads at the next run. Nothing is filled in by guessing (D2). |
| CAT-OBS | A catalogue row describes where the data is; it carries no observation values, so E2-E9 apply only when its values are loaded in the DataCommon template format. |

## Open points

- **datePublished**: sheets do not give the publisher's release date yet, so the last refresh date is used. The doc
  allows this only with Data Commons approval; until then these datasets are listed in the log.
- **contentUrl / sha256** (the extended FileObject/RecordSet metadata) are emitted only when `public_data_url` is set
  in `config/instance.yaml`, matching the doc's `show_croissant_extended_feature` flag, which is off by default.
