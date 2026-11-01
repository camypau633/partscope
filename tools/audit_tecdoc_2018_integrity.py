#!/usr/bin/env python3
"""Audit 2Q2018 article-related tables and compare their article_id references."""
import csv
import json
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

REPO = "tecdocSQL/tecdocdatabase2Q2018"
API = "https://api.github.com/repos/{}/git/trees/main?recursive=1"
RAW = "https://raw.githubusercontent.com/{}/main/{}"
WORK = Path("tecdoc-2018-integrity")

# TecDoc CSV fields can exceed Python's default 128 KiB parser limit.
_csv_limit = sys.maxsize
while True:
    try:
        csv.field_size_limit(_csv_limit)
        break
    except OverflowError:
        _csv_limit //= 10
WORK.mkdir(exist_ok=True)
TABLES = [
    "articles.csv", "article_attributes", "article_informations",
    "article_mediainformation", "article_oe_numbers", "article_ea_numbers",
    "article_cross_list", "articles_linkages", "article_new_numbers",
    "article_replace_numbers", "article_utility_numbers", "article_parts_list",
    "article_parts_list_parents", "article_accessory_lists",
    "article_accessory_list_parents",
]


def request(url):
    headers = {"User-Agent": "PartScope-TecDoc-2018-Integrity-Audit"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=180)


def download(path, dest):
    with request(RAW.format(REPO, path)) as response, dest.open("wb") as out:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            out.write(block)


def part_sort_key(path):
    # Unsplit CSV files sort first; numbered chunks sort numerically.
    match = re.search(r"\.(\d{3})$", path)
    return (1, int(match.group(1))) if match else (0, 0)


def main():
    with request(API.format(REPO)) as response:
        tree = json.loads(response.read().decode("utf-8"))["tree"]
    files = [x for x in tree if x.get("type") == "blob"]
    db = sqlite3.connect(WORK / "article-ids.sqlite")
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute("CREATE TABLE IF NOT EXISTS ids (table_name TEXT, article_id TEXT)")
    db.execute("DELETE FROM ids")
    report = {"repository": REPO, "tables": {}, "supplier_metadata": {}}

    supplier_path = next(x["path"] for x in files if x["path"] == "suppliers.csv")
    supplier_file = WORK / "suppliers.csv"
    download(supplier_path, supplier_file)
    supplier_values = []
    with supplier_file.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if len(row) >= 7:
                try:
                    n = int(row[4])
                except (ValueError, TypeError):
                    continue
                supplier_values.append({"id": row[1], "internal_id": row[2],
                                        "brand": row[3], "nbr_of_articles": n})
    plausible = [x for x in supplier_values if 0 <= x["nbr_of_articles"] <= 100000000]
    report["supplier_metadata"] = {
        "supplier_rows_with_numeric_count": len(supplier_values),
        "rows_with_count_under_100m": len(plausible),
        "sum_of_counts_under_100m_not_deduplicated": sum(x["nbr_of_articles"] for x in plausible),
        "warning": "NbrOfArticles is supplier metadata; it may not be a unique article count for this exported CSV."
    }

    for table in TABLES:
        pattern = (re.compile(r"^articles\.csv$") if table == "articles.csv" else
                   re.compile(r"^" + re.escape(table) + r"\.csv(?:\.\d{3})?$"))
        parts = sorted((x["path"] for x in files if pattern.fullmatch(x["path"])),
                       key=part_sort_key)
        if not parts:
            report["tables"][table] = {"missing": True}
            continue
        merged = WORK / (table.replace(".csv", "") + "-merged.tsv")
        with merged.open("wb") as out:
            for idx, part in enumerate(parts, 1):
                local = WORK / Path(part).name
                if not local.exists() or local.stat().st_size == 0:
                    print("Downloading", part, idx, "/", len(parts), flush=True)
                    download(part, local)
                with local.open("rb") as src:
                    while True:
                        block = src.read(1024 * 1024)
                        if not block:
                            break
                        out.write(block)
        rows = 0
        unique_ids = set() if table == "articles.csv" else None
        batch = []
        min_id = None
        max_id = None
        with merged.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            for row in reader:
                if not row or not row[0].strip():
                    continue
                rows += 1
                article_id = row[0].strip()
                try:
                    number = int(article_id)
                    min_id = number if min_id is None else min(min_id, number)
                    max_id = number if max_id is None else max(max_id, number)
                except ValueError:
                    pass
                if unique_ids is not None:
                    unique_ids.add(article_id)
                elif table != "articles_linkages":
                    # articles_linkages starts with item_type, not article_id.
                    batch.append((table, article_id))
                    if len(batch) >= 50000:
                        db.executemany("INSERT INTO ids VALUES (?,?)", batch)
                        db.commit()
                        batch = []
        if batch:
            db.executemany("INSERT INTO ids VALUES (?,?)", batch)
            db.commit()
        result = {"parts": len(parts), "rows": rows, "min_article_id": min_id,
                  "max_article_id": max_id}
        if unique_ids is not None:
            result["unique_article_ids"] = len(unique_ids)
        elif table == "articles_linkages":
            result["article_id_reference_check"] = "not_applicable: first column is item_type"
            result["unique_article_ids_referenced"] = None
        else:
            result["unique_article_ids_referenced"] = None
        report["tables"][table] = result
        print(table, json.dumps(result), flush=True)

    for table in TABLES:
        if (table in ("articles.csv", "articles_linkages") or table not in report["tables"]
                or report["tables"][table].get("missing")):
            continue
        counts = db.execute(
            "SELECT COUNT(DISTINCT article_id), MIN(CAST(article_id AS INTEGER)), "
            "MAX(CAST(article_id AS INTEGER)) FROM ids WHERE table_name=?", (table,)
        ).fetchone()
        report["tables"][table]["unique_article_ids_referenced"] = counts[0]
        report["tables"][table]["min_article_id"] = counts[1]
        report["tables"][table]["max_article_id"] = counts[2]

    article_file = WORK / "articles-merged.tsv"
    article_ids = set()
    with article_file.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle, delimiter="\t"):
            if row and row[0].strip():
                article_ids.add(row[0].strip())
    report["master_article_ids"] = len(article_ids)
    report["reference_checks"] = {}
    for table in TABLES:
        if (table in ("articles.csv", "articles_linkages") or table not in report["tables"]
                or report["tables"][table].get("missing")):
            continue
        cursor = db.execute("SELECT DISTINCT article_id FROM ids WHERE table_name=?", (table,))
        missing = 0
        total = 0
        for (article_id,) in cursor:
            total += 1
            if article_id not in article_ids:
                missing += 1
        report["reference_checks"][table] = {
            "unique_ids_checked": total,
            "ids_not_found_in_articles_csv": missing
        }

    Path("tecdoc-2018-integrity.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# TecDoc 2Q2018 article-table integrity audit", "",
             "This audit counts the article master table and article-related tables separately.", ""]
    for name, values in report["tables"].items():
        if values.get("missing"):
            lines.append("- **{}**: missing".format(name))
        else:
            lines.append("- **{}**: {:,} rows; {:,} unique article IDs referenced; ID range {}–{}".format(
                name, values["rows"],
                values.get("unique_article_ids", values.get("unique_article_ids_referenced", 0)),
                values.get("min_article_id"), values.get("max_article_id")))
    lines += ["", "## Master-table reference checks", "",
              "- Article IDs in articles.csv: **{:,}**".format(report["master_article_ids"])]
    if "articles_linkages" in report["tables"] and not report["tables"]["articles_linkages"].get("missing"):
        lines.append("- **articles_linkages**: reference check skipped; first column is item_type, not article_id")
    for name, values in report["reference_checks"].items():
        lines.append("- **{}**: {:,} unique IDs checked; {:,} not found in articles.csv".format(
            name, values["unique_ids_checked"], values["ids_not_found_in_articles_csv"]))
    Path("tecdoc-2018-integrity.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    db.close()


if __name__ == "__main__":
    main()
