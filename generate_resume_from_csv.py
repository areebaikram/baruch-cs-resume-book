#!/usr/bin/env python3
"""
Baruch CS Resume Book generator.

Reads a CSV (Section, Field, Value), writes an HTML file that matches
the official CS resume-book template, and (if Chrome or Edge is installed)
prints it to "LastName, FirstName.pdf" with no dialog, so every student's PDF
has identical fonts, margins, and page size. Exit code 2 means the resume ran
past one page.

Usage:
    python3 generate_resume_from_csv.py YOUR_NAME_resume.csv
"""

import base64
import csv
import html
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import webbrowser
from collections import defaultdict

VERSION = "4.0 (Fall 2026)"

# --------------------------------------------------------------------------- #
# CSV reading
# --------------------------------------------------------------------------- #

# Canonical field names, keyed by lowercase. Lets students type "bullet1",
# "Bullet 1", "GITHUB", etc. without breaking anything.
FIELD_ALIASES = {
    "name": "Name", "email": "Email", "phone": "Phone",
    "last name": "LastName", "lastname": "LastName", "surname": "LastName",
    "linkedin": "LinkedIn", "github": "GitHub", "website": "Website",
    "portfolio": "Website", "url": "Website", "link": "Website", "repo": "Website",
    "repository": "Website",
    "institution": "Institution", "school": "Institution",
    "location": "Location", "degree": "Degree", "minor": "Minor",
    "dates": "Dates", "date": "Dates", "gpa": "GPA",
    "coursework": "Coursework", "relevant coursework": "Coursework", "courses": "Coursework",
    "honors": "Honors",
    "award": "Award", "details": "Details",
    "company": "Company", "organization": "Company", "employer": "Company",
    "title": "Title", "role": "Title", "position": "Title",
    "project": "Company", "technologies": "Title", "tech": "Title",
    "skills": "Skills", "technical": "Skills", "technical skills": "Skills",
    "certifications": "Certifications", "strengths": "Strengths",
    "interests": "Interests", "languages": "Languages",
    "venue": "Venue", "conference": "Venue", "journal": "Venue", "authors": "Authors",
}

SECTION_ALIASES = {
    "contact": "CONTACT",
    "education": "EDUCATION",
    "awards": "AWARDS", "award": "AWARDS", "honors": "AWARDS",
    "experience": "EXPERIENCE", "work": "EXPERIENCE",
    "leadership": "LEADERSHIP", "activities": "LEADERSHIP",
    "projects": "PROJECTS", "project": "PROJECTS",
    "research": "RESEARCH", "research experience": "RESEARCH",
    "publications": "PUBLICATIONS", "publication": "PUBLICATIONS",
    "skills": "SKILLS",
}

VALID_SECTIONS = "CONTACT, EDUCATION, AWARDS, EXPERIENCE, RESEARCH, PUBLICATIONS, LEADERSHIP, PROJECTS, SKILLS"

# Which canonical fields each section accepts. Anything else is a warning.
# (Bullet1, Bullet2, ... are allowed wherever "Bullet" is listed.)
SECTION_FIELDS = {
    "CONTACT":      {"Name", "LastName", "Phone", "Email", "LinkedIn", "GitHub", "Website"},
    "EDUCATION":    {"Institution", "Location", "Degree", "Minor", "Dates", "GPA", "Coursework", "Honors", "Bullet"},
    "AWARDS":       {"Award", "Dates", "Details"},
    "EXPERIENCE":   {"Company", "Location", "Title", "Dates", "Bullet"},
    "RESEARCH":     {"Company", "Location", "Title", "Dates", "Bullet"},
    "PUBLICATIONS": {"Title", "Venue", "Authors", "Dates"},
    "LEADERSHIP":   {"Company", "Location", "Title", "Dates", "Bullet"},
    "PROJECTS":     {"Company", "Title", "Location", "Website", "Dates", "Bullet"},
    "SKILLS":       {"Skills", "Certifications", "Languages", "Strengths", "Interests"},
}
# Student-facing spelling of the fields, for warning messages.
FIELD_NAMES_SHOWN = {
    "CONTACT":      "Name, Phone, Email, LinkedIn, GitHub, Website, Last Name",
    "EDUCATION":    "Institution, Location, Degree, Minor, Dates, GPA, Coursework, Honors",
    "AWARDS":       "Award, Dates, Details",
    "EXPERIENCE":   "Company, Location, Title, Dates, Bullet (one row per bullet)",
    "RESEARCH":     "Company, Location, Title, Dates, Bullet (one row per bullet)",
    "PUBLICATIONS": "Title, Venue, Authors, Dates",
    "LEADERSHIP":   "Organization, Location, Title, Dates, Bullet (one row per bullet)",
    "PROJECTS":     "Project, Technologies, Website, Dates, Bullet (one row per bullet)",
    "SKILLS":       "Skills, Certifications, Interests",
}
# The field that begins a new block (a new job, school, award, ...) in each section.
LEADING_FIELD = {
    "EDUCATION": "Institution", "AWARDS": "Award", "EXPERIENCE": "Company", "RESEARCH": "Company",
    "LEADERSHIP": "Company", "PROJECTS": "Company", "PUBLICATIONS": "Title",
}
LEADING_SHOWN = {
    "EDUCATION": "Institution", "AWARDS": "Award", "EXPERIENCE": "Company", "RESEARCH": "Company",
    "LEADERSHIP": "Organization", "PROJECTS": "Project", "PUBLICATIONS": "Title",
}
# Sections whose position on the page follows their position in the CSV.
MOVABLE = ("AWARDS", "EXPERIENCE", "RESEARCH", "PUBLICATIONS", "LEADERSHIP", "PROJECTS")
LIST_SECTIONS = ("education", "awards", "experience", "research", "publications", "leadership", "projects")
WORK_LIKE = ("experience", "research", "leadership", "projects")


def canonical_field(field):
    """Return the canonical field name, or None if the field is not recognized."""
    key = re.sub(r"\s+", " ", field.strip().lower())
    if key in FIELD_ALIASES:
        return FIELD_ALIASES[key]
    if re.match(r"bullets?[\s._-]*\d*$", key):
        return "Bullet"
    return None


class ResumeData:
    def __init__(self):
        self.contact = {}
        self.education = defaultdict(dict)
        self.awards = defaultdict(dict)
        self.experience = defaultdict(dict)
        self.leadership = defaultdict(dict)
        self.projects = defaultdict(dict)
        self.research = defaultdict(dict)
        self.publications = defaultdict(dict)
        self.skills = {}
        self.order = []         # sections in order of first appearance in the CSV
        self.current = {}       # section -> key of the block currently being filled
        self.problems = []      # parse-time warnings, shown with the others
        self.lines = {}         # (section, entry, field) -> CSV line number

    def add(self, section, field, value, line_no):
        raw_section = section.strip()
        section = SECTION_ALIASES.get(raw_section.lower())
        if section is None:
            self.problems.append(
                f'Line {line_no}: unknown section "{raw_section}" was ignored. Valid sections: '
                f"{VALID_SECTIONS}.")
            return
        canon = canonical_field(field)
        base = "Bullet" if canon and canon.startswith("Bullet") else canon
        if canon is None or base not in SECTION_FIELDS[section]:
            self.problems.append(
                f'Line {line_no}: "{field.strip()}" is not a valid field in {section}, so the row was ignored. '
                f"Fields allowed in {section}: {FIELD_NAMES_SHOWN[section]}.")
            return
        value = value.strip()
        if section not in self.order:
            self.order.append(section)
        if section in ("CONTACT", "SKILLS"):
            entry = "1"
            target = self.contact if section == "CONTACT" else self.skills
        else:
            blocks = getattr(self, section.lower())
            # A new block starts at each leading field (Company, Institution, ...).
            if canon == LEADING_FIELD[section] or section not in self.current:
                entry = str(len(blocks) + 1)
                self.current[section] = entry
            entry = self.current[section]
            target = blocks[entry]
        if canon == "Bullet":
            canon = "Bullet" + str(sum(1 for k in target if k.startswith("Bullet")) + 1)
        if canon in target:
            hint = ""
            if section in LEADING_FIELD:
                hint = (f" A new {section.lower()} item must begin with its "
                        f"{LEADING_SHOWN[section]} row.")
            self.problems.append(
                f'Line {line_no}: {section} already has a "{canon}" value (line '
                f"{self.lines.get((section, entry, canon), '?')}), so this row REPLACED it.{hint}")
        target[canon] = value
        self.lines[(section, entry, canon)] = line_no

    def line(self, section, entry, field):
        n = self.lines.get((section, entry, field))
        return f"Line {n}: " if n else ""


def read_csv_rows(path):
    """Read the CSV, tolerating Excel's BOM, Windows encoding, and ; delimiters."""
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        head = f.read(4096)
    if ext in (".xlsx", ".xls", ".numbers", ".ods") or b"\x00" in head or head.startswith(b"PK"):
        raise ValueError(
            "This is not a CSV file (it looks like an Excel or Numbers file). "
            "Save it again choosing the CSV UTF-8 format, then run the script on the .csv file.")
    raw = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                raw = f.read()
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raise ValueError("Could not decode the CSV file.")

    delimiter = ","
    first_line = raw.splitlines()[0] if raw.splitlines() else ""
    if first_line.count(";") > first_line.count(","):
        delimiter = ";"

    reader = csv.reader(io.StringIO(raw, newline=""), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        raise ValueError("The CSV file is empty.")

    header = [h.strip().lower() for h in rows[0]]
    if header[:3] == ["section", "field", "value"]:
        return rows[1:]
    if header[:4] == ["section", "entry", "field", "value"]:
        # Older template with an Entry column: drop it, blocks are inferred anyway.
        return [r[:1] + r[2:] for r in rows[1:]]
    raise ValueError(
        "The first row must be exactly: Section,Field,Value "
        f"(found: {','.join(rows[0])})"
    )


# Characters that appear when a Mac-Roman-encoded file (Excel for Mac,
# plain "CSV" format) is read as Windows-1252: é->Ž  á->‡  ó->—  ñ->–  ü->Ÿ
MOJIBAKE_RE = re.compile(r"[Ž‡Ÿ¸ˆ˜]|\w[–—]\w")


def parse_csv(path):
    data = ResumeData()
    for line_no, row in enumerate(read_csv_rows(path), start=2):
        # Excel pads rows with empty trailing cells; drop them.
        while row and not row[-1].strip():
            row.pop()
        # An unquoted comma inside the Value splits it into extra columns;
        # glue them back together instead of silently dropping text.
        if len(row) > 3:
            row = row[:2] + [",".join(row[2:])]
        row = [c.strip() for c in row] + ["", "", ""]
        section, field, value = row[0], row[1], row[2]
        if not section or not field or not value:
            continue
        # Alt+Enter line breaks inside a cell become spaces.
        value = re.sub(r"\s*[\r\n]+\s*", " ", value)
        data.add(section, field, value, line_no)
        if MOJIBAKE_RE.search(value):
            data.problems.append(
                f'Line {line_no}: "{value}" contains characters that usually mean the file was saved '
                'with the wrong encoding. In Excel use File > Save As > "CSV UTF-8"; in Numbers use '
                "File > Export To > CSV with encoding Unicode (UTF-8).")
    return data


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

PLACEHOLDER_PATTERNS = [
    r"^\[.*\]$",          # anything still wrapped in the template's square brackets
    r"\[[^\]]*\b(your|insert|delete|leave|optional|relevant|soft skills|certifications)\b[^\]]*\]",
    r"\{[^}]*\b(your|insert)\b[^}]*\}",
    r"\bX\.XX\b",
    r"\b20XX\b",
    r"Accomplished X by doing Y",
    r"Did X by doing Y",
    r"\bYour Full Name\b",
    r"\bCompany Name\b",
    r"\bOrganization Name\b",
    r"\bProject Name\b",
    r"\bAward Name\b",
    r"\bJob Title\b",
    r"\bPosition Title\b",
    r"your\.email@",
    r"yourprofile|yourusername",
    r"123-456-7890",
    r"List certifications here",
]

MONTHS = "(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?"
DATE_TOKEN = rf"(?:{MONTHS}\s+\d{{4}}|Present|Current|Expected\s+{MONTHS}\s+\d{{4}}|Expected\s+(Spring|Summer|Fall|Winter)\s+\d{{4}}|\d{{4}})"
DATE_RE = re.compile(rf"^\s*{DATE_TOKEN}(\s*[-–—]\s*{DATE_TOKEN})?\s*$", re.IGNORECASE)


def all_values(data):
    yield from ((data.line("CONTACT", "1", k) + f"CONTACT / {k}", v) for k, v in data.contact.items())
    yield from ((data.line("SKILLS", "1", k) + f"SKILLS / {k}", v) for k, v in data.skills.items())
    for name in LIST_SECTIONS:
        for entry, fields in getattr(data, name).items():
            for k, v in fields.items():
                yield (data.line(name.upper(), entry, k) + f"{name.upper()} {entry} / {k}", v)


def validate(data):
    """Return (errors, warnings). Errors stop generation; warnings block DONE."""
    errors, warnings = [], list(data.problems)

    for f in ("Name", "Email", "Phone"):
        if not data.contact.get(f):
            errors.append(f"CONTACT is missing required field: {f}")
    if not data.education:
        errors.append("No EDUCATION entries found.")
    if not (data.experience or data.research or data.leadership or data.projects):
        errors.append("Need at least one EXPERIENCE, RESEARCH, LEADERSHIP, or PROJECTS entry.")

    for where, value in all_values(data):
        for pat in PLACEHOLDER_PATTERNS:
            if re.search(pat, value, flags=re.IGNORECASE):
                warnings.append(f'{where} still has placeholder text: "{value}"')
                break

    for name in LIST_SECTIONS:
        for entry, fields in getattr(data, name).items():
            d = tidy_dates(fields.get("Dates", ""))
            if d and not DATE_RE.match(d):
                warnings.append(
                    data.line(name.upper(), entry, "Dates") +
                    f'{name.upper()} {entry} Dates "{fields.get("Dates", "")}" is not in the standard format '
                    '"Mon. YYYY – Mon. YYYY" (e.g. "Jun. 2025 – Aug. 2025", '
                    '"Sep. 2024 – Present", "Expected May 2026").'
                )

    for entry, fields in data.education.items():
        gpa = fields.get("GPA", "")
        m = re.search(r"(\d\.\d+)", gpa)
        if m and float(m.group(1)) < 3.4:
            warnings.append(f"EDUCATION {entry} GPA is {m.group(1)}. "
                            "The template says to leave out a GPA below 3.4.")

    for name in WORK_LIKE:
        for entry, fields in getattr(data, name).items():
            if not any(k.startswith("Bullet") for k in fields):
                warnings.append(f"{name.upper()} {entry} ({fields.get('Company', '?')}) has no bullet points.")
            for k in ("Company", "Title", "Dates"):
                if not fields.get(k):
                    label = {"Company": "Company/Organization/Project",
                             "Title": "Title/Technologies", "Dates": "Dates"}[k]
                    warnings.append(f"{name.upper()} {entry} is missing {label}.")
    for entry, fields in data.publications.items():
        for k in ("Title", "Venue", "Dates"):
            if not fields.get(k):
                warnings.append(f"PUBLICATIONS {entry} is missing {k}.")
    for entry, fields in data.awards.items():
        for k in ("Award", "Dates"):
            if not fields.get(k):
                warnings.append(f"AWARDS {entry} is missing {k}.")

    email = data.contact.get("Email", "")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        warnings.append(f'Email "{email}" does not look like a valid address.')

    return errors, warnings


# --------------------------------------------------------------------------- #
# HTML generation
# --------------------------------------------------------------------------- #

def esc(s):
    return html.escape(s, quote=True)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_FACES = [
    ("EBGaramond-Regular.woff2", "normal", 400),
    ("EBGaramond-Bold.woff2", "normal", 700),
    ("EBGaramond-Italic.woff2", "italic", 400),
    ("EBGaramond-BoldItalic.woff2", "italic", 700),
]


class MissingFontError(Exception):
    pass


def embedded_font_css():
    """Inline the bundled EB Garamond files so every machine renders the same font."""
    css = []
    for fname, style, weight in FONT_FACES:
        path = os.path.join(SCRIPT_DIR, "fonts", fname)
        if not os.path.exists(path):
            raise MissingFontError(
                f"Font file not found: {path}\n"
                "The 'fonts' folder must sit next to this script. On Windows, extract the zip "
                "first and run the script from the extracted folder.")
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        css.append(
            "  @font-face { font-family: 'ResumeGaramond'; font-style: %s; font-weight: %d; "
            "src: url(data:font/woff2;base64,%s) format('woff2'); }" % (style, weight, b64)
        )
    return "\n".join(css)


MONTH_ABBR = {
    "jan": "Jan.", "feb": "Feb.", "mar": "Mar.", "apr": "Apr.", "may": "May", "jun": "Jun.",
    "jul": "Jul.", "aug": "Aug.", "sep": "Sep.", "sept": "Sep.", "oct": "Oct.", "nov": "Nov.",
    "dec": "Dec.",
}
MONTH_WORD_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\.?(?=\s|$|-)",
    re.IGNORECASE)


def tidy_dates(s):
    """Normalize dates to the template's 'Mon. YYYY – Mon. YYYY' form."""
    s = s.strip()
    # Excel turns a typed "May 2025" into "May-25"; undo that.
    s = re.sub(r"\b([A-Za-z]{3,9})-(\d{2})\b", lambda m: f"{m.group(1)} 20{m.group(2)}", s)
    # Full or unpunctuated month names -> abbreviations with a period (except May).
    s = MONTH_WORD_RE.sub(lambda m: MONTH_ABBR[m.group(1).lower()], s)
    # Any hyphen/dash between the two halves -> spaced en dash.
    s = re.sub(r"\s*(?:-{1,2}|–|—)\s*", " – ", s)
    s = re.sub(r"\b(present|current|expected)\b", lambda m: m.group(1).capitalize(), s, flags=re.I)
    return s


def tidy_bullet(s):
    """Template bullets have no trailing period; keep them uniform."""
    s = s.strip()
    if s.endswith(".") and not s.endswith(".."):
        s = s[:-1]
    return s


def link_html(value, kind):
    """Turn a URL or email into a real hyperlink (kept when saving to PDF)."""
    value = value.strip()
    if kind == "email":
        return f'<a href="mailto:{esc(value)}">{esc(value)}</a>'
    href = value if re.match(r"^https?://", value, re.I) else "https://" + value
    display = re.sub(r"^https?://(www\.)?", "", value, flags=re.I).rstrip("/")
    return f'<a href="{esc(href)}">{esc(display)}</a>'


def sorted_entries(d):
    def key(item):
        try:
            return (0, int(item[0]))
        except ValueError:
            return (1, item[0])
    return sorted(d.items(), key=key)


def bullets_of(fields):
    out = []
    keys = sorted((k for k in fields if k.startswith("Bullet")),
                  key=lambda k: int(k[6:]) if k[6:].isdigit() else 999)
    for k in keys:
        if fields[k].strip():
            out.append(tidy_bullet(fields[k]))
    return out


def entry_block(left1, right1, left2, right2, bullets, extra_lines=()):
    h = ['<div class="entry">']
    h.append('  <div class="row"><span class="title">%s</span><span class="right">%s</span></div>'
             % (esc(left1), esc(right1)))
    if left2 or right2:
        h.append('  <div class="row"><span class="subtitle">%s</span><span class="right dates">%s</span></div>'
                 % (esc(left2), esc(right2)))
    for line in extra_lines:
        h.append(f'  <div class="line">{line}</div>')
    if bullets:
        h.append("  <ul>")
        for b in bullets:
            h.append(f"    <li>{esc(b)}</li>")
        h.append("  </ul>")
    h.append("</div>")
    return "\n".join(h)


class HTMLGenerator:
    def __init__(self, data):
        self.d = data

    def generate(self):
        parts = [self.head(), '<div class="page">', self.contact()]
        parts.append(self.education())
        # Movable sections print in the order they first appear in the CSV.
        # LEADERSHIP and PROJECTS share one heading, placed where the first of
        # the two appears.
        done = set()
        for sec in self.d.order:
            if sec not in MOVABLE or sec in done:
                continue
            if sec == "AWARDS":
                parts.append(self.awards())
            elif sec == "EXPERIENCE":
                parts.append(self.work("Experience", self.d.experience))
            elif sec == "RESEARCH":
                parts.append(self.work("Research Experience", self.d.research))
            elif sec == "PUBLICATIONS":
                parts.append(self.publications())
            elif sec in ("LEADERSHIP", "PROJECTS"):
                parts.append(self.leadership_and_projects())
                done.update(("LEADERSHIP", "PROJECTS"))
            done.add(sec)
        if any(self.d.skills.values()):
            parts.append(self.skills())
        parts.append('<div class="page-end"></div>')
        parts.append("</div>")
        parts.append(self.tail())
        return "\n".join(parts)

    # ---- sections ------------------------------------------------------- #

    def contact(self):
        c = self.d.contact
        items = []
        if c.get("Phone"):
            items.append(esc(c["Phone"]))
        if c.get("Email"):
            items.append(link_html(c["Email"], "email"))
        for k in ("LinkedIn", "GitHub", "Website"):
            if c.get(k):
                items.append(link_html(c[k], "url"))
        return ('<div class="header">\n'
                f'  <h1>{esc(c.get("Name", ""))}</h1>\n'
                f'  <div class="contact">{" &bull; ".join(items)}</div>\n'
                "</div>")

    def education(self):
        h = ["<h2>Education</h2>"]
        for _, f in sorted_entries(self.d.education):
            degree = f.get("Degree", "")
            if f.get("Minor"):
                degree += f", Minor in {f['Minor']}"
            extra = []
            if f.get("GPA"):
                extra.append(f"GPA: {esc(f['GPA'])}")
            if f.get("Coursework"):
                extra.append(f"Relevant Coursework: {esc(f['Coursework'])}")
            if f.get("Honors"):
                extra.append(esc(f["Honors"]))
            h.append(entry_block(f.get("Institution", ""), f.get("Location", ""),
                                 degree, tidy_dates(f.get("Dates", "")),
                                 bullets_of(f), extra))
        return "\n".join(h)

    def awards(self):
        h = ["<h2>Awards</h2>"]
        for _, f in sorted_entries(self.d.awards):
            h.append('<div class="entry award">')
            h.append('  <div class="row"><span class="title">%s</span><span class="right dates">%s</span></div>'
                     % (esc(f.get("Award", "")), esc(tidy_dates(f.get("Dates", "")))))
            if f.get("Details"):
                h.append(f'  <div class="line">{esc(f["Details"])}</div>')
            h.append("</div>")
        return "\n".join(h)

    def work(self, heading, entries):
        h = [f"<h2>{heading}</h2>"]
        for _, f in sorted_entries(entries):
            h.append(entry_block(f.get("Company", ""), f.get("Location", ""),
                                 f.get("Title", ""), tidy_dates(f.get("Dates", "")),
                                 bullets_of(f)))
        return "\n".join(h)

    def publications(self):
        h = ["<h2>Publications</h2>"]
        for _, f in sorted_entries(self.d.publications):
            h.append('<div class="entry">')
            h.append('  <div class="row"><span class="title">%s</span><span class="right dates">%s</span></div>'
                     % (esc(f.get("Title", "")), esc(tidy_dates(f.get("Dates", "")))))
            sub = esc(f.get("Venue", ""))
            if f.get("Authors"):
                sub += (". " if sub else "") + esc(f["Authors"])
            if sub:
                h.append(f'  <div class="row"><span class="subtitle">{sub}</span></div>')
            h.append("</div>")
        return "\n".join(h)

    def leadership_and_projects(self):
        if self.d.leadership and self.d.projects:
            heading = "Leadership and Projects"
        elif self.d.leadership:
            heading = "Leadership"
        else:
            heading = "Projects"
        h = [f"<h2>{heading}</h2>"]
        for _, f in sorted_entries(self.d.leadership):
            h.append(entry_block(f.get("Company", ""), f.get("Location", ""),
                                 f.get("Title", ""), tidy_dates(f.get("Dates", "")),
                                 bullets_of(f)))
        for _, f in sorted_entries(self.d.projects):
            # Projects: name on the left, link (if any) on the right,
            # technologies in italics where a job title would go.
            right = f.get("Location", "")
            link = f.get("Website", "")
            right_html = esc(right)
            if link:
                right_html = link_html(link, "url")
            h.append('<div class="entry">')
            h.append('  <div class="row"><span class="title">%s</span><span class="right">%s</span></div>'
                     % (esc(f.get("Company", "")), right_html))
            h.append('  <div class="row"><span class="subtitle">%s</span><span class="right dates">%s</span></div>'
                     % (esc(f.get("Title", "")), esc(tidy_dates(f.get("Dates", "")))))
            b = bullets_of(f)
            if b:
                h.append("  <ul>")
                h.extend(f"    <li>{esc(x)}</li>" for x in b)
                h.append("  </ul>")
            h.append("</div>")
        return "\n".join(h)

    def skills(self):
        h = ["<h2>Skills, Interests, and Certifications</h2>"]
        for label in ("Skills", "Certifications", "Languages", "Strengths", "Interests"):
            v = self.d.skills.get(label, "")
            if v:
                h.append(f'<div class="line"><span class="label">{label}:</span> {esc(v)}</div>')
        return "\n".join(h)

    # ---- page chrome ---------------------------------------------------- #

    def head(self):
        name = self.d.contact.get("Name", "Student")
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<!-- The title becomes the default PDF file name when you print. -->
<title>{esc(pdf_filename(self.d.contact)[:-4])}</title>
<style>
{embedded_font_css()}
  /* ---- Page geometry: identical on screen and in print ---------------- */
  @page {{
    size: letter portrait;
    margin: 0;               /* margins live inside .page so every browser agrees */
  }}
  * {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    background: #e9e9e9;
    font-family: "ResumeGaramond", "EB Garamond", Garamond, "Times New Roman", Times, serif;
    font-size: 11pt;
    line-height: 1.2;
    color: #000;
  }}
  .page {{
    position: relative;
    width: 8.5in;
    min-height: 11in;
    margin: 24px auto;
    padding: 0.5in 0.6in 0.45in 0.6in;
    background: #fff;
    box-shadow: 0 2px 12px rgba(0,0,0,0.25);
  }}
  /* Dashed line marks the bottom of page one on screen only. */
  .page-end {{
    position: absolute; left: 0; right: 0; top: calc(11in - 2px);
    border-top: 2px dashed #d33;
  }}
  .page-end::after {{
    content: "end of page 1 – anything below this line will not fit";
    position: absolute; right: 0.3in; bottom: 2px;
    font: 9pt Arial, sans-serif; color: #d33;
  }}
  @media print {{
    body {{ background: #fff; }}
    .page {{ margin: 0; box-shadow: none; min-height: 0; width: auto; }}
    .page-end, .banner {{ display: none !important; }}
  }}

  /* ---- Template styling ------------------------------------------------ */
  .header {{ text-align: center; margin-bottom: 6pt; }}
  .header h1 {{
    font-size: 16pt; font-weight: bold; text-transform: uppercase;
    letter-spacing: 0.5pt; margin: 0 0 2pt 0;
  }}
  .header .contact {{ font-size: 10pt; }}
  a {{ color: #1a56a0; text-decoration: none; }}

  h2 {{
    font-size: 11.5pt; font-weight: bold; text-transform: uppercase;
    margin: 9pt 0 3pt 0; padding-bottom: 1pt;
    border-bottom: 1pt solid #000; letter-spacing: 0.3pt;
  }}
  .entry {{ margin-bottom: 6pt; }}
  .award {{ margin-bottom: 2pt; }}
  .row {{ display: flex; justify-content: space-between; align-items: baseline; }}
  .row .right {{ text-align: right; white-space: nowrap; margin-left: 12pt; }}
  .title {{ font-weight: bold; }}
  .subtitle {{ font-style: italic; }}
  .dates {{ font-style: italic; }}
  .line {{ margin: 0; }}
  .label {{ font-weight: bold; }}
  ul {{ margin: 1pt 0 0 0; padding-left: 16pt; }}
  li {{ margin: 0 0 1pt 0; padding-left: 2pt; }}

  /* ---- On-screen banner (never printed) -------------------------------- */
  .banner {{
    font: 10.5pt/1.45 Arial, sans-serif;
    max-width: 8.5in; margin: 16px auto 0 auto; padding: 12px 16px;
    border-radius: 6px; border: 1px solid #ccc; background: #fff;
  }}
  .banner.ok {{ border-color: #2e7d32; background: #edf7ee; }}
  .banner.bad {{ border-color: #c62828; background: #fdecea; }}
  .banner strong {{ font-size: 11.5pt; }}
  .banner ul {{ margin: 6px 0 0 0; padding-left: 18pt; }}
  .banner li {{ margin: 0; }}
</style>
</head>
<body>
<div class="banner" id="banner"><strong>Checking page length…</strong></div>
"""

    def tail(self):
        return """
<script>
  // Warn if the resume runs past one page. 1in = 96px in every browser.
  function checkLength() {
    var page = document.querySelector('.page');
    var banner = document.getElementById('banner');
    // Bottom edge of the last real content element, plus the page's bottom
    // padding, compared with the 11in page height. Ignores the guide line.
    var pageTop = page.getBoundingClientRect().top;
    var bottom = 0;
    var kids = page.children;
    for (var i = 0; i < kids.length; i++) {
      if (kids[i].classList.contains('page-end')) continue;
      var b = kids[i].getBoundingClientRect().bottom - pageTop;
      if (b > bottom) bottom = b;
    }
    var bottomPad = parseFloat(getComputedStyle(page).paddingBottom);
    var overflow = bottom + bottomPad - 11 * 96;
    var settings =
      '<ul>' +
      '<li>Open this file in <b>Chrome or Edge</b> (Safari and Firefox add their own margins and headers).</li>' +
      '<li>Press <b>Ctrl+P</b> (Windows) or <b>Cmd+P</b> (Mac).</li>' +
      '<li>Destination: <b>Save as PDF</b>.</li>' +
      '<li>Paper size: <b>Letter</b>. Margins: <b>Default</b>. Scale: <b>100%</b>.</li>' +
      '<li>Turn <b>off</b> "Headers and footers". Turn <b>on</b> "Background graphics".</li>' +
      '<li>Keep the suggested file name (<i>LastName, FirstName.pdf</i>).</li>' +
      '</ul>';
    if (overflow > 2) {
      banner.className = 'banner bad';
      banner.innerHTML = '<strong>Too long: your resume runs about ' +
        Math.round(overflow / 96 * 100) / 100 +
        ' inches past one page.</strong> Shorten or remove bullets in your CSV, ' +
        'run the script again, and reload this page. Everything below the red dashed line will be cut off. ' +
        'Once it fits, save it as a PDF:' + settings;
    } else {
      banner.className = 'banner ok';
      banner.innerHTML = '<strong>Fits on one page.</strong> Save it as a PDF:' + settings;
    }
  }
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(checkLength);
  } else {
    window.addEventListener('load', checkLength);
  }
</script>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# PDF generation via Chrome / Edge (headless, no print dialog)
# --------------------------------------------------------------------------- #

def find_browser():
    """Return the path to a Chrome/Edge/Chromium executable, or None."""
    candidates = []
    if sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
    elif sys.platform.startswith("win"):
        roots = [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                 os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")]
        for r in roots:
            if r:
                candidates += [
                    os.path.join(r, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(r, "Microsoft", "Edge", "Application", "msedge.exe"),
                    os.path.join(r, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                ]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                 "microsoft-edge", "msedge", "chrome"):
        w = shutil.which(name)
        if w:
            candidates.append(w)
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def html_to_pdf(browser, html_path, pdf_path):
    """Print html_path to pdf_path with a headless browser.

    Returns (ok, error_text). A stale PDF from an earlier run is removed first
    so a failed print can never be mistaken for a fresh one.
    """
    if os.path.exists(pdf_path):
        try:
            os.remove(pdf_path)
        except OSError:
            return False, (f"Could not overwrite {pdf_path}. "
                           "Close it if it is open in a PDF viewer and run again.")
    url = pathlib.Path(html_path).resolve().as_uri()
    last_err = ""
    for headless_flag in ("--headless=new", "--headless"):
        cmd = [browser, headless_flag, "--disable-gpu", "--no-pdf-header-footer",
               "--run-all-compositor-stages-before-draw", "--virtual-time-budget=2000",
               f"--print-to-pdf={os.path.abspath(pdf_path)}", url]
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)
            last_err = r.stderr.decode("utf-8", "replace").strip()[-600:]
        except subprocess.TimeoutExpired:
            last_err = "The browser did not finish within 90 seconds."
            continue
        except OSError as e:
            last_err = str(e)
            continue
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            return True, ""
    return False, last_err


def pdf_page_count(pdf_path):
    with open(pdf_path, "rb") as f:
        raw = f.read()
    n = len(re.findall(rb"/Type\s*/Page(?![s\w])", raw))
    return n if n > 0 else None


def pdf_filename(contact):
    """'Smith, John.pdf' so a folder of resumes sorts by last name."""
    name = contact.get("Name", "Resume").strip()
    last = contact.get("LastName", "").strip()
    if last:
        first = re.sub(r"\s*" + re.escape(last) + r"\s*$", "", name, flags=re.I).strip()
    else:
        parts = name.split()
        first, last = " ".join(parts[:-1]), (parts[-1] if parts else "Resume")
    clean = lambda t: re.sub(r"[^\w\s.'-]", "", t).strip()
    return (f"{clean(last)}, {clean(first)}" if first else clean(last)) + ".pdf"


def open_file(path):
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path])
        elif sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", path])
    except Exception:  # noqa: BLE001
        webbrowser.open("file://" + os.path.abspath(path))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_resume_from_csv.py YOUR_NAME_resume.csv")
        sys.exit(1)

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: python3 generate_resume_from_csv.py YOUR_NAME_resume.csv")
        sys.exit(1)
    csv_path = args[0]
    if not os.path.exists(csv_path) and len(args) > 1 and os.path.exists(" ".join(args)):
        csv_path = " ".join(args)   # unquoted name with spaces
    html_path = os.path.splitext(csv_path)[0] + ".html"

    if not os.path.exists(csv_path):
        print(f"ERROR: file not found: {csv_path}")
        print("Make sure you are in the folder that contains your CSV, and put the file name in "
              'quotes if it has spaces: "My Name resume.csv"')
        sys.exit(1)

    print(f"Reading {csv_path}")
    try:
        data = parse_csv(csv_path)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR reading CSV: {e}")
        sys.exit(1)

    errors, warnings = validate(data)
    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")
    if errors:
        print("\nERRORS (resume not generated):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    try:
        content = HTMLGenerator(data).generate()
    except MissingFontError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"\nWrote {html_path}")

    quiet = "--no-open" in sys.argv
    pdf_name = pdf_filename(data.contact)
    pdf_path = os.path.join(os.path.dirname(os.path.abspath(csv_path)), pdf_name)
    html_url = pathlib.Path(html_path).resolve().as_uri()

    browser = find_browser()
    if browser is None:
        print("\nChrome or Edge was not found, so the PDF was not created automatically.")
        print("Open the HTML file in Chrome or Edge and follow the instructions in the box "
              "at the top of the page to save it as a PDF.")
        if not quiet:
            webbrowser.open(html_url)
        sys.exit(3 if warnings else 0)

    ok, err = html_to_pdf(browser, html_path, pdf_path)
    if not ok:
        print(f"\nThe browser ({browser}) was found but did not produce a PDF.")
        if err:
            print("Browser output:\n  " + err.replace("\n", "\n  "))
        print("Try again. If it keeps failing, open the HTML file in Chrome or Edge and follow "
              "the instructions in the box at the top of the page to save it as a PDF.")
        if not quiet:
            webbrowser.open(html_url)
        sys.exit(3)

    pages = pdf_page_count(pdf_path)
    print(f"Wrote {pdf_path}")
    if pages is None:
        print("\nCould not verify the page count automatically. Open the PDF and confirm it is "
              "exactly one page.")
    elif pages != 1:
        print(f"\nNOT DONE: the PDF is {pages} pages long. Resumes must be exactly one page.")
        print("Shorten or remove bullets in your CSV, then run this script again.")
        print("The HTML preview shows a red dashed line where page 1 ends.")
        if not quiet:
            webbrowser.open(html_url)
        sys.exit(2)

    if warnings:
        print(f"\nNOT DONE: fix the {len(warnings)} warning(s) above, then run this script again.")
        print("The PDF was written so you can preview it, but do not submit it yet.")
        if not quiet:
            open_file(pdf_path)
        sys.exit(3)

    try:
        os.remove(html_path)    # preview no longer needed; the PDF is the deliverable
    except OSError:
        pass
    print("\nDONE: your resume fits on one page with no warnings.")
    print("Submit the PDF above together with your CSV.")
    if not quiet:
        open_file(pdf_path)


if __name__ == "__main__":
    main()
