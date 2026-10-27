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
