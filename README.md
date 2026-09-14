# Baruch CS Resume Book

Fill in a spreadsheet, get a one-page PDF in the Baruch College CS Resume Book format.

## Students

1. Download the zip from the **Releases** page on the right and unzip it.
2. Open `INSTRUCTIONS.html` inside the folder. It has the full instructions.
3. Fill in `resume_template_BLANK.csv` or `resume_template_GUIDED.csv`, run the script, submit the PDF and the CSV.

You need Python 3 and Google Chrome or Microsoft Edge. Nothing else to install.

## What is in the folder

| File | What it is |
|---|---|
| `INSTRUCTIONS.html` | Student instructions. Start here. |
| `resume_template_BLANK.csv` | Template with short placeholders. |
| `resume_template_GUIDED.csv` | Template where every value spells out what goes there. |
| `example_resume.csv` and `Smith, John.pdf` | A filled-in example and the PDF it produces. |
| `generate_resume_from_csv.py` | The script. Run it on your CSV. |
| `fonts/` | EB Garamond, bundled so every resume renders the same. |

## Format in one paragraph

The CSV has three columns: Section, Field, Value. Students only type in Value. Sections and fields are a fixed vocabulary (listed in `INSTRUCTIONS.html`). A new job, school, award, or project starts at its Company, Institution, Award, or Project row. Bullets are repeated `Bullet` rows. Sections print in the order they appear in the file, with Education first and Skills last.

## Fonts

EB Garamond is redistributed under the SIL Open Font License; see `fonts/OFL.txt`.
