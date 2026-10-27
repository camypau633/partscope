#!/usr/bin/env python3
"""Build compact PartScope JSON shards from the selected TecDoc CSV tables.

Raw TecDoc exports are headerless, tab-separated records. This importer uses
known article/supplier positions, resolves image paths only against the
1Q2019 Git tree manifest, and never fabricates image URLs. It also supports
the earlier normalized CSV format as a fallback.
"""
import argparse
import csv
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

TEXT_RE = re.compile(r"[A-Za-zА-Яа-яЁё]")
DIMENSION_RE = re.compile(
    r"длин|диаметр|резьб|ширин|высот|толщин|length|diameter|thread|width|height|thickness|"
    r"outside.?diameter|inner.?diameter|pitch|размер|размеры",
    re.IGNORECASE,
)
IMAGE_BASE = "https://raw.githubusercontent.com/tecdocSQL/tecdocdatabase1Q2019/main/"


def clean_sku(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def split_line(line):
    return next(csv.reader([line], delimiter="\t", quotechar='"'))


def read_tsv(path):
    if not path or not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
        for line in stream:
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                yield [value.strip() for value in split_line(line)]
            except csv.Error:
                continue


def first_existing(folder, name):
    path = folder / name
    return path if path.is_file() else None


def all_rows(folder, name):
    path = first_existing(folder, name)
    if path:
        yield from read_tsv(path)


def normalize_header(value):
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def normalized_rows(path):
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
        sample = stream.read(8192)
        stream.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(stream, dialect=dialect)
        if not reader.fieldnames:
            return
        reader.fieldnames = [normalize_header(x) for x in reader.fieldnames]
        for row in reader:
            yield {normalize_header(k): (v or "").strip() for k, v in row.items() if k}


def pick(row, *names):
    for name in names:
        value = row.get(name, "")
        if value:
            return str(value).strip()
    return ""


def load_image_manifest(path):
    found = set()
    if not path.exists():
        return found
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            item = line.strip().replace("\\", "/")
            if item.startswith("images/") and not item.endswith("/"):
                found.add(item)
    return found


def create_db(db):
    db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        CREATE TABLE parts(
          sku TEXT PRIMARY KEY, article_id TEXT NOT NULL DEFAULT '',
          supplier_id TEXT NOT NULL DEFAULT '', brand TEXT NOT NULL DEFAULT '',
          name TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX idx_parts_article_id ON parts(article_id);
        CREATE TABLE specs(
          sku TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL,
          unit TEXT NOT NULL DEFAULT '', UNIQUE(sku,name,value,unit)
        );
        CREATE TABLE drawings(
          sku TEXT NOT NULL, url TEXT NOT NULL, UNIQUE(sku,url)
        );
        CREATE TABLE analogues(
          source_sku TEXT NOT NULL, sku TEXT NOT NULL,
          UNIQUE(source_sku,sku)
        );
        CREATE TABLE compatibility(
          sku TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '', years TEXT NOT NULL DEFAULT '',
          engine TEXT NOT NULL DEFAULT '',
          UNIQUE(sku,brand,model,years,engine)
        );
    """)


def load_articles(db, folder, suppliers_by_id, source_label, preserve_existing=False):
    path = folder / "articles.csv"
    if not path.exists():
        print("[WARN] Missing " + str(path), file=sys.stderr)
        return 0
    count = 0
    article_map = {}
    batch = []
    # TecDoc article rows in these exports are tab-separated and headerless:
    # article ID, article number, supplier ID, generic-article ID, description...
    for row in read_tsv(path):
        if len(row) < 5:
            continue
        article_id, number, supplier_id, _generic_id, name = row[:5]
        sku = clean_sku(number)
        if not sku:
            continue
        brand = suppliers_by_id.get(supplier_id, "")
        article_map[article_id] = sku
        batch.append((sku, article_id, supplier_id, brand, name))
        if len(batch) >= 5000:
            if preserve_existing:
                db.executemany("INSERT OR IGNORE INTO parts(sku,article_id,supplier_id,brand,name) VALUES(?,?,?,?,?)", batch)
            else:
                db.executemany("""
                    INSERT INTO parts(sku,article_id,supplier_id,brand,name)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(sku) DO UPDATE SET
                      article_id=CASE WHEN excluded.article_id='' THEN parts.article_id ELSE excluded.article_id END,
                      supplier_id=CASE WHEN excluded.supplier_id='' THEN parts.supplier_id ELSE excluded.supplier_id END,
                      brand=CASE WHEN excluded.brand='' THEN parts.brand ELSE excluded.brand END,
                      name=CASE WHEN excluded.name='' THEN parts.name ELSE excluded.name END
                """, batch)
            count += len(batch)
            batch = []
    if batch:
        if preserve_existing:
            db.executemany("INSERT OR IGNORE INTO parts(sku,article_id,supplier_id,brand,name) VALUES(?,?,?,?,?)", batch)
        else:
            db.executemany("""
                INSERT INTO parts(sku,article_id,supplier_id,brand,name)
                VALUES(?,?,?,?,?)
                ON CONFLICT(sku) DO UPDATE SET
                  article_id=CASE WHEN excluded.article_id='' THEN parts.article_id ELSE excluded.article_id END,
                  supplier_id=CASE WHEN excluded.supplier_id='' THEN parts.supplier_id ELSE excluded.supplier_id END,
                  brand=CASE WHEN excluded.brand='' THEN parts.brand ELSE excluded.brand END,
                  name=CASE WHEN excluded.name='' THEN parts.name ELSE excluded.name END
            """, batch)
        count += len(batch)
    db.commit()
    print("[INFO] Articles from {}: {:,}".format(source_label, count))
    return article_map


def load_suppliers(folder):
    """TecDoc supplier records are headerless; resolve IDs to visible brand text."""
    result = {}
    for row in all_rows(folder, "suppliers.csv"):
        if len(row) < 4:
            continue
        candidates = [row[3], row[-1]]
        brand = next((x for x in candidates if x and TEXT_RE.search(x)), "")
        if not brand:
            continue
        # TecDoc releases differ in the order of supplier identifiers. Index all
        # short numeric identifier columns; article rows resolve by exact ID.
        for token in row[:4]:
            if token.isdigit():
                result.setdefault(token, brand)
    return result


def find_article_id(row, known_ids):
    # IDs are exact tokens only. Never infer an article from a substring.
    for token in row[:5]:
        if token in known_ids:
            return token
    return ""


def load_attribute_dictionary(folder):
    """Read the actual TecDoc items_atributes lookup table (ID -> localized text)."""
    result = {}
    for row in all_rows(folder, "items_atributes.csv"):
        if len(row) >= 2 and row[0]:
            text = next((value for value in row[1:] if value and TEXT_RE.search(value)), "")
            if text:
                result[row[0]] = text.strip()
    return result


def load_specs(db, folder, article_to_sku):
    """Resolve dimension labels/values through the actual item-attribute dictionary."""
    dictionary = load_attribute_dictionary(folder)
    count = 0
    for filename in ("article_attributes.csv", "article_informations.csv"):
        path = folder / filename
        if not path.exists():
            continue
        batch = []
        for row in read_tsv(path):
            article_id = find_article_id(row, article_to_sku)
            if not article_id:
                continue
            sku = article_to_sku[article_id]
            resolved = [(index, dictionary.get(token, token)) for index, token in enumerate(row)]
            label_index = None
            label = ""
            for index, value in resolved:
                if index < 1:
                    continue
                if DIMENSION_RE.search(value or "") and value != article_id:
                    label_index, label = index, value.strip()
                    break
            if label_index is None:
                # Some exports carry the label directly instead of an item ID.
                for index, token in enumerate(row):
                    if index > 0 and DIMENSION_RE.search(token or "") and token != article_id:
                        label_index, label = index, token.strip()
                        break
            if label_index is None:
                continue

            raw_value = next(
                (token for token in row[label_index + 1:] if token and token != article_id),
                "",
            )
            if not raw_value:
                continue
            value = dictionary.get(raw_value, raw_value).strip()
            if not value or value == label:
                continue
            unit = ""
            unit_match = re.search(r"\\b(mm|cm|m|in|inch|°|bar|kg|g)\\b", value, re.I)
            if unit_match:
                unit = unit_match.group(1)
            batch.append((sku, label, value, unit))
            if len(batch) >= 5000:
                db.executemany("INSERT OR IGNORE INTO specs(sku,name,value,unit) VALUES(?,?,?,?)", batch)
                count += len(batch)
                batch = []
        if batch:
            db.executemany("INSERT OR IGNORE INTO specs(sku,name,value,unit) VALUES(?,?,?,?)", batch)
            count += len(batch)
        db.commit()
    print("[INFO] Dimension-labelled attribute rows imported: {:,}".format(count))


def load_drawings(db, folder, article_to_sku, image_paths):
    path = folder / "article_mediainformation.csv"
    if not path.exists():
        print("[WARN] article_mediainformation.csv missing; no drawings imported")
        return 0
    # Build a basename index from the Git tree metadata; no image files are fetched.
    by_basename = {}
    for image_path in image_paths:
        by_basename.setdefault(Path(image_path).name.lower(), image_path)
    count = 0
    batch = []
    for row in read_tsv(path):
        article_id = find_article_id(row, article_to_sku)
        if not article_id:
            continue
        sku = article_to_sku[article_id]
        for token in row:
            candidate = token.replace("\\", "/").strip()
            basename = Path(candidate).name.lower()
            actual_path = by_basename.get(basename)
            if actual_path:
                url = IMAGE_BASE + quote(actual_path, safe="/")
                batch.append((sku, url))
                break
        if len(batch) >= 5000:
            db.executemany("INSERT OR IGNORE INTO drawings(sku,url) VALUES(?,?)", batch)
            count += len(batch)
            batch = []
    if batch:
        db.executemany("INSERT OR IGNORE INTO drawings(sku,url) VALUES(?,?)", batch)
        count += len(batch)
    db.commit()
    print("[INFO] Verified drawing links imported: {:,}".format(count))
    return count


def load_crosses(db, folder, sku_by_article):
    """Import cross references only when a target SKU is actually present in articles."""
    path = folder / "article_cross_list.csv"
    if not path.exists():
        print("[INFO] article_cross_list.csv not present; analogues remain empty")
        return 0
    count = 0
    batch = []
    known_skus = {row[0] for row in db.execute("SELECT sku FROM parts")}
    for row in read_tsv(path):
        if len(row) < 3:
            continue
        source_id = next((x for x in row[:4] if x in sku_by_article), "")
        if not source_id:
            continue
        source_sku = sku_by_article[source_id]
        for token in row:
            candidate = clean_sku(token)
            if candidate in known_skus and candidate != source_sku:
                batch.append((source_sku, candidate))
        if len(batch) >= 5000:
            db.executemany("INSERT OR IGNORE INTO analogues(source_sku,sku) VALUES(?,?)", batch)
            count += len(batch)
            batch = []
    if batch:
        db.executemany("INSERT OR IGNORE INTO analogues(source_sku,sku) VALUES(?,?)", batch)
        count += len(batch)
    db.commit()
    return count


def import_normalized(db, input_dir):
    """Compatibility fallback for a prepared CSV set."""
    parts = input_dir / "parts.csv"
    if not parts.exists():
        return False
    for row in normalized_rows(parts):
        sku = clean_sku(pick(row, "sku", "article_number", "part_number", "article"))
        if sku:
            db.execute("INSERT OR REPLACE INTO parts(sku,brand,name) VALUES(?,?,?)",
                       (sku, pick(row, "brand", "manufacturer"), pick(row, "name", "description")))
    for filename in ("specifications.csv", "article_criteria.csv", "criteria.csv"):
        path = input_dir / filename
        if path.exists():
            for row in normalized_rows(path):
                sku = clean_sku(pick(row, "sku", "article_number", "part_number"))
                name = pick(row, "name", "criterion", "attribute_name", "label")
                value = pick(row, "value", "spec_value", "criteria_value")
                if sku and name and value:
                    db.execute("INSERT OR IGNORE INTO specs(sku,name,value,unit) VALUES(?,?,?,?)",
                               (sku, name, value, pick(row, "unit")))
    for filename in ("diagrams.csv", "graphics.csv", "article_graphics.csv", "article_images.csv"):
        path = input_dir / filename
        if path.exists():
            for row in normalized_rows(path):
                sku = clean_sku(pick(row, "sku", "article_number", "part_number"))
                url = pick(row, "diagram_url", "image_url", "url")
                if sku and url.startswith(("https://", "http://")):
                    db.execute("INSERT OR IGNORE INTO drawings(sku,url) VALUES(?,?)", (sku,url))
    db.commit()
    return True


def export_shards(db, output_dir, limit=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    prefixes = [row[0] for row in db.execute("SELECT DISTINCT substr(sku,1,2) FROM parts ORDER BY 1")]
    exported = 0
    for prefix in prefixes:
        destination = output_dir / (prefix + ".json")
        temporary = destination.with_suffix(".json.tmp")
        first = True
        count = 0
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write("{")
            for (sku, brand, name) in db.execute(
                    "SELECT sku,brand,name FROM parts WHERE substr(sku,1,2)=? ORDER BY sku", (prefix,)):
                specs = {}
                for spec_name, value, unit in db.execute(
                        "SELECT name,value,unit FROM specs WHERE sku=? ORDER BY rowid", (sku,)):
                    specs[spec_name] = value + ((" " + unit) if unit and unit.lower() not in value.lower() else "")
                drawings = [r[0] for r in db.execute(
                    "SELECT url FROM drawings WHERE sku=? ORDER BY rowid", (sku,))]
                analogues = [{"sku": r[0]} for r in db.execute(
                    "SELECT sku FROM analogues WHERE source_sku=? ORDER BY sku LIMIT 500", (sku,))]
                compatibility = [{"brand": r[0], "model": r[1], "years": r[2], "engine": r[3]}
                                  for r in db.execute(
                    "SELECT brand,model,years,engine FROM compatibility WHERE sku=? LIMIT 2000", (sku,))]
                record = {
                    "sku": sku,
                    "brand": brand or "",
                    "name": name or "",
                    "specs": specs,
                    "technical_drawings": drawings,
                    "analogues": analogues,
                    "compatibility": compatibility,
                }
                if not first:
                    stream.write(",")
                stream.write(json.dumps(sku, ensure_ascii=False) + ":" +
                             json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                first = False
                count += 1
                exported += 1
                if limit is not None and exported >= limit:
                    break
            stream.write("}")
        temporary.replace(destination)
        print("[SHARD] {}.json: {:,} SKUs".format(prefix, count))
        if limit is not None and exported >= limit:
            break
    return exported, len(prefixes)


def main():
    parser = argparse.ArgumentParser(description="Stream TecDoc CSV data into PartScope SKU shards.")
    parser.add_argument("--input", type=Path, default=Path("raw_data"))
    parser.add_argument("--output", type=Path, default=Path("public/data"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--clean-output", action="store_true")
    args = parser.parse_args()
    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    if not input_dir.is_dir():
        print("[ERROR] Input directory does not exist: " + str(input_dir), file=sys.stderr)
        return 2
    if args.clean_output and output_dir.exists():
        for item in output_dir.glob("*.json"):
            item.unlink()
    with tempfile.TemporaryDirectory(prefix="partscope-tecdoc-") as tmp:
        db = sqlite3.connect(str(Path(tmp) / "catalogue.sqlite"))
        try:
            create_db(db)
            if import_normalized(db, input_dir):
                print("[INFO] Imported prepared/normalized CSV input.")
            else:
                text_dir = input_dir / "2Q2018"
                graphics_dir = input_dir / "1Q2019"
                if not text_dir.is_dir() or not graphics_dir.is_dir():
                    print("[ERROR] Expected raw_data/2Q2018 and raw_data/1Q2019.", file=sys.stderr)
                    return 2
                suppliers = load_suppliers(text_dir)
                article_map_2q = load_articles(db, text_dir, suppliers, "2Q2018")
                # Quarterly article IDs are not assumed to be stable. Use the
                # 1Q2019 map for media, while retaining 2Q2018 article text.
                article_map_1q = load_articles(
                    db, graphics_dir, load_suppliers(graphics_dir), "1Q2019",
                    preserve_existing=True,
                )
                load_specs(db, text_dir, article_map_2q)
                image_manifest = load_image_manifest(graphics_dir / "images_manifest.txt")
                load_drawings(db, graphics_dir, article_map_1q, image_manifest)
                load_crosses(db, text_dir, article_map_2q)
            count = db.execute("SELECT COUNT(*) FROM parts").fetchone()[0]
            if not count:
                print("[ERROR] No articles imported. Inspect the downloaded TecDoc CSV layout.", file=sys.stderr)
                return 1
            print("[INFO] Unique SKUs: {:,}".format(count))
            exported, shard_count = export_shards(db, output_dir, args.limit)
            print("[DONE] Exported {:,} records.".format(exported))
            return 0
        finally:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
