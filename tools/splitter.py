#!/usr/bin/env python3
# Compatible with Python 3.8.10 (including Windows 7 environments where supported).

"""
PartScope CSV -> sharded JSON exporter. Cloud workflow downloads the source archive; this script processes the extracted, normalized CSV tables.

Expected input files (each may also be split into .000, .001, ... chunks):
  parts.csv           brand, sku, name
  analogs.csv         source_sku, brand, sku, name
  compatibility.csv   sku, brand, model, years/year_from/year_to, engine
  specifications.csv  sku, name, value, unit
  diagrams.csv        sku, diagram_url

Output example:
  public/data/W7.json
  {
    "W7129": {
      "sku": "W7129",
      "brand": "...",
      "name": "...",
      "analogs": [],
      "compatibility": [],
      "specifications": [],
      "diagramUrl": ""
    }
  }

The CSV files must already be mapped to these logical relationships. A raw
relational TecDoc dump with internal article/vehicle IDs needs a separate
mapping/join step first. This script does not download archives or guess undocumented ID relations. The cloud workflow must fetch and extract an archive containing these normalized CSV tables first.

Uses only Python standard-library modules and a temporary SQLite database.
It streams CSV input and exports one shard at a time instead of keeping the
whole catalogue in RAM.
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# Configure this only after verifying the real drawing CDN URL pattern.
# Example: "https://your-real-cdn.example/drawings/{id}.jpg"
# Empty by default so the exporter never invents image URLs.
DRAWING_CDN_URL = os.environ.get("DRAWING_CDN_URL", "").strip()

FIELD_ALIASES = {
    "sku": (
        "sku", "article_number", "art_article_nr", "part_number",
        "partnumber", "article", "oem", "number", "artnr",
        "article_no", "article_nr", "oem_number",
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
    "value": ("value", "spec_value", "attribute_value", "content", "criteria_value", "crit_value", "text_value"),
    "drawing_id": ("drawing_id", "graphic_id", "media_id", "picture_id", "image_id", "gd_id", "doc_id"),
    "criterion": ("criterion", "criterion_name", "criteria_name", "crit_name", "attribute_name", "property_name", "key", "label"),
    "unit": ("unit", "unit_name", "measurement_unit"),
    "diagram_url": ("diagram_url", "image_url", "image", "media_url", "url", "graphic_url", "picture_url"),
}


def clean_header(value):
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def clean_sku(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def get_value(row, field):
    for alias in FIELD_ALIASES[field]:
        if alias in row and row[alias] is not None:
            found = str(row[alias]).strip()
            if found:
                return found
    return ""


def csv_files(input_dir, family):
    aliases = {
        "specifications": ("specifications", "article_criteria", "criteria"),
        "diagrams": ("diagrams", "graphics", "article_graphics", "article_images"),
    }.get(family, (family,))
    prefixes = tuple(alias + ".csv" for alias in aliases)
    files = [
        path for path in input_dir.iterdir()
        if path.is_file() and any(
            path.name.lower() == prefix
            or path.name.lower().startswith(prefix + ".")
            for prefix in prefixes
        )
    ]

    def sort_key(path):
        match = re.search(r"\.(\d+)$", path.name)
        if match:
            return (0, int(match.group(1)))
        return (1, path.name.lower())

    return sorted(files, key=sort_key)


def rows_from_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        sample = stream.read(8192)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(stream, dialect=dialect)
        if not reader.fieldnames:
            print("[WARN] CSV has no header row: {}".format(path), file=sys.stderr)
            return

        reader.fieldnames = [clean_header(name) for name in reader.fieldnames]
        for raw in reader:
            yield {
                clean_header(key): (value or "").strip()
                for key, value in raw.items()
                if key is not None
            }


def create_schema(db):
    db.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE parts (
          sku TEXT PRIMARY KEY,
          brand TEXT NOT NULL DEFAULT '',
          name TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE analogs (
          source_sku TEXT NOT NULL,
          brand TEXT NOT NULL DEFAULT '',
          sku TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          UNIQUE(source_sku, sku, brand, name)
        );

        CREATE TABLE compatibility (
          sku TEXT NOT NULL,
          brand TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          years TEXT NOT NULL DEFAULT '',
          engine TEXT NOT NULL DEFAULT '',
          UNIQUE(sku, brand, model, years, engine)
        );

        CREATE TABLE specifications (
          sku TEXT NOT NULL,
          name TEXT NOT NULL,
          value TEXT NOT NULL,
          unit TEXT NOT NULL DEFAULT '',
          UNIQUE(sku, name, value, unit)
        );

        CREATE TABLE diagrams (
          sku TEXT NOT NULL,
          diagram_url TEXT NOT NULL DEFAULT '',
          drawing_id TEXT NOT NULL DEFAULT '',
          UNIQUE(sku, diagram_url, drawing_id)
        );

        CREATE INDEX idx_analogs_source ON analogs(source_sku);
        CREATE INDEX idx_compatibility_sku ON compatibility(sku);
        CREATE INDEX idx_specifications_sku ON specifications(sku);
        CREATE INDEX idx_parts_prefix ON parts(substr(sku, 1, 2));
    """)


def insert_batch(db, family, batch):
    if family == "parts":
        db.executemany("""
            INSERT INTO parts(sku, brand, name) VALUES(?,?,?)
            ON CONFLICT(sku) DO UPDATE SET
              brand=CASE WHEN excluded.brand='' THEN parts.brand ELSE excluded.brand END,
              name=CASE WHEN excluded.name='' THEN parts.name ELSE excluded.name END
        """, batch)

    elif family == "analogs":
        db.executemany("""
            INSERT OR IGNORE INTO analogs(source_sku, brand, sku, name)
            VALUES(?,?,?,?)
        """, batch)

    elif family == "compatibility":
        db.executemany("""
            INSERT OR IGNORE INTO compatibility(sku, brand, model, years, engine)
            VALUES(?,?,?,?,?)
        """, batch)

    elif family == "specifications":
        db.executemany("""
            INSERT OR IGNORE INTO specifications(sku, name, value, unit)
            VALUES(?,?,?,?)
        """, batch)

    elif family == "diagrams":
        db.executemany("""
            INSERT OR IGNORE INTO diagrams(sku, diagram_url, drawing_id)
            VALUES(?,?,?)
        """, batch)


def import_family(db, input_dir, family):
    files = csv_files(input_dir, family)
    if not files:
        print("[INFO] No {}.csv found; skipping.".format(family))
        return 0

    total = 0
    for path in files:
        print("[READ] {}".format(path.name))
        batch = []

        for row in rows_from_csv(path):
            if family == "parts":
                sku = clean_sku(get_value(row, "sku"))
                if not sku:
                    continue
                batch.append((sku, get_value(row, "brand"), get_value(row, "name")))

            elif family == "analogs":
                source_sku = clean_sku(get_value(row, "source_sku"))
                sku = clean_sku(get_value(row, "sku"))
                if not source_sku or not sku or source_sku == sku:
                    continue
                batch.append((
                    source_sku, get_value(row, "brand"),
                    sku, get_value(row, "name"),
                ))

            elif family == "compatibility":
                sku = clean_sku(get_value(row, "sku"))
                brand = get_value(row, "brand")
                model = get_value(row, "model")
                if not sku or not (brand or model):
                    continue

                years = get_value(row, "years") or get_value(row, "year")
                year_from = get_value(row, "year_from")
                year_to = get_value(row, "year_to")
                if not years and (year_from or year_to):
                    years = "{}–{}".format(year_from, year_to).strip("–")

                batch.append((
                    sku, brand, model, years, get_value(row, "engine"),
                ))

            elif family == "specifications":
                sku = clean_sku(get_value(row, "sku"))
                name = get_value(row, "criterion") or get_value(row, "name")
                value = get_value(row, "value")
                if not sku or not name or not value:
                    continue
                batch.append((sku, name, value, get_value(row, "unit")))

            elif family == "diagrams":
                sku = clean_sku(get_value(row, "sku"))
                url = get_value(row, "diagram_url")
                drawing_id = get_value(row, "drawing_id")
                if not url and drawing_id and DRAWING_CDN_URL and "{id}" in DRAWING_CDN_URL:
                    url = DRAWING_CDN_URL.format(id=drawing_id)
                if not sku or not (url or drawing_id):
                    continue
                batch.append((sku, url, drawing_id))

            if len(batch) >= 5000:
                insert_batch(db, family, batch)
                total += len(batch)
                batch = []

        if batch:
            insert_batch(db, family, batch)
            total += len(batch)

        db.commit()

    print("[INFO] {}: imported {:,} valid rows.".format(family, total))
    return total


def build_part_record(db, sku, brand, name):
    analogs = [
        {"brand": analog_brand, "sku": analog_sku, "name": analog_name}
        for analog_brand, analog_sku, analog_name in db.execute(
            """
            SELECT brand, sku, name FROM analogs
            WHERE source_sku=? ORDER BY sku LIMIT 500
            """,
            (sku,),
        )
    ]

    compatibility = [
        {
            "brand": vehicle_brand,
            "model": model,
            "years": years,
            "engine": engine,
        }
        for vehicle_brand, model, years, engine in db.execute(
            """
            SELECT brand, model, years, engine FROM compatibility
            WHERE sku=? LIMIT 2000
            """,
            (sku,),
        )
    ]

    specifications = [
        {
            "key": spec_name,
            "label": spec_name,
            "value": spec_value,
            **({"unit": unit} if unit else {}),
        }
        for spec_name, spec_value, unit in db.execute(
            """
            SELECT name, value, unit FROM specifications
            WHERE sku=? LIMIT 1000
            """,
            (sku,),
        )
    ]

    specs_object = {
        spec_name: spec_value
        for spec_name, spec_value, _unit in db.execute(
            "SELECT name, value, unit FROM specifications WHERE sku=? ORDER BY rowid",
            (sku,),
        )
    }

    technical_drawings = []
    for drawing_url, drawing_id in db.execute(
        "SELECT diagram_url, drawing_id FROM diagrams WHERE sku=? ORDER BY rowid",
        (sku,),
    ):
        resolved_url = drawing_url
        if not resolved_url and drawing_id and DRAWING_CDN_URL and "{id}" in DRAWING_CDN_URL:
            resolved_url = DRAWING_CDN_URL.format(id=drawing_id)
        if resolved_url and resolved_url not in technical_drawings:
            technical_drawings.append(resolved_url)

    diagram_url = technical_drawings[0] if technical_drawings else ""

    return {
        "sku": sku,
        "brand": brand,
        "name": name,
        "specs": specs_object,
        "technical_drawings": technical_drawings,
        "analogs": analogs,
        "analogues": analogs,
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


def shard_prefix(sku):
    return sku[:2]


def export_shards(db, output_dir, limit):
    output_dir.mkdir(parents=True, exist_ok=True)
    prefixes = [
        row[0] for row in db.execute(
            "SELECT DISTINCT substr(sku, 1, 2) FROM parts ORDER BY 1"
        )
    ]

    exported = 0
    shard_count = 0

    for prefix in prefixes:
        destination = output_dir / (prefix + ".json")
        temporary = destination.with_suffix(".json.tmp")
        shard_written = 0

        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write("{\n")
            first = True

            for sku, brand, name in db.execute(
                """
                SELECT sku, brand, name FROM parts
                WHERE substr(sku, 1, 2)=?
                ORDER BY sku
                """,
                (prefix,),
            ):
                record = build_part_record(db, sku, brand, name)
                if not first:
                    stream.write(",\n")
                stream.write(json.dumps(sku, ensure_ascii=False))
                stream.write(":")
                stream.write(json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ))
                first = False
                shard_written += 1
                exported += 1

                if limit is not None and exported >= limit:
                    break

            stream.write("\n}\n")

        temporary.replace(destination)
        shard_count += 1
        print(
            "[SHARD] {}.json: {:,} parts".format(prefix, shard_written)
        )

        if limit is not None and exported >= limit:
            break

    return exported, shard_count


def main():
    parser = argparse.ArgumentParser(
        description="Stream automotive CSV tables into two-character JSON shards."
    )
    parser.add_argument(
        "--input", type=Path, default=Path("csv"),
        help="Input CSV directory (default: ./csv)",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("public/data"),
        help="Output shard directory (default: ./public/data)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Optional maximum number of SKUs to export for a test",
    )
    parser.add_argument(
        "--clean-output", action="store_true",
        help="Delete existing *.json shard files before exporting",
    )
    args = parser.parse_args()

    input_dir = args.input.resolve()
    output_dir = args.output.resolve()

    if not input_dir.is_dir():
        print("[ERROR] Input directory does not exist: {}".format(input_dir), file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 1:
        print("[ERROR] --limit must be >= 1", file=sys.stderr)
        return 2

    if args.clean_output and output_dir.exists():
        removed = 0
        for path in output_dir.glob("*.json"):
            path.unlink()
            removed += 1
        print("[INFO] Removed {:,} existing JSON files.".format(removed))

    with tempfile.TemporaryDirectory(prefix="part-finder-split-") as temp_dir:
        db_path = Path(temp_dir) / "staging.sqlite"
        db = sqlite3.connect(str(db_path))
        try:
            create_schema(db)

            for family in (
                "parts", "analogs", "compatibility",
                "specifications", "diagrams",
            ):
                import_family(db, input_dir, family)

            part_count = db.execute(
                "SELECT COUNT(*) FROM parts"
            ).fetchone()[0]

            if not part_count:
                print(
                    "[ERROR] No parts imported. Check parts.csv headers "
                    "(brand, sku/article_number, name/description).",
                    file=sys.stderr,
                )
                return 1

            print("[INFO] Unique SKUs: {:,}".format(part_count))
            exported, shard_count = export_shards(db, output_dir, args.limit)
            print("[DONE] Exported {:,} SKU records into {} shard files.".format(
                exported, shard_count
            ))
            print("[NOTE] Shards were written to {}.".format(output_dir))
            print("[NOTE] This only reduces the number of files; large shards "
                  "can still exceed GitHub file-size or repository-size limits.")
            return 0
        finally:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
