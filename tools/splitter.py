#!/usr/bin/env python3
"""
Part Finder local CSV splitter (Python 3.10+; standard library only).

Reads these CSV families from --input:
  parts.csv[.000], analogs.csv[.000], compatibility.csv[.000],
  specifications.csv[.000], diagrams.csv[.000]

Required columns (aliases listed in FIELD_ALIASES below are also accepted):
  parts:          brand, sku, name
  analogs:        source_sku, brand, sku, name
  compatibility:  sku, brand/make, model (years and engine optional)
  specifications: sku, name, value (unit optional)
  diagrams:       sku, diagram_url

Large files are streamed into a temporary SQLite staging database. The script
does not keep the entire catalogue in RAM. Only the frontend-relevant fields
are exported; unrelated columns are discarded.

IMPORTANT: a raw relational TecDoc SQL/CSV dump is not automatically equivalent
to these five linked tables. If its files contain article IDs rather than SKU
numbers, map/join those relationships into these input CSV shapes first.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Iterable

FIELD_ALIASES = {
    "sku": (
        "sku", "article_number", "art_article_nr", "part_number",
        "partnumber", "article", "oem", "number", "artnr",
    ),
    "source_sku": (
        "source_sku", "source_article", "original_sku", "base_sku",
        "from_sku", "parent_sku", "main_article_number",
    ),
    "brand": (
        "brand", "manufacturer", "manufacturer_name", "supplier_brand",
        "make", "brand_name", "mfr",
    ),
    "name": (
        "name", "part_name", "description", "article_name", "des_text",
        "title", "label",
    ),
    "model": ("model", "vehicle_model", "model_name"),
    "years": ("years", "year_range", "production_years"),
    "year_from": ("year_from", "yearfrom", "year_start", "production_from"),
    "year_to": ("year_to", "yearto", "year_end", "production_to"),
    "year": ("year", "model_year"),
    "engine": ("engine", "engine_code", "motor", "engine_type"),
    "value": ("value", "spec_value", "attribute_value", "content"),
    "unit": ("unit", "unit_name", "measurement_unit"),
    "diagram_url": ("diagram_url", "image_url", "image", "media_url", "url"),
}

def clean_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")

def clean_sku(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

def value(row: dict[str, str], field: str) -> str:
    for alias in FIELD_ALIASES[field]:
        if alias in row and row[alias] is not None:
            found = str(row[alias]).strip()
            if found:
                return found
    return ""

def csv_files(input_dir: Path, family: str) -> list[Path]:
    # Support unsplit files and chunks such as parts.csv.000 / parts.csv.001.
    files = [
        p for p in input_dir.iterdir()
        if p.is_file() and (
            p.name.lower() == f"{family}.csv"
            or p.name.lower().startswith(f"{family}.csv.")
        )
    ]
    def sort_key(path: Path):
        match = re.search(r"\.(\d+)$", path.name)
        return (0, int(match.group(1))) if match else (-1, path.name)
    return sorted(files, key=sort_key)

def rows_from_csv(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(8192)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(stream, dialect=dialect)
        if not reader.fieldnames:
            print(f"[WARN] No header row: {path}", file=sys.stderr)
            return
        reader.fieldnames = [clean_header(h) for h in reader.fieldnames]
        for raw in reader:
            yield {clean_header(k): (v or "").strip() for k, v in raw.items() if k is not None}

def create_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        CREATE TABLE parts (
          sku TEXT PRIMARY KEY, brand TEXT NOT NULL DEFAULT '',
          name TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE analogs (
          source_sku TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '',
          sku TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
          UNIQUE(source_sku, sku, brand, name)
        );
        CREATE TABLE compatibility (
          sku TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '', years TEXT NOT NULL DEFAULT '',
          engine TEXT NOT NULL DEFAULT '',
          UNIQUE(sku, brand, model, years, engine)
        );
        CREATE TABLE specifications (
          sku TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
          unit TEXT NOT NULL DEFAULT '',
          UNIQUE(sku, name, value, unit)
        );
        CREATE TABLE diagrams (
          sku TEXT PRIMARY KEY, diagram_url TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX idx_analogs_source ON analogs(source_sku);
        CREATE INDEX idx_compatibility_sku ON compatibility(sku);
        CREATE INDEX idx_specifications_sku ON specifications(sku);
    """)

def import_family(db: sqlite3.Connection, input_dir: Path, family: str) -> int:
    files = csv_files(input_dir, family)
    if not files:
        print(f"[INFO] No {family}.csv file found; skipping.")
        return 0

    total = 0
    for path in files:
        print(f"[READ] {path.name}")
        batch = []
        for row in rows_from_csv(path):
            if family == "parts":
                sku = clean_sku(value(row, "sku"))
                if not sku:
                    continue
                batch.append((sku, value(row, "brand"), value(row, "name")))
            elif family == "analogs":
                source_sku = clean_sku(value(row, "source_sku"))
                sku = clean_sku(value(row, "sku"))
                if not source_sku or not sku or source_sku == sku:
                    continue
                batch.append((source_sku, value(row, "brand"), sku, value(row, "name")))
            elif family == "compatibility":
                sku = clean_sku(value(row, "sku"))
                brand = value(row, "brand")
                model = value(row, "model")
                if not sku or not (brand or model):
                    continue
                years = value(row, "years") or value(row, "year")
                year_from, year_to = value(row, "year_from"), value(row, "year_to")
                if not years and (year_from or year_to):
                    years = f"{year_from}–{year_to}".strip("–")
                batch.append((sku, brand, model, years, value(row, "engine")))
            elif family == "specifications":
                sku, name, val = clean_sku(value(row, "sku")), value(row, "name"), value(row, "value")
                if not sku or not name or not val:
                    continue
                batch.append((sku, name, val, value(row, "unit")))
            elif family == "diagrams":
                sku, url = clean_sku(value(row, "sku")), value(row, "diagram_url")
                if not sku or not url:
                    continue
                batch.append((sku, url))

            if len(batch) >= 5000:
                insert_batch(db, family, batch)
                total += len(batch)
                batch.clear()
        if batch:
            insert_batch(db, family, batch)
            total += len(batch)
        db.commit()
    print(f"[INFO] {family}: imported {total:,} valid rows.")
    return total

def insert_batch(db: sqlite3.Connection, family: str, batch: list[tuple]) -> None:
    if family == "parts":
        db.executemany("""
            INSERT INTO parts(sku,brand,name) VALUES(?,?,?)
            ON CONFLICT(sku) DO UPDATE SET
              brand=CASE WHEN excluded.brand='' THEN parts.brand ELSE excluded.brand END,
              name=CASE WHEN excluded.name='' THEN parts.name ELSE excluded.name END
        """, batch)
    elif family == "analogs":
        db.executemany("INSERT OR IGNORE INTO analogs(source_sku,brand,sku,name) VALUES(?,?,?,?)", batch)
    elif family == "compatibility":
        db.executemany("INSERT OR IGNORE INTO compatibility(sku,brand,model,years,engine) VALUES(?,?,?,?,?)", batch)
    elif family == "specifications":
        db.executemany("INSERT OR IGNORE INTO specifications(sku,name,value,unit) VALUES(?,?,?,?)", batch)
    elif family == "diagrams":
        db.executemany("""
            INSERT INTO diagrams(sku,diagram_url) VALUES(?,?)
            ON CONFLICT(sku) DO UPDATE SET diagram_url=excluded.diagram_url
        """, batch)

def export_json(db: sqlite3.Connection, output_dir: Path, limit: int | None) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    cursor = db.execute("SELECT sku, brand, name FROM parts ORDER BY sku")
    exported = 0

    for sku, brand, name in cursor:
        analogs = [
            {"brand": b, "sku": a_sku, "name": a_name}
            for b, a_sku, a_name in db.execute(
                "SELECT brand, sku, name FROM analogs WHERE source_sku=? ORDER BY sku LIMIT 500",
                (sku,),
            )
        ]
        compatibility = [
            {"brand": b, "model": model, "years": years, "engine": engine}
            for b, model, years, engine in db.execute(
                "SELECT brand, model, years, engine FROM compatibility WHERE sku=? LIMIT 2000",
                (sku,),
            )
        ]
        specifications = [
            {"key": spec_name, "label": spec_name, "value": spec_value, **({"unit": unit} if unit else {})}
            for spec_name, spec_value, unit in db.execute(
                "SELECT name, value, unit FROM specifications WHERE sku=? LIMIT 1000",
                (sku,),
            )
        ]
        diagram_row = db.execute("SELECT diagram_url FROM diagrams WHERE sku=?", (sku,)).fetchone()
        diagram_url = diagram_row[0] if diagram_row else ""

        record = {
            "sku": sku,
            "brand": brand,
            "name": name,
            "analogs": analogs,
            "compatibility": compatibility,
            "specifications": specifications,
            "diagramUrl": diagram_url,
            "diagram": {
                "diagramUrl": diagram_url,
                "imageUrl": diagram_url,
                "specifications": specifications,
                "hotspots": [],
            },
        }
        destination = output_dir / f"{sku}.json"
        temp_destination = destination.with_suffix(".json.tmp")
        with temp_destination.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        temp_destination.replace(destination)
        exported += 1

        if exported % 10000 == 0:
            print(f"[EXPORT] {exported:,} JSON files written...")
        if limit is not None and exported >= limit:
            break

    return exported

def main() -> int:
    parser = argparse.ArgumentParser(description="Split automotive CSV tables into per-SKU JSON files.")
    parser.add_argument("--input", type=Path, default=Path("csv"), help="Directory with CSV tables (default: ./csv)")
    parser.add_argument("--output", type=Path, default=Path("data"), help="Output JSON directory (default: ./data)")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum number of SKU files to export (useful for a test run)")
    parser.add_argument("--clean-output", action="store_true", help="Delete existing *.json files in output before export")
    args = parser.parse_args()

    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    if not input_dir.is_dir():
        print(f"[ERROR] Input directory does not exist: {input_dir}", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 1:
        print("[ERROR] --limit must be >= 1", file=sys.stderr)
        return 2

    if args.clean_output and output_dir.exists():
        removed = 0
        for path in output_dir.glob("*.json"):
            path.unlink()
            removed += 1
        print(f"[INFO] Removed {removed:,} old JSON files.")

    with tempfile.TemporaryDirectory(prefix="part-finder-split-") as temp_dir:
        staging_path = Path(temp_dir) / "staging.sqlite"
        db = sqlite3.connect(staging_path)
        try:
            create_schema(db)
            for family in ("parts", "analogs", "compatibility", "specifications", "diagrams"):
                import_family(db, input_dir, family)
            part_count = db.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
            if not part_count:
                print(
                    "[ERROR] No part rows imported. Check that parts.csv has columns equivalent to brand, sku, name.",
                    file=sys.stderr,
                )
                return 1
            print(f"[INFO] Unique part numbers in staging database: {part_count:,}")
            exported = export_json(db, output_dir, args.limit)
            print(f"[DONE] Wrote {exported:,} JSON files to: {output_dir}")
            print("[NOTE] JSON files contain only selected catalogue fields; original CSVs are unchanged.")
            print("[NOTE] Generated data is local until you copy/commit it into the repository's public /data/ folder.")
            return 0
        finally:
            db.close()

if __name__ == "__main__":
    raise SystemExit(main())
