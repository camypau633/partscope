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
          part_id TEXT PRIMARY KEY, sku TEXT NOT NULL,
          source_snapshot TEXT NOT NULL DEFAULT '', article_id TEXT NOT NULL DEFAULT '',
          supplier_id TEXT NOT NULL DEFAULT '', brand TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX idx_parts_sku ON parts(sku);
        CREATE INDEX idx_parts_article_id ON parts(source_snapshot,article_id);
        CREATE TABLE specs(part_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL, unit TEXT NOT NULL DEFAULT '', UNIQUE(part_id,name,value,unit));
        CREATE TABLE drawings(part_id TEXT NOT NULL, url TEXT NOT NULL, UNIQUE(part_id,url));
        CREATE TABLE analogues(source_part_id TEXT NOT NULL, target_part_id TEXT NOT NULL, UNIQUE(source_part_id,target_part_id));
        CREATE TABLE compatibility(part_id TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', years TEXT NOT NULL DEFAULT '', engine TEXT NOT NULL DEFAULT '', UNIQUE(part_id,brand,model,years,engine));
    """)


def load_articles(db, folder, suppliers_by_id, source_label, preserve_existing=False):
    path = folder / "articles.csv"
    if not path.exists():
        print("[WARN] Missing " + str(path), file=sys.stderr)
        return {}
    article_map = {}
    accepted = 0
    for row in read_tsv(path):
        if len(row) < 5:
            continue
        article_id, number, supplier_id, _generic_id, name = row[:5]
        sku = clean_sku(number)
        if not sku or not article_id or not supplier_id:
            continue
        # TecDoc article IDs are snapshot-local; SKU is deliberately non-unique.
        part_id = source_label + ":" + article_id
        brand = suppliers_by_id.get(supplier_id, "")
        db.execute(
            "INSERT OR IGNORE INTO parts(part_id,sku,source_snapshot,article_id,supplier_id,brand,name) VALUES(?,?,?,?,?,?,?)",
            (part_id, sku, source_label, article_id, supplier_id, brand, name),
        )
        if db.execute("SELECT changes()").fetchone()[0]:
            accepted += 1
        article_map[article_id] = part_id
    db.commit()
    print("[INFO] Articles from {}: {:,} unique TecDoc article IDs".format(source_label, accepted))
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


def load_specs(db, folder, article_to_part_id):
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
        if article_id not in article_to_part_id:
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
        batch.append((article_to_part_id[article_id], label, value, unit))
        if len(batch) >= 5000:
            db.executemany("INSERT OR IGNORE INTO specs(part_id,name,value,unit) VALUES(?,?,?,?)", batch)
            count += len(batch)
            batch = []
    if batch:
        db.executemany("INSERT OR IGNORE INTO specs(part_id,name,value,unit) VALUES(?,?,?,?)", batch)
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


def load_crosses(db, folder, part_id_by_article):
    """Resolve directed cross-list edges to exact snapshot-local article identities."""
    path = folder / "article_cross_list.csv"
    articles_path = folder / "articles.csv"
    if not path.exists() or not articles_path.exists():
        print("[INFO] Cross-list or articles table missing; cross references skipped")
        return 0
    target_by_supplier_number = {}
    ambiguous_targets = set()
    for article in read_tsv(articles_path):
        if len(article) < 3:
            continue
        article_id, number, supplier_id = article[0].strip(), article[1].strip(), article[2].strip()
        target_id = part_id_by_article.get(article_id, "")
        key = (supplier_id, clean_sku(number))
        if not supplier_id or not key[1] or not target_id:
            continue
        existing = target_by_supplier_number.get(key)
        if existing and existing != target_id:
            ambiguous_targets.add(key)
            target_by_supplier_number.pop(key, None)
        elif key not in ambiguous_targets:
            target_by_supplier_number[key] = target_id
    total = imported = 0
    batch = []
    for row in read_tsv(path):
        if len(row) < 3:
            continue
        total += 1
        source_id, target_supplier, target_number = row[0].strip(), row[1].strip(), row[2].strip()
        source_part = part_id_by_article.get(source_id, "")
        target_part = target_by_supplier_number.get((target_supplier, clean_sku(target_number)), "")
        if source_part and target_part and source_part != target_part:
            batch.append((source_part, target_part))
            if len(batch) >= 5000:
                db.executemany("INSERT OR IGNORE INTO analogues(source_part_id,target_part_id) VALUES(?,?)", batch)
                imported += db.execute("SELECT changes()").fetchone()[0]
                batch = []
    if batch:
        db.executemany("INSERT OR IGNORE INTO analogues(source_part_id,target_part_id) VALUES(?,?)", batch)
        imported += db.execute("SELECT changes()").fetchone()[0]
    db.commit()
    print("[CROSS-AUDIT] rows: {:,}; resolved directed article links: {:,}".format(total, imported))
    return imported

def import_normalized(db, input_dir):
    """Import normalized CSVs using the same article identity model as TecDoc."""
    parts = input_dir / "parts.csv"
    if not parts.exists():
        return False
    sku_to_part = {}
    for index, row in enumerate(normalized_rows(parts), 1):
        sku = clean_sku(pick(row, "sku", "article_number", "part_number", "article"))
        if not sku:
            continue
        article_id = pick(row, "article_id", "id") or str(index)
        source = pick(row, "source_snapshot", "source") or "normalized"
        part_id = source + ":" + article_id
        db.execute("INSERT OR IGNORE INTO parts(part_id,sku,source_snapshot,article_id,supplier_id,brand,name) VALUES(?,?,?,?,?,?,?)",
                   (part_id, sku, source, article_id, pick(row, "supplier_id"), pick(row, "brand", "manufacturer"),
                    pick(row, "name", "description")))
        sku_to_part.setdefault(sku, part_id)
    for filename in ("specifications.csv", "article_criteria.csv", "criteria.csv"):
        path = input_dir / filename
        if path.exists():
            for row in normalized_rows(path):
                sku = clean_sku(pick(row, "sku", "article_number", "part_number"))
                part_id = pick(row, "part_id")
                if not part_id:
                    part_id = sku_to_part.get(sku, "")
                name = pick(row, "name", "criterion", "attribute_name", "label")
                value = pick(row, "value", "spec_value", "criteria_value")
                if part_id and name and value:
                    db.execute("INSERT OR IGNORE INTO specs(part_id,name,value,unit) VALUES(?,?,?,?)",
                               (part_id, name, value, pick(row, "unit")))
    for filename in ("diagrams.csv", "graphics.csv", "article_graphics.csv", "article_images.csv"):
        path = input_dir / filename
        if path.exists():
            for row in normalized_rows(path):
                sku = clean_sku(pick(row, "sku", "article_number", "part_number"))
                part_id = pick(row, "part_id") or sku_to_part.get(sku, "")
                url = pick(row, "diagram_url", "image_url", "url")
                if part_id and url.startswith(("https://", "http://")):
                    db.execute("INSERT OR IGNORE INTO drawings(part_id,url) VALUES(?,?)", (part_id, url))
    db.commit()
    return True


def export_shards(db, output_dir, limit=None, prioritize_enriched=False):
    """Export SKU shards with arrays because printed part numbers are not unique."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if prioritize_enriched and limit is not None:
        rows = db.execute("""
            SELECT p.part_id,p.sku,p.brand,p.name FROM parts p
            ORDER BY (EXISTS(SELECT 1 FROM specs s WHERE s.part_id=p.part_id)) DESC,
                     (EXISTS(SELECT 1 FROM analogues a WHERE a.source_part_id=p.part_id)) DESC,
                     p.sku,p.part_id LIMIT ?
        """, (limit,)).fetchall()
        selected = {}
        for row in rows:
            selected.setdefault(row[1][:2], []).append(row)
        prefixes = sorted(selected)
    else:
        prefixes = [r[0] for r in db.execute("SELECT DISTINCT substr(sku,1,2) FROM parts ORDER BY 1")]
        selected = None
    exported = 0
    for prefix in prefixes:
        destination = output_dir / (prefix + ".json")
        temporary = destination.with_suffix(".json.tmp")
        rows = selected[prefix] if selected is not None else db.execute(
            "SELECT part_id,sku,brand,name FROM parts WHERE substr(sku,1,2)=? ORDER BY sku,part_id", (prefix,)
        )
        shard = {}
        for part_id, sku, brand, name in rows:
            specs = {}
            for label, value, unit in db.execute("SELECT name,value,unit FROM specs WHERE part_id=? ORDER BY rowid", (part_id,)):
                specs[label] = value + ((" " + unit) if unit and unit.lower() not in value.lower() else "")
            drawings = [r[0] for r in db.execute("SELECT url FROM drawings WHERE part_id=? ORDER BY rowid", (part_id,))]
            analogues = [{"part_id": r[0], "sku": r[1], "brand": r[2] or "", "name": r[3] or ""}
                for r in db.execute("""SELECT p.part_id,p.sku,p.brand,p.name FROM analogues a
                    JOIN parts p ON p.part_id=a.target_part_id WHERE a.source_part_id=? ORDER BY p.sku,p.part_id LIMIT 500""", (part_id,))]
            compatibility = [{"brand": r[0], "model": r[1], "years": r[2], "engine": r[3]}
                for r in db.execute("SELECT brand,model,years,engine FROM compatibility WHERE part_id=? LIMIT 2000", (part_id,))]
            record = {"part_id": part_id, "sku": sku, "brand": brand or "", "name": name or "",
                      "specs": specs, "technical_drawings": drawings, "analogues": analogues, "compatibility": compatibility}
            shard.setdefault(sku, []).append(record)
            exported += 1
        temporary.write_text(json.dumps(shard, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(destination)
        print("[SHARD] {}.json: {:,} article records".format(prefix, sum(len(v) for v in shard.values())))
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
                WHERE EXISTS (SELECT 1 FROM specs s WHERE s.part_id=p.part_id)
                   OR EXISTS (SELECT 1 FROM analogues a WHERE a.source_part_id=p.part_id)
                   OR EXISTS (SELECT 1 FROM drawings d WHERE d.part_id=p.part_id)
                   OR EXISTS (SELECT 1 FROM compatibility c WHERE c.part_id=p.part_id)
            """).fetchone()[0]
            print("[COVERAGE] parts with at least one linked detail record: {:,}".format(enriched))
            print("[DONE] Exported {:,} records.".format(exported))
            return 0
        finally:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
