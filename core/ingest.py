"""Reading, mapping and validating an uploaded customer sheet.

The sheet is written by whoever runs the company, not by us, so we never assume
a column layout. We guess a mapping, show the owner what we guessed, and let
them override it before anything is imported.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

# Header synonyms, lowercased and stripped of non-letters before matching.
# Deliberately generic: a book publisher writes "title", a SaaS writes "plan",
# an appliance brand writes "model" — all of them mean "the thing they bought".
FIELD_SYNONYMS: dict[str, list[str]] = {
    "name": [
        "name", "customername", "fullname", "firstname", "contactname",
        "customer", "client", "clientname", "buyer", "student", "member",
    ],
    "email": [
        "email", "emailaddress", "mail", "emailid", "contactemail",
        "customeremail", "workemail", "e",
    ],
    "product": [
        "product", "productname", "item", "sku", "plan", "subscription",
        "model", "title", "course", "service", "package", "purchased",
        "productpurchased", "device", "book",
    ],
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


@dataclass
class IngestReport:
    """What the owner sees before committing an import."""

    mapping: dict[str, str | None]
    total_rows: int
    valid: pd.DataFrame
    missing_name: pd.DataFrame = field(default_factory=pd.DataFrame)
    missing_email: pd.DataFrame = field(default_factory=pd.DataFrame)
    bad_email: pd.DataFrame = field(default_factory=pd.DataFrame)
    missing_product: pd.DataFrame = field(default_factory=pd.DataFrame)
    duplicates: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def problem_count(self) -> int:
        return sum(
            len(df) for df in (
                self.missing_name, self.missing_email, self.bad_email,
                self.missing_product, self.duplicates,
            )
        )


def _normalise(header: str) -> str:
    return re.sub(r"[^a-z]", "", str(header).lower())


def guess_mapping(columns: list[str]) -> dict[str, str | None]:
    """Best-effort column guess. Exact synonym hits win over substring hits."""
    mapping: dict[str, str | None] = {"name": None, "email": None, "product": None}
    normalised = {col: _normalise(col) for col in columns}
    taken: set[str] = set()

    for field_name, synonyms in FIELD_SYNONYMS.items():
        for col, norm in normalised.items():
            if col in taken:
                continue
            if norm in synonyms:
                mapping[field_name] = col
                taken.add(col)
                break

    # Second pass: substring match for headers like "Customer E-mail Address".
    for field_name, synonyms in FIELD_SYNONYMS.items():
        if mapping[field_name] is not None:
            continue
        for col, norm in normalised.items():
            if col in taken or not norm:
                continue
            if any(syn in norm or norm in syn for syn in synonyms):
                mapping[field_name] = col
                taken.add(col)
                break

    return mapping


def read_table(uploaded_file) -> pd.DataFrame:
    """Read CSV or Excel from a Streamlit upload."""
    name = getattr(uploaded_file, "name", "") or ""
    raw = uploaded_file.read() if hasattr(uploaded_file, "read") else uploaded_file
    if isinstance(raw, str):
        raw = raw.encode("utf-8")

    if name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw))

    # CSV: try a couple of encodings before giving up, since exported sheets
    # from Excel on Windows are frequently cp1252 rather than utf-8.
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=encoding)
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    raise ValueError(
        "Could not read this file. Save it as CSV (UTF-8) or .xlsx and try again."
    )


def validate(df: pd.DataFrame, mapping: dict[str, str | None]) -> IngestReport:
    """Split the sheet into importable rows and rows the owner must fix."""
    for field_name in ("name", "email", "product"):
        if not mapping.get(field_name):
            raise ValueError(f"No column chosen for '{field_name}'.")

    work = pd.DataFrame({
        "name": df[mapping["name"]],
        "email": df[mapping["email"]],
        "product": df[mapping["product"]],
    })
    # Keep every other column so nothing the owner uploaded is silently dropped.
    extras = [c for c in df.columns if c not in set(mapping.values())]
    work["_extra"] = (
        df[extras].astype(str).agg(" | ".join, axis=1) if extras else ""
    )
    work["_row"] = df.index + 2  # +2 => spreadsheet row number, header included

    for col in ("name", "email", "product"):
        # fillna FIRST, and do not rely on astype(str) turning NaN into "nan".
        # pandas 2.x did; pandas 3.x keeps NaN as NaN, so a version bump would
        # silently stop catching blank cells — the exact failure this function
        # exists to prevent.
        work[col] = work[col].fillna("").astype(str).str.strip()
        work.loc[
            work[col].str.lower().isin({"nan", "none", "null", "n/a", "na", "-", ""}),
            col,
        ] = ""
    work["email"] = work["email"].str.lower()

    missing_name = work[work["name"] == ""]
    missing_email = work[work["email"] == ""]
    missing_product = work[work["product"] == ""]

    complete = work[
        (work["name"] != "") & (work["email"] != "") & (work["product"] != "")
    ]
    bad_email = complete[~complete["email"].str.match(EMAIL_RE)]
    clean = complete[complete["email"].str.match(EMAIL_RE)]

    # A customer may legitimately appear twice for two different products, so
    # identity is (email, product) — not email alone.
    dupe_mask = clean.duplicated(subset=["email", "product"], keep="first")
    duplicates = clean[dupe_mask]
    valid = clean[~dupe_mask]

    return IngestReport(
        mapping=mapping,
        total_rows=len(df),
        valid=valid.reset_index(drop=True),
        missing_name=missing_name,
        missing_email=missing_email,
        bad_email=bad_email,
        missing_product=missing_product,
        duplicates=duplicates,
    )
