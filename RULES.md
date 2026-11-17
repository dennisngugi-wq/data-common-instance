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
| D1 | Only sheets you provide: Google Sheets linked in `data/inbox/links.txt` or `config/sheets.json`, or Excel files put in `data/inbox/files/`. The pipeline never scrapes websites, transcribes reports or invents values. |
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

## Open points

- **datePublished**: sheets do not give the publisher's release date yet, so the last refresh date is used. The doc
  allows this only with Data Commons approval; until then these datasets are listed in the log.
- **contentUrl / sha256** (the extended FileObject/RecordSet metadata) are emitted only when `public_data_url` is set
  in `config/instance.yaml`, matching the doc's `show_croissant_extended_feature` flag, which is off by default.
