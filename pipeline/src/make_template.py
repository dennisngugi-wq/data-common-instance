#!/usr/bin/env python3
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

import settings

HEAD = PatternFill("solid", fgColor="0751B3")
HEAD_FONT = Font(bold=True, color="FFFFFF")
REQ = PatternFill("solid", fgColor="E8F0FE")
WRAP = Alignment(wrap_text=True, vertical="top")

ABOUT = [
    ("Dataset name", "required", "Official title of the dataset, as the publisher names it."),
    ("Description", "required", "50 to 5000 characters: what is measured, for which places and years, how it was "
                                "collected and by whom. Shown on the site and in Google Dataset Search."),
    ("Source organisation", "required", "The organisation that publishes the data, e.g. Kenya National Bureau of "
                                        "Statistics. Known names and acronyms (KNBS, ZIMSTAT, ...) are recognised."),
    ("Source website", "required if the organisation is new", "Home page of the organisation, https://..."),
    ("Dataset page", "recommended", "Page where the publisher presents this data, https://... (if empty, the "
                                    "link of this sheet is used)."),
    ("Licence URL", "recommended", "The licence the PUBLISHER states for this data, e.g. "
                                   "https://creativecommons.org/licenses/by/4.0/ . Never guess it: if unknown, leave "
                                   "it empty (the data still loads; its Croissant metadata waits for the licence)."),
    ("Licence evidence", "required with a licence", "Where the publisher states that licence (page URL or document)."),
    ("Citation", "optional", "Only an official citation given by the publisher."),
    ("Place type", "recommended", "What the places in the Data tab are: Country, Province, County, State, Region, "
                                  "District or City. Empty = recognised automatically."),
    ("Country", "recommended for sub-national data", "The country the places are in, e.g. Kenya. Needed to tell "
                                                     "provinces/counties apart from namesakes elsewhere."),
    ("Topic", "optional", "Topic used to group the variables on the site, e.g. Agriculture."),
    ("Includes projections", "optional", "yes if the data contains future years (projections)."),
]


def build(path):
    wb = openpyxl.Workbook()
    how = wb.active
    how.title = "How to fill"
    lines = [
        ("DataCommon dataset sheet", True),
        ("One workbook = one dataset. Fill in the tabs About and Data (Variables is optional), then add the link "
         "of this sheet to config/sheets.json with enabled: true.", False),
        ("", False),
        ("About", True),
        ("Column B holds the answers. Do not change the names in column A.", False),
        ("", False),
        ("Data: one row per number (keep these column names)", True),
        ("Place     the place name (Kenya, Nairobi, Bulawayo Province), ISO3 code (KEN) or Data Commons id "
         "(country/KEN)", False),
        ("Date      year (2023), month (2023-06) or day (2023-06-30)", False),
        ("Variable  what the number measures, e.g. 'Maize production' (same text for the same measure)", False),
        ("Value     the number only: no units, no footnote marks; leave empty when there is no value", False),
        ("Unit      optional, e.g. Tonnes, Percent, KES (one unit per variable)", False),
        ("Instead of Variable + Value you may also use one column per variable: Place | Date | Maize | Beans ...",
         False),
        ("", False),
        ("Variables (optional)", True),
        ("One row per variable of the Data tab (same text in column Variable): a description shown on the site, "
         "the unit, and Min / Max: a value outside them stops the dataset from loading (the check against typing "
         "errors). Leave Min / Max empty when unsure. "
         "Data Commons variable: leave empty and a variable whose name is exactly the name of a Data Commons variable "
         "uses that one (no duplicate); write its id (e.g. Count_Person) to choose it yourself, or 'new' to keep "
         "your own.", False),
        ("", False),
        ("Every value is checked before it is published; a sheet with a problem keeps its last good version on the "
         "site and the run report says which cell to fix.", False),
    ]
    for text, bold in lines:
        how.append([text])
        if bold:
            how.cell(how.max_row, 1).font = Font(bold=True, size=13 if how.max_row == 1 else 11)
    how.column_dimensions["A"].width = 120
    for row in how.iter_rows():
        row[0].alignment = Alignment(wrap_text=True)

    about = wb.create_sheet("About")
    about.append(["Field", "Value", "Required?", "What to write"])
    for name, req, hint in ABOUT:
        about.append([name, "", req, hint])
        if req == "required":
            about.cell(about.max_row, 2).fill = REQ
    for c, w in zip("ABCD", (24, 70, 26, 90)):
        about.column_dimensions[c].width = w
    for row in about.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = WRAP

    data = wb.create_sheet("Data")
    data.append(["Place", "Date", "Variable", "Value", "Unit"])
    for c, w in zip("ABCDE", (28, 12, 44, 16, 16)):
        data.column_dimensions[c].width = w
    data.freeze_panes = "A2"

    var = wb.create_sheet("Variables")
    var.append(["Variable", "Description", "Unit", "Min", "Max", "Data Commons variable"])
    for c, w in zip("ABCDEF", (44, 80, 16, 12, 16, 40)):
        var.column_dimensions[c].width = w
    var.freeze_panes = "A2"

    for ws in (about, data, var):
        for cell in ws[1]:
            cell.fill, cell.font = HEAD, HEAD_FONT
    dv = DataValidation(type="list", formula1='"Country,Province,County,State,Region,District,City"',
                        allow_blank=True)
    about.add_data_validation(dv)
    dv.add(f"B{[n for n, *_ in ABOUT].index('Place type') + 2}")
    wb.active = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    build(settings.PROJECT_ROOT / "imports" / "templates" / "DataCommon_dataset_template.xlsx")
    sys.exit(0)
