#!/usr/bin/env python3
"""Count TecDoc article rows and compare identities across two published snapshots."""
import csv
import json
import os
import re
import sqlite3
import urllib.request
from pathlib import Path

SOURCES = {
    "2Q2018": "tecdocSQL/tecdocdatabase2Q2018",
    "1Q2019": "tecdocSQL/tecdocdatabase1Q2019",
}
API = "https://api.github.com/repos/{}/git/trees/main?recursive=1"
RAW = "https://raw.githubusercontent.com/{}/main/{}"
WORK = Path("tecdoc-audit-work")
WORK.mkdir(exist_ok=True)


def request(url):
    headers = {"User-Agent": "PartScope-TecDoc-Audit"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=120)


def get_tree(repo):
    with request(API.format(repo)) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("truncated"):
        raise RuntimeError("GitHub tree truncated: " + repo)
    return data["tree"]


def normalize(value):
    # Ignore case and whitespace only; punctuation and leading zeroes remain meaningful.
    return re.sub(r"\s+", "", (value or "").strip()).upper()


def download(repo, path, destination):
    with request(RAW.format(repo, path)) as response, destination.open("wb") as out:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            out.write(block)


def main():
    db = sqlite3.connect(WORK / "audit.sqlite")
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("""CREATE TABLE IF NOT EXISTS articles (
        snapshot TEXT, article_id TEXT, supplier_id TEXT, sku TEXT,
        normalized_sku TEXT, is_valid TEXT, is_pseudo TEXT
    )""")
    db.execute("DELETE FROM articles")
    summary = {"sources": {}, "comparison": {}}

    for snapshot, repo in SOURCES.items():
        print("Inspecting", snapshot, repo, flush=True)
        tree = get_tree(repo)
        article_paths = sorted(
            [x["path"] for x in tree
             if x.get("type") == "blob"
             and re.fullmatch(r"articles\.csv(?:\.\d{3})?", x["path"])],
            key=lambda p: (0, -1) if p == "articles.csv" else (1, int(p.rsplit(".", 1)[1]))
        )
        if not article_paths:
            raise RuntimeError("No articles.csv file parts found for " + snapshot)

        supplier_path = next((x["path"] for x in tree
                              if x.get("type") == "blob" and x["path"] == "suppliers.csv"), None)
        if not supplier_path:
            raise RuntimeError("suppliers.csv missing for " + snapshot)

        local = WORK / snapshot
        local.mkdir(exist_ok=True)
        supplier_file = local / "suppliers.csv"
        download(repo, supplier_path, supplier_file)

        supplier_names = {}
        with supplier_file.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle, delimiter="\t"):
                if len(row) >= 7 and row[1].strip():
                    supplier_names[row[1].strip()] = row[6].strip()

        raw_rows = 0
        inserted = 0
        empty_sku = 0
        missing_supplier = 0
        duplicate_ids = 0
        batch = []

        for index, path in enumerate(article_paths, 1):
            target = local / Path(path).name
            if not target.exists() or target.stat().st_size == 0:
                print("Downloading", snapshot, path, flush=True)
                download(repo, path, target)
            with target.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle, delimiter="\t")
                for row in reader:
                    if not row or all(not cell.strip() for cell in row):
                        continue
                    raw_rows += 1
                    if len(row) < 20:
                        raise RuntimeError(
                            "Unexpected articles.csv row width in {}: {} columns at row {}".format(
                                snapshot, len(row), raw_rows
                            )
                        )
                    article_id = row[0].strip()
                    sku = row[1].strip()
                    supplier_id = row[2].strip()
                    if not sku:
                        empty_sku += 1
                    if not supplier_id:
                        missing_supplier += 1
                    batch.append((
                        snapshot, article_id, supplier_id, sku, normalize(sku),
                        row[19].strip(), row[18].strip()
                    ))
                    if len(batch) >= 25000:
                        db.executemany("INSERT INTO articles VALUES (?,?,?,?,?,?,?)", batch)
                        db.commit()
                        inserted += len(batch)
                        batch = []
            print("Processed", snapshot, index, "/", len(article_paths), path, flush=True)

        if batch:
            db.executemany("INSERT INTO articles VALUES (?,?,?,?,?,?,?)", batch)
            db.commit()
            inserted += len(batch)

        row = db.execute("""
            SELECT COUNT(*),
                   COUNT(DISTINCT article_id),
                   COUNT(DISTINCT CASE WHEN sku <> '' AND supplier_id <> ''
                       THEN supplier_id || char(31) || normalized_sku END),
                   COUNT(DISTINCT CASE WHEN normalized_sku <> '' THEN normalized_sku END),
                   SUM(CASE WHEN is_valid IN ('1','true','TRUE') THEN 1 ELSE 0 END),
                   SUM(CASE WHEN is_pseudo IN ('1','true','TRUE') THEN 1 ELSE 0 END)
            FROM articles WHERE snapshot=?
        """, (snapshot,)).fetchone()

        duplicate_ids = db.execute(
            "SELECT COUNT(*) - COUNT(DISTINCT article_id) FROM articles WHERE snapshot=?",
            (snapshot,)
        ).fetchone()[0]

        duplicate_ids = db.execute(\n            \"SELECT COUNT(*) - COUNT(DISTINCT article_id) FROM articles WHERE snapshot=?\",\n            (snapshot,)\n        ).fetchone()[0]\n\n        supplier_ids = db.execute(\n            "SELECT COUNT(DISTINCT supplier_id) FROM articles WHERE snapshot=? AND supplier_id<>''",
            (snapshot,)
        ).fetchone()[0]

        summary["sources"][snapshot] = {
            "repository": repo,
            "article_files": article_paths,
            "article_file_count": len(article_paths),
            "raw_article_rows": raw_rows,
            "inserted_rows": inserted,
            "distinct_article_ids": row[1],
            "unique_supplier_plus_article_number": row[2],
            "unique_article_numbers_without_brand": row[3],
            "valid_rows_IsValid_1": row[4] or 0,
            "pseudo_rows_IsPseudoArticle_1": row[5] or 0,
            "rows_with_empty_article_number": empty_sku,
            "rows_with_empty_supplier_id": missing_supplier,
            "duplicate_article_ids_in_csv": duplicate_ids,
            "distinct_supplier_ids_used": supplier_ids,
            "supplier_dictionary_entries": len(supplier_names),
            "supplier_name_examples": dict(list(supplier_names.items())[:8]),
        }
        print(json.dumps(summary["sources"][snapshot], ensure_ascii=False, indent=2), flush=True)

    db.execute("CREATE INDEX idx_article_identity ON articles(snapshot, supplier_id, normalized_sku)")
    db.commit()
    overlap = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT supplier_id, normalized_sku FROM articles
            WHERE snapshot='2Q2018' AND supplier_id<>'' AND normalized_sku<>''
            GROUP BY supplier_id, normalized_sku
            INTERSECT
            SELECT supplier_id, normalized_sku FROM articles
            WHERE snapshot='1Q2019' AND supplier_id<>'' AND normalized_sku<>''
            GROUP BY supplier_id, normalized_sku
        )
    """).fetchone()[0]
    union_count = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT supplier_id, normalized_sku
            FROM articles
            WHERE supplier_id<>'' AND normalized_sku<>''
            GROUP BY supplier_id, normalized_sku
        )
    """).fetchone()[0]
    sum_unique = sum(v["unique_supplier_plus_article_number"]
                     for v in summary["sources"].values())
    summary["comparison"] = {
        "same_brand_and_article_in_both_snapshots": overlap,
        "sum_of_per_snapshot_unique_counts": sum_unique,
        "combined_unique_brand_article_pairs_across_both": union_count,
        "overlap_is_not_counted_twice_in_combined_total": True,
    }

    Path("tecdoc-article-audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# TecDoc article count audit",
        "",
        "Counts are calculated from the published CSV files, not inferred from file sizes.",
        "Article identity for deduplication: supplier ID + article number (case-insensitive, whitespace removed; punctuation and leading zeroes retained).",
        "",
    ]
    for name, values in summary["sources"].items():
        lines += [
            "## " + name,
            "- Raw article rows: **{:,}**".format(values["raw_article_rows"]),
            "- Distinct internal article IDs: **{:,}**".format(values["distinct_article_ids"]),
            "- Unique supplier + article number pairs: **{:,}**".format(values["unique_supplier_plus_article_number"]),
            "- Unique article numbers without brand: **{:,}**".format(values["unique_article_numbers_without_brand"]),
            "- Valid rows (IsValid=1): **{:,}**".format(values["valid_rows_IsValid_1"]),
            "- Pseudo-article rows (IsPseudoArticle=1): **{:,}**".format(values["pseudo_rows_IsPseudoArticle_1"]),
            "- Empty article numbers: **{:,}**".format(values["rows_with_empty_article_number"]),
            "- Duplicate internal IDs in CSV: **{:,}**".format(values["duplicate_article_ids_in_csv"]),
            "- CSV parts: {}".format(values["article_file_count"]),
            "",
        ]
    c = summary["comparison"]
    lines += [
        "## Across both snapshots",
        "- Matching supplier + article pairs present in both versions: **{:,}**".format(c["same_brand_and_article_in_both_snapshots"]),
        "- Sum of per-version unique counts (double-counts overlap): **{:,}**".format(c["sum_of_per_snapshot_unique_counts"]),
        "- Combined unique supplier + article pairs: **{:,}**".format(c["combined_unique_brand_article_pairs_across_both"]),
        "",
        "The article-row count is not the same as the count of unique sellable parts. Interpret valid/pseudo flags separately.",
    ]
    Path("tecdoc-article-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    db.close()


if __name__ == "__main__":
    main()
