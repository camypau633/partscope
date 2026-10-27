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
                "1\\t203\\t_NotDefined\\tдлина\\t1040 мм\\t1\\tNumeric\\n"
                "999\\t203\\t_NotDefined\\tширина\\t99 мм\\t1\\tNumeric\\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            splitter.load_specs(db, folder, {"1": "SKU-1", "203": "WRONG-SKU"})
            rows = list(db.execute("SELECT sku, name, value FROM specs"))
            self.assertEqual(rows, [("SKU-1", "длина", "1040 мм")])
            db.close()

    def test_media_metadata_is_not_mistaken_for_part_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "article_mediainformation.csv").write_text(
                "332\\tPicture\\t\\t0\\t\\t\\t5\\t\\tTM206248_WM.JPG\\tPicture\\n",
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

    def test_crosses_resolve_only_explicit_article_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "article_cross_list.csv").write_text(
                "100\t200\tEXTERNAL-REF\n"
                "100\t999\tNOT-IN-ARTICLES\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            db.execute("INSERT INTO parts(sku, article_id) VALUES('AAA100', '100')")
            db.execute("INSERT INTO parts(sku, article_id) VALUES('BBB200', '200')")
            count = splitter.load_crosses(
                db, folder, {"100": "AAA100", "200": "BBB200"}
            )
            pairs = set(db.execute("SELECT source_sku, sku FROM analogues"))
            self.assertEqual(count, 2)
            self.assertEqual(pairs, {("AAA100", "BBB200"), ("BBB200", "AAA100")})
            db.close()


if __name__ == "__main__":
    unittest.main()
