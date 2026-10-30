import sqlite3
import tempfile
import unittest
from pathlib import Path

import splitter


class TecDocJoinTests(unittest.TestCase):
    def test_supplier_join_uses_best_matching_id_column(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "suppliers.csv").write_text(
                "999\t10\t1\tBRANDA\t700\t0\tBRANDA\n"
                "888\t20\t2\tBRANDB\t800\t0\tBRANDB\n",
                encoding="utf-8",
            )
            mapping = splitter.load_suppliers(folder, {"10", "20"})
            self.assertEqual(mapping, {"10": "BRANDA", "20": "BRANDB"})

    def test_specs_use_article_id_from_first_column_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "article_attributes.csv").write_text(
                "1\t203\t_NotDefined\tдлина\t1040 мм\t1\tNumeric\n"
                "999\t203\t_NotDefined\tширина\t99 мм\t1\tNumeric\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            splitter.load_specs(db, folder, {"1": "SKU-1", "203": "WRONG-SKU"})
            rows = list(db.execute("SELECT sku, name, value FROM specs"))
            self.assertEqual(rows, [("SKU-1", "длина", "1040 мм")])
            db.close()

    def test_crosses_skip_ambiguous_normalized_target_numbers(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            # Two printed numbers collapse to the same normalized SKU. The importer
            # must not guess which target supplier/article was intended.
            (folder / "articles.csv").write_text(
                "100\\tSRC-100\\t10\\t1\\tSource part\\n"
                "200\\tAB-12\\t20\\t1\\tTarget A\\n"
                "201\\tAB12\\t20\\t1\\tTarget B\\n",
                encoding="utf-8",
            )
            (folder / "article_cross_list.csv").write_text(
                "100\\t20\\tAB/12\\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            splitter.load_crosses(
                db, folder, {"100": "SRC100", "200": "AB12", "201": "AB12"}
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM analogues").fetchone()[0], 0
            )
            db.close()

    def test_media_metadata_is_not_mistaken_for_part_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "article_mediainformation.csv").write_text(
                "332\tPicture\t\t0\t\t\t5\t\tTM206248_WM.JPG\tPicture\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            count = splitter.load_drawings(
                db, folder, {"332": "SKU-332"}, {"images/TM206248_WM.JPG"}
            )
            self.assertEqual(count, 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM drawings").fetchone()[0], 0)
            db.close()

    def test_crosses_resolve_target_by_supplier_and_article_number(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            # articles.csv layout: article ID, article number, supplier ID, generic ID, description
            (folder / "articles.csv").write_text(
                "100\tSRC-100\t10\t1\tSource part\n"
                "200\tTARGET-200\t20\t1\tTarget part\n"
                "300\tOTHER-300\t30\t1\tOther part\n",
                encoding="utf-8",
            )
            # article_cross_list layout: source article ID, target supplier ID, target article number
            (folder / "article_cross_list.csv").write_text(
                "100\t20\tTARGET-200\n"
                "100\t999\tNOT-IN-ARTICLES\n"
                "999\t20\tTARGET-200\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            count = splitter.load_crosses(
                db, folder, {"100": "SRC100", "200": "TARGET200", "300": "OTHER300"}
            )
            pairs = set(db.execute("SELECT source_sku, sku FROM analogues"))
            self.assertEqual(count, 1)
            self.assertEqual(pairs, {("SRC100", "TARGET200")})
            db.close()

    def test_articles_with_same_normalized_sku_are_not_merged(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "articles.csv").write_text(
                "100\\tAB-12\\t10\\t1\\tBrand A part\\n"
                "200\\tAB12\\t20\\t1\\tBrand B part\\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            mapping = splitter.load_articles(db, folder, {"10": "BRANDA", "20": "BRANDB"}, "test")
            rows = list(db.execute("SELECT sku,supplier_id,brand FROM parts"))
            self.assertEqual(rows, [("AB12", "10", "BRANDA")])
            self.assertEqual(mapping, {"100": "AB12"})
            db.close()

    def test_cross_reference_keeps_source_direction(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "articles.csv").write_text(
                "100\\tSRC-100\\t10\\t1\\tSource part\\n"
                "200\\tTARGET-200\\t20\\t1\\tTarget part\\n",
                encoding="utf-8",
            )
            (folder / "article_cross_list.csv").write_text(
                "100\\t20\\tTARGET-200\\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            count = splitter.load_crosses(
                db, folder, {"100": "SRC100", "200": "TARGET200"}
            )
            self.assertEqual(count, 1)
            self.assertEqual(
                list(db.execute("SELECT source_sku,sku FROM analogues")),
                [("SRC100", "TARGET200")],
            )
            db.close()


if __name__ == "__main__":
    unittest.main()
