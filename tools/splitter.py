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


def load_suppliers(folder, expected_supplier_ids=None):
    """Resolve supplier IDs using measured overlap with articles.csv column 2.

    Supplier exports contain several numeric IDs. Mapping every numeric field
    to a brand creates false matches, so choose one column only when it has a
    uniquely best overlap with article supplier IDs.
    """
    rows = [row for row in all_rows(folder, "suppliers.csv") if len(row) >= 4]
    if not rows:
        print("[WARN] No usable supplier rows in {}".format(folder))
        return {}

    expected = {str(value) for value in (expected_supplier_ids or set()) if str(value).isdigit()}
    brand_index = 3
    max_width = max(len(row) for row in rows)
    scores = {}
    for column in range(max_width):
        scores[column] = sum(
            1 for row in rows
            if column < len(row) and row[column].isdigit() and row[column] in expected
            and brand_index < len(row) and TEXT_RE.search(row[brand_index] or "")
        )
    best_score = max(scores.values(), default=0)
    winners = [column for column, score in scores.items() if score == best_score and score > 0]
    if len(winners) != 1:
        print("[WARN] Supplier ID column is ambiguous in {} (best overlap: {}, columns: {}). Brands left blank.".format(
            folder, best_score, winners
        ))
        return {}

    key_index = winners[0]
    result = {}
    for row in rows:
        if key_index >= len(row):
            continue
        key = row[key_index].strip()
        brand = row[brand_index].strip() if brand_index < len(row) else ""
        if key in expected and brand and TEXT_RE.search(brand):
            result[key] = brand
    print("[INFO] Supplier join in {}: column {}, matched {}/{} article supplier IDs".format(
        folder, key_index, len(result), len(expected)
    ))
    return result


def article_supplier_ids(folder):
    """Collect supplier IDs from the observed articles.csv field at index 2."""
    return {
        row[2] for row in all_rows(folder, "articles.csv")
        if len(row) >= 3 and row[2].isdigit()
    }


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
    """Import only the verified article_attributes layout.

    Observed rows are: article ID, attribute ID, placeholder, label, value,
    display flag, value type. Do not scan arbitrary columns for an article ID,
    because attribute IDs can coincidentally match an article ID.
    """
    path = folder / "article_attributes.csv"
    if not path.exists():
        print("[WARN] article_attributes.csv missing; no specifications imported")
        return 0
    count = 0
    batch = []
    for row in read_tsv(path):
        if len(row) < 5:
            continue
        article_id = row[0].strip()
        if article_id not in article_to_sku:
            continue
        label = row[3].strip()
        value = row[4].strip()
        if not label or not value or value == "-" or not DIMENSION_RE.search(label):
            continue
        unit = ""
        unit_match = re.search(r"\b(mm|cm|m|in|inch|°|bar|kg|g)\b", value, re.I)
        if unit_match:
            unit = unit_match.group(1)
        else:
            suffix_match = re.search(r"(мм|см|м|дюйм(?:а|ов)?|кг|г)$", value, re.I)
            if suffix_match:
                unit = suffix_match.group(1)
        batch.append((article_to_sku[article_id], label, value, unit))
        if len(batch) >= 5000:
            db.executemany("INSERT OR IGNORE INTO specs(sku,name,value,unit) VALUES(?,?,?,?)", batch)
            count += len(batch)
            batch = []
    if batch:
        db.executemany("INSERT OR IGNORE INTO specs(sku,name,value,unit) VALUES(?,?,?,?)", batch)
        count += len(batch)
    db.commit()
    print("[INFO] Verified dimension rows imported: {:,}".format(count))
    print("[INFO] article_informations.csv skipped until its article relationship is verified.")
    return count


def load_drawings(db, folder, article_to_sku, image_paths):
    """Do not associate media with parts until an explicit relation is verified.

    The observed article_mediainformation rows contain media metadata and a
    filename, but the sampled layout does not expose an article ID. Matching
    an arbitrary numeric field to articles would create false diagram links.
    """
    path = folder / "article_mediainformation.csv"
    if not path.exists():
        print("[WARN] article_mediainformation.csv missing; no drawings imported")
        return 0
    print("[INFO] Drawings skipped: article_mediainformation has no verified article-ID relation.")
    print("[INFO] Image path manifest contains {} paths; filenames are not enough to link a part.".format(
        len(image_paths)
    ))
    return 0


def load_crosses(db, folder, sku_by_article):
    """Import only cross rows whose two endpoint IDs resolve to known article IDs."""
    path = folder / "article_cross_list.csv"
    if not path.exists():
        print("[INFO] article_cross_list.csv not present; analogues remain empty")
        return 0

    known = set(sku_by_article)
    total = left_match = right_match = both_match = imported = 0
    unmatched_samples = []
    batch = []
    for row in read_tsv(path):
        if len(row) < 2:
            continue
        total += 1
        source_id, target_id = row[0].strip(), row[1].strip()
        left_ok, right_ok = source_id in known, target_id in known
        left_match += int(left_ok)
        right_match += int(right_ok)
        if left_ok and right_ok:
            both_match += 1
            source_sku, target_sku = sku_by_article[source_id], sku_by_article[target_id]
            if source_sku and target_sku and source_sku != target_sku:
                batch.extend(((source_sku, target_sku), (target_sku, source_sku)))
                if len(batch) >= 5000:
                    db.executemany("INSERT OR IGNORE INTO analogues(source_sku,sku) VALUES(?,?)", batch)
                    imported += len(batch)
                    batch = []
        elif len(unmatched_samples) < 8:
            unmatched_samples.append([source_id, target_id, row[2] if len(row) > 2 else ""])
    if batch:
        db.executemany("INSERT OR IGNORE INTO analogues(source_sku,sku) VALUES(?,?)", batch)
        imported += len(batch)
    db.commit()
    print("[CROSS-AUDIT] rows: {:,}; source IDs matched: {:,}; target IDs matched: {:,}; both matched: {:,}".format(
        total, left_match, right_match, both_match
    ))
    if unmatched_samples:
        print("[CROSS-AUDIT] unmatched examples: {}".format(json.dumps(unmatched_samples, ensure_ascii=False)))
    print("[INFO] Verified article-ID cross references imported: {:,}".format(imported))
    return imported

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


def export_shards(db, output_dir, limit=None, prioritize_enriched=False):
    """Export records grouped into shards; when requested, choose enriched SKUs globally."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if prioritize_enriched and limit is not None:
        rows = db.execute("""
            SELECT sku, brand, name FROM parts
            ORDER BY
              (EXISTS(SELECT 1 FROM specs s WHERE s.sku=parts.sku)) DESC,
              (EXISTS(SELECT 1 FROM analogues a WHERE a.source_sku=parts.sku)) DESC,
              (EXISTS(SELECT 1 FROM drawings d WHERE d.sku=parts.sku)) DESC,
              (EXISTS(SELECT 1 FROM compatibility c WHERE c.sku=parts.sku)) DESC,
              sku
            LIMIT ?
        """, (limit,)).fetchall()
        selected = {}
        for sku, brand, name in rows:
            selected.setdefault(sku[:2], []).append((sku, brand, name))
        prefixes = sorted(selected)
        print("[INFO] Globally selected {} records for enriched smoke sample.".format(len(rows)))
    else:
        prefixes = [row[0] for row in db.execute("SELECT DISTINCT substr(sku,1,2) FROM parts ORDER BY 1")]
        selected = None

    exported = 0
    for prefix in prefixes:
        destination = output_dir / (prefix + ".json")
        temporary = destination.with_suffix(".json.tmp")
        first = True
        count = 0
        with temporary.open("w", encoding="utf-8", newline="\\n") as stream:
            stream.write("{")
            if selected is not None:
                part_rows = selected[prefix]
            else:
                part_rows = db.execute(
                    "SELECT sku,brand,name FROM parts WHERE substr(sku,1,2)=? ORDER BY sku",
                    (prefix,)
                )
            for sku, brand, name in part_rows:
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
            stream.write("}")
        temporary.replace(destination)
        print("[SHARD] {}.json: {:,} SKUs".format(prefix, count))
    return exported, len(prefixes)


def inspect_raw_layout(input_dir):
    """Print a bounded structural report before relying on unverified TecDoc column positions."""
    sources = [
        (input_dir / "2Q2018", "articles.csv"),
        (input_dir / "2Q2018", "suppliers.csv"),
        (input_dir / "2Q2018", "article_attributes.csv"),
        (input_dir / "2Q2018", "article_informations.csv"),
        (input_dir / "2Q2018", "article_cross_list.csv"),
        (input_dir / "2Q2018", "items_atributes.csv"),
        (input_dir / "1Q2019", "articles.csv"),
        (input_dir / "1Q2019", "suppliers.csv"),
        (input_dir / "1Q2019", "article_mediainformation.csv"),
    ]
    found = 0
    for folder, filename in sources:
        path = folder / filename
        if not path.is_file():
            print("[LAYOUT] MISSING {} (optional or not downloaded)".format(path))
            continue
        found += 1
        samples = []
        widths = {}
        try:
            for index, row in enumerate(read_tsv(path)):
                width = len(row)
                widths[width] = widths.get(width, 0) + 1
                if len(samples) < 3:
                    samples.append([value[:100] for value in row[:12]])
                if index >= 199:
                    break
        except OSError as exc:
            print("[LAYOUT] ERROR {}: {}".format(path, exc))
            continue
        print("[LAYOUT] FILE {}".format(path))
        print("[LAYOUT] ROW_WIDTHS_IN_FIRST_200 {}".format(
            ", ".join("{} fields:{}".format(width, count) for width, count in sorted(widths.items()))
        ))
        for index, row in enumerate(samples, 1):
            print("[LAYOUT] SAMPLE {} {}".format(index, json.dumps(row, ensure_ascii=False)))
    print("[LAYOUT] Files inspected: {}".format(found))
    return 0 if found else 2


def main():
    parser = argparse.ArgumentParser(description="Stream TecDoc CSV data into PartScope SKU shards.")
    parser.add_argument("--input", type=Path, default=Path("raw_data"))
    parser.add_argument("--output", type=Path, default=Path("public/data"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--clean-output", action="store_true")
    parser.add_argument("--prioritize-enriched", action="store_true",
                        help="Export records with imported linked details first.")
    parser.add_argument("--inspect-layout", action="store_true",
                        help="Report actual raw TecDoc row shapes without generating catalogue data.")
    args = parser.parse_args()
    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    if args.inspect_layout:
        if not input_dir.is_dir():
            print("[ERROR] Input directory does not exist: " + str(input_dir), file=sys.stderr)
            return 2
        return inspect_raw_layout(input_dir)
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
                suppliers = load_suppliers(text_dir, article_supplier_ids(text_dir))
                article_map_2q = load_articles(db, text_dir, suppliers, "2Q2018")
                # Quarterly article IDs are not assumed to be stable. Use the
                # 1Q2019 map for media, while retaining 2Q2018 article text.
                article_map_1q = load_articles(
                    db, graphics_dir, load_suppliers(graphics_dir, article_supplier_ids(graphics_dir)), "1Q2019",
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
            exported, shard_count = export_shards(
                db, output_dir, args.limit, prioritize_enriched=args.prioritize_enriched
            )
            for table in ("specs", "analogues", "drawings", "compatibility"):
                print("[COVERAGE] {} rows: {:,}".format(
                    table, db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                ))
            enriched = db.execute("""
                SELECT COUNT(*) FROM parts p
                WHERE EXISTS (SELECT 1 FROM specs s WHERE s.sku=p.sku)
                   OR EXISTS (SELECT 1 FROM analogues a WHERE a.source_sku=p.sku)
                   OR EXISTS (SELECT 1 FROM drawings d WHERE d.sku=p.sku)
                   OR EXISTS (SELECT 1 FROM compatibility c WHERE c.sku=p.sku)
            """).fetchone()[0]
            print("[COVERAGE] parts with at least one linked detail record: {:,}".format(enriched))
            print("[DONE] Exported {:,} records.".format(exported))
            return 0
        finally:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
