# Published data (licensed datasets only)

A snapshot, taken on 2026-11-13, of the datasets on this instance whose publisher states an open licence. Each folder
is one Data Commons import exactly as the pipeline publishes it: observations (`<id>.csv`: variable, place, date,
value, unit), provenance (`<id>.mcf`) and the loader configuration (`config.json`). The live instance refreshes them
every night from the source; this copy is not updated automatically.

| Folder | Data | Places, years | Source | Licence |
|---|---|---|---|---|
| `worldbank_population/` | Total population (WDI series SP.POP.TOTL), midyear estimates | 216 places, 1960-2025 | World Bank, World Development Indicators ([catalogue](https://datacatalog.worldbank.org/search/dataset/0037712/World-Development-Indicators)) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| `owid_co2_per_capita/` | Annual CO2 emissions per person from fossil fuels and industry, tonnes | 219 places, 1750-2025 | Our World in Data ([chart](https://ourworldindata.org/grapher/co-emissions-per-capita)) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

## Attribution (required by CC BY 4.0)

- **World Bank:** The World Bank, World Development Indicators, Population, total (SP.POP.TOTL).
- **Our World in Data:** Global Carbon Budget (2026); Population based on various sources (2024), with major
  processing by Our World in Data.

Places are Data Commons ids (`country/KEN`, `Earth`, ...). Values are as published by the source; this instance only
reshapes them (one reviewed exclusion in the OWID data: Sint Maarten 1956, an outlier of 1,062 t per person).

The other datasets on the instance (ZIMSTAT, INSP) are not included because their publishers have not stated a
licence; they are rebuilt from the sources when the instance starts.
