"""Strict, privacy-conscious Gridwise PDF/CSV archive importer.

Uploads are parsed in memory, never stored to public GitHub or written to the
operator's Shifts/Decisions sheets. Imported rows are HISTORICAL CONTEXT ONLY.
They are deliberately excluded from driving recommendations and current-vehicle
after-fuel calculations; reported tax deductions are not operating expenses.
"""
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import csv
import hashlib
from io import BytesIO, StringIO
import re
import warnings

ARCHIVE_COLUMNS = (
    "row_key", "report_type", "date", "platform", "category",
    "trips", "earnings", "hours", "miles", "deduction_rate",
    "tax_deduction", "source_id", "cohort",
)
MAX_UPLOAD = 3_000_000
MAX_ROWS = 10_000
DATE_PATTERN = (
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},\s*\d{4}"
)
DATE_RE = re.compile(r"^(" + DATE_PATTERN + r")", re.I)
EARNINGS_LINE = re.compile(
    r"^(?P<platform>.+?)\s*(?:(?P<category>Tips)\s*)?"
    r"(?:(?P<trips>\d+)\s*)?\$(?P<earnings>[\d,]+\.\d{1,2})$",
    re.I,
)
MILEAGE_LINE = re.compile(
    r"^(?P<hours>\d+\.\d)\s*(?P<miles>\d+\.\d)\s*"
    r"(?P<rate>\d+\.\d)¢\s*\$(?P<deduction>[\d,]+\.\d{1,2})$"
)
ALIASES = {
    "date": {"date", "day", "shift_date"},
    "platform": {"service", "platform", "app"},
    "category": {"category", "type"},
    "trips": {"trips", "trip_count", "number_of_trips"},
    "earnings": {"earnings", "gross_earnings", "total_earnings", "pay", "amount"},
    "hours": {"hours", "tracked_hours", "time"},
    "miles": {"miles", "mileage", "total_miles", "business_miles"},
    "deduction_rate": {"rate", "deduction_rate"},
    "tax_deduction": {"deduction", "tax_deduction"},
}


def _clean_header(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _date(value):
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y",
                "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Unrecognized Gridwise date")


def _number(value, kind="money"):
    text = str(value).strip().replace("$", "").replace(",", "").replace("¢", "")
    try:
        amount = Decimal(text)
        if not amount.is_finite() or amount < 0:
            raise InvalidOperation()
    except InvalidOperation:
        raise ValueError("Invalid nonnegative Gridwise numeric value") from None
    if kind == "count":
        if amount != amount.to_integral_value():
            raise ValueError("Trip count must be a whole number")
        return str(int(amount))
    return format(amount, "f")


def _base(report_type, row):
    if report_type not in ("earnings", "mileage"):
        raise ValueError("Only Gridwise earnings and mileage reports are supported")
    output = {key: "" for key in ARCHIVE_COLUMNS}
    output.update(report_type=report_type, date=_date(row["date"]),
                  platform=str(row.get("platform", "")).strip()[:80],
                  category=str(row.get("category", "")).strip()[:60],
                  cohort="Archive only - no 2026 forecasting")
    if report_type == "earnings":
        output["earnings"] = _number(row["earnings"])
        value = row.get("trips")
        output["trips"] = _number(value, "count") if value not in ("", None) else ""
    else:
        output["hours"] = _number(row["hours"])
        output["miles"] = _number(row["miles"])
        if str(row.get("deduction_rate", "")).strip():
            output["deduction_rate"] = _number(row["deduction_rate"])
        if str(row.get("tax_deduction", "")).strip():
            output["tax_deduction"] = _number(row["tax_deduction"])
    return output


def _natural_row(row):
    """Canonical values, including legacy Sheet numbers, without changing records."""
    normalized = _base(row["report_type"], row)
    for field in ("trips", "earnings", "hours", "miles", "deduction_rate",
                  "tax_deduction"):
        value = normalized[field]
        if value != "":
            normalized[field] = ("0" if Decimal(value) == 0 else
                                 value.rstrip("0").rstrip(".") if "." in value
                                 else value)
    return "|".join(str(normalized.get(field, "")) for field in ARCHIVE_COLUMNS
                    if field not in ("row_key", "source_id", "cohort"))


def _finalize(raw_rows, report_type, contents):
    if not raw_rows:
        raise ValueError("No supported Gridwise data rows were found")
    if len(raw_rows) > MAX_ROWS:
        raise ValueError("Report has too many rows")
    source_id = hashlib.sha256(contents).hexdigest()[:16]
    seen = Counter()
    result = []
    for raw in raw_rows:
        row = _base(report_type, raw)
        # Duplicate identical transactions within one report are preserved by
        # ordinal, while re-importing that report is idempotent.
        natural = _natural_row(row)
        seen[natural] += 1
        row["row_key"] = hashlib.sha256(
            (natural + "|" + str(seen[natural])).encode("utf-8")
        ).hexdigest()[:24]
        row["source_id"] = source_id
        result.append(row)
    return result


def _extract_pdf_text(data):
    from pypdf import PdfReader
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            reader = PdfReader(BytesIO(data), strict=False)
            if len(reader.pages) < 1 or len(reader.pages) > 30:
                raise ValueError("PDF must contain 1-30 readable pages")
            texts = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:
        raise ValueError("Cannot read this Gridwise PDF") from error
    text = "\n".join(texts)
    if "Gridwise" not in text:
        raise ValueError("This is not a recognizable Gridwise report")
    return text


def parse_gridwise_pdf(data):
    """Recognize current sample's text-PDF structure; fail closed on scans."""
    if not 0 < len(data) <= MAX_UPLOAD or not data.startswith(b"%PDF"):
        raise ValueError("Upload a Gridwise PDF up to 3 MB")
    text = _extract_pdf_text(data)
    if "Earnings Report" in text:
        report_type = "earnings"
        declared = re.search(r"\$([\d,]+\.\d{1,2})\s*\nEarnings\b", text)
    elif "Mileage Report" in text:
        report_type = "mileage"
        declared = re.search(r"\$([\d,]+\.\d{1,2})\s*\nTax Deduction\b", text)
    else:
        raise ValueError("Only Gridwise Earnings or Mileage PDFs are supported")
    if declared is None:
        raise ValueError("Report totals could not be verified")
    raw = []
    for line in text.splitlines():
        m = DATE_RE.match(line.strip())
        if not m:
            continue
        rest = line.strip()[m.end():].strip()
        when = m.group(1)
        if report_type == "earnings":
            parsed = EARNINGS_LINE.fullmatch(rest)
            if not parsed:
                continue
            record = {"date": when, **parsed.groupdict()}
            # 'Tips' rows legitimately have no trips.
            if not record.get("platform"):
                continue
            raw.append(record)
        else:
            parsed = MILEAGE_LINE.fullmatch(rest)
            if parsed:
                raw.append({"date": when, **parsed.groupdict(),
                            "deduction_rate": parsed.group("rate"),
                            "tax_deduction": parsed.group("deduction")})
    result = _finalize(raw, report_type, data)
    expected = Decimal(declared.group(1).replace(",", ""))
    actual = sum(Decimal(row["earnings"] if report_type == "earnings"
                         else row["tax_deduction"]) for row in result)
    if abs(expected - actual) > Decimal("0.12"):
        # The PDF may display rounded tax deductions; allow small rounding
        # differences but never silently accept an incomplete extraction.
        raise ValueError(
            f"Gridwise PDF totals failed validation (reported {expected}, "
            f"parsed {actual}). No rows were imported.")
    return {"type": report_type, "rows": result,
            "declared_total": str(expected), "parsed_total": str(actual),
            "format": "pdf"}


def parse_gridwise_csv(data):
    if not 0 < len(data) <= MAX_UPLOAD:
        raise ValueError("CSV must be no larger than 3 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV must use UTF-8 text") from None
    reader = csv.DictReader(StringIO(text))
    fields = reader.fieldnames or []
    if len(fields) > 40:
        raise ValueError("CSV has too many columns")
    normalized = {_clean_header(field): field for field in fields}
    mapping = {key: next((normalized[x] for x in aliases if x in normalized), None)
               for key, aliases in ALIASES.items()}
    if not mapping["date"]:
        raise ValueError("Gridwise CSV needs a Date column")
    if mapping["earnings"]:
        report_type = "earnings"
    elif mapping["miles"] and mapping["hours"]:
        report_type = "mileage"
    else:
        raise ValueError("CSV needs Earnings or both Hours and Miles columns")
    result = []
    for source_row in reader:
        if len(result) >= MAX_ROWS:
            raise ValueError("CSV has too many rows")
        row = {key: (source_row.get(column) or "") if column else ""
               for key, column in mapping.items()}
        if not row["date"].strip():
            continue
        # Fail closed on unfamiliar / non-Gridwise-like files.
        if report_type == "mileage" and (
            not row["hours"].strip() or not row["miles"].strip()):
            raise ValueError("Mileage report contains missing hours/miles")
        if report_type == "earnings" and not row["earnings"].strip():
            raise ValueError("Earnings report contains missing values")
        result.append(row)
    return {"type": report_type, "rows": _finalize(result, report_type, data),
            "declared_total": None, "parsed_total": None, "format": "csv"}


def parse_gridwise_upload(name, data):
    lower = name.lower()
    if lower.endswith(".pdf"):
        return parse_gridwise_pdf(data)
    if lower.endswith(".csv"):
        return parse_gridwise_csv(data)
    raise ValueError("Only PDF and CSV Gridwise reports are supported")


def report_summary(parsed):
    rows = parsed["rows"]
    dates = {r["date"] for r in rows}
    years = sorted({date.fromisoformat(d).year for d in dates})
    total = sum(Decimal(r["earnings"] or "0") for r in rows) if parsed["type"] == "earnings" \
        else sum(Decimal(r["miles"] or "0") for r in rows)
    return {"rows": len(rows), "dates": len(dates), "years": years,
            "total": round(float(total), 2), "measure":
            "gross reported earnings" if parsed["type"] == "earnings" else "tracked miles"}


def join_by_date(archive_rows):
    """Date-level comparison, not per-trip matching or vehicle-cost estimate."""
    days = defaultdict(lambda: {"earnings": None, "hours": None, "miles": None})
    for row in archive_rows:
        date.fromisoformat(str(row["date"]))
        day = days[row["date"]]
        if row["report_type"] == "earnings":
            day["earnings"] = (day["earnings"] or Decimal("0")) + Decimal(
                str(row["earnings"]))
        elif row["report_type"] == "mileage":
            for key in ("hours", "miles"):
                day[key] = (day[key] or Decimal("0")) + Decimal(str(row[key]))
    return [{"date": day, **{k: float(v) if v is not None else None
                             for k, v in values.items()}}
            for day, values in sorted(days.items())]


def archive_sheet(credentials, sheet_id):
    import gspread
    from pilot_features import open_sheet
    book = open_sheet(credentials, sheet_id)
    try:
        sheet = book.worksheet("Gridwise_Archive")
    except gspread.WorksheetNotFound:
        sheet = book.add_worksheet("Gridwise_Archive", rows=5000, cols=len(ARCHIVE_COLUMNS))
    header = sheet.row_values(1)
    if not header:
        sheet.append_row(list(ARCHIVE_COLUMNS), value_input_option="RAW")
    elif tuple(header) != ARCHIVE_COLUMNS:
        raise ValueError("Gridwise_Archive has incompatible columns; not modifying it")
    return sheet


def archive_load(credentials, sheet_id):
    return archive_sheet(credentials, sheet_id).get_all_records()


def archive_append_new(credentials, sheet_id, rows):
    """Idempotent re-uploads; don't modify the Shifts or Decisions worksheets."""
    if len(rows) > MAX_ROWS:
        raise ValueError("Too many archive rows")
    sheet = archive_sheet(credentials, sheet_id)
    # Old row keys encoded decimal formatting. Compare normalized values and
    # occurrence counts so legacy rows stay untouched and identical legitimate
    # transactions within one report retain their multiplicity.
    existing = Counter(_natural_row(row) for row in sheet.get_all_records())
    seen = Counter()
    new = []
    for row in rows:
        natural = _natural_row(row)
        seen[natural] += 1
        if seen[natural] > existing[natural]:
            new.append(row)
    if new:
        sheet.append_rows(
            [[str(row.get(k, "")) for k in ARCHIVE_COLUMNS] for row in new],
            value_input_option="RAW")
    return len(new), len(rows) - len(new)

