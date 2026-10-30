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
            splitter.load_specs(db, folder, {"1": "2Q2018:1", "203": "2Q2018:203"})
            rows = list(db.execute("SELECT part_id, name, value FROM specs"))
            self.assertEqual(rows, [("2Q2018:1", "длина", "1040 мм")])
            db.close()

    def test_crosses_skip_ambiguous_normalized_target_numbers(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            # Two printed numbers collapse to the same normalized SKU. The importer
            # must not guess which target supplier/article was intended.
            (folder / "articles.csv").write_text(
                "100\tSRC-100\t10\t1\tSource part\n"
                "200\tAB-12\t20\t1\tTarget A\n"
                "201\tAB12\t20\t1\tTarget B\n",
                encoding="utf-8",
            )
            (folder / "article_cross_list.csv").write_text(
                "100\t20\tAB/12\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            splitter.load_crosses(
                db, folder, {"100": "2Q2018:100", "200": "2Q2018:200", "201": "2Q2018:201"}
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM analogues").fetchone()[0], 0
            )
            db.close()

    def test_media_metadata_without_resolved_part_is_skipped(self):
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
                db, folder, {"100": "2Q2018:100", "200": "2Q2018:200", "300": "2Q2018:300"}
            )
            pairs = set(db.execute("SELECT source_part_id, target_part_id FROM analogues"))
            self.assertEqual(count, 1)
            self.assertEqual(pairs, {("2Q2018:100", "2Q2018:200")})
            db.close()

    def test_crosses_ignore_blank_target_article_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "articles.csv").write_text(
                "100\tSRC-100\t10\t1\tSource part\n", encoding="utf-8"
            )
            (folder / "article_cross_list.csv").write_text(
                "100\t999\tUNKNOWN\n", encoding="utf-8"
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            count = splitter.load_crosses(db, folder, {"100": "2Q2018:100"})
            self.assertEqual(count, 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM analogues").fetchone()[0], 0)
            db.close()

    def test_articles_with_same_normalized_sku_are_not_merged(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "articles.csv").write_text(
                "100\tAB-12\t10\t1\tBrand A part\n"
                "200\tAB12\t20\t1\tBrand B part\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            mapping = splitter.load_articles(
                db, folder, {"10": "BRANDA", "20": "BRANDB"}, "test",
                {"10": "1000", "20": "2000"},
            )
            rows = list(db.execute("SELECT sku,supplier_id,brand,supplier_internal_id FROM parts"))
            self.assertEqual(rows, [("AB12", "10", "BRANDA", "1000"), ("AB12", "20", "BRANDB", "2000")])
            self.assertEqual(mapping, {"100": "test:10:100:AB12", "200": "test:20:200:AB12"})
            db.close()

    def test_cross_reference_keeps_source_direction(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "articles.csv").write_text(
                "100\tSRC-100\t10\t1\tSource part\n"
                "200\tTARGET-200\t20\t1\tTarget part\n",
                encoding="utf-8",
            )
            (folder / "article_cross_list.csv").write_text(
                "100\t20\tTARGET-200\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            count = splitter.load_crosses(
                db, folder, {"100": "2Q2018:100", "200": "2Q2018:200"}
            )
            self.assertEqual(count, 1)
            self.assertEqual(
                list(db.execute("SELECT source_part_id,target_part_id FROM analogues")),
                [("2Q2018:100", "2Q2018:200")],
            )
            db.close()


    def test_duplicate_skus_export_as_multiple_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            db.executemany(
                "INSERT INTO parts(part_id,sku,source_snapshot,article_id,supplier_id,brand,name) VALUES(?,?,?,?,?,?,?)",
                [
                    ("2Q2018:100", "AB12", "2Q2018", "100", "10", "BRANDA", "Part A"),
                    ("2Q2018:200", "AB12", "2Q2018", "200", "20", "BRANDB", "Part B"),
                ],
            )
            splitter.export_shards(db, root)
            import json
            shard = json.loads((root / "AB.json").read_text(encoding="utf-8"))
            self.assertEqual(len(shard["AB12"]), 2)
            self.assertEqual({p["part_id"] for p in shard["AB12"]}, {"2Q2018:100", "2Q2018:200"})
            self.assertEqual({p["manufacturer_id"] for p in shard["AB12"]}, {"10", "20"})
            self.assertEqual({p["internal_id"] for p in shard["AB12"]}, {"100", "200"})
            db.close()




    def test_normalized_details_require_unique_manufacturer_and_sku(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "parts.csv").write_text(
                "sku,supplier_id,article_id,brand,name\n"
                "AB-123,10,100,BRANDA,Part A\n"
                "AB-123,20,200,BRANDB,Part B\n",
                encoding="utf-8",
            )
            (folder / "specifications.csv").write_text(
                "sku,supplier_id,name,value\n"
                "AB123,20,length,5 mm\n"
                "AB123,,width,10 mm\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            self.assertTrue(splitter.import_normalized(db, folder))
            self.assertEqual(
                list(db.execute("SELECT part_id,name FROM specs")),
                [("normalized:20:200:AB123", "length")],
            )
            db.close()

    def test_media_article_index_uses_supplier_internal_id_across_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "articles.csv").write_text(
                "900\tAB-123\t10\t1\tPart\n", encoding="utf-8"
            )
            index = splitter.load_article_identity_index(folder, {"10": "1000"})
            self.assertEqual(index, {"900": ("1000", "AB123", "10")})

    def test_snapshot_media_rebinds_by_supplier_internal_id_and_sku(self):
        db = sqlite3.connect(":memory:")
        splitter.create_db(db)
        db.executemany(
            """INSERT INTO parts(
                 part_id,sku,source_snapshot,article_id,supplier_id,supplier_internal_id,brand,name
               ) VALUES(?,?,?,?,?,?,?,?)""",
            [
                ("2Q2018:10:100:AB12", "AB12", "2Q2018", "100", "10", "1000", "BRANDA", "A"),
                ("2Q2018:20:200:AB12", "AB12", "2Q2018", "200", "20", "2000", "BRANDB", "B"),
            ],
        )
        resolved = splitter.resolve_snapshot_article_map(
            db, {"900": ("1000", "AB12"), "901": ("2000", "AB12")}
        )
        self.assertEqual(
            resolved,
            {"900": "2Q2018:10:100:AB12", "901": "2Q2018:20:200:AB12"},
        )
        db.close()

    def test_snapshot_media_does_not_guess_ambiguous_same_supplier_sku(self):
        db = sqlite3.connect(":memory:")
        splitter.create_db(db)
        db.executemany(
            """INSERT INTO parts(
                 part_id,sku,source_snapshot,article_id,supplier_id,supplier_internal_id
               ) VALUES(?,?,?,?,?,?)""",
            [
                ("2Q2018:10:100:AB12", "AB12", "2Q2018", "100", "10", "1000"),
                ("2Q2018:10:101:AB12", "AB12", "2Q2018", "101", "10", "1000"),
            ],
        )
        resolved = splitter.resolve_snapshot_article_map(
            db, {"900": ("1000", "AB12")}
        )
        self.assertEqual(resolved, {})
        db.close()

    def test_drawings_use_article_id_and_supplier_scoped_image_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "article_mediainformation.csv").write_text(
                "900\tPicture\t\t1\t\t\t5\t\tAB123.jpg\tFront view\n",
                encoding="utf-8",
            )
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            part_id = "2Q2018:10:100:AB123"
            db.execute(
                """INSERT INTO parts(
                     part_id,sku,source_snapshot,article_id,supplier_id,supplier_internal_id
                   ) VALUES(?,?,?,?,?,?)""",
                (part_id, "AB123", "2Q2018", "100", "11", "1000"),
            )
            count = splitter.load_drawings(
                db, folder, {"900": part_id}, {"images/10/AB/AB123.jpg"},
                {"900": "10"},
            )
            self.assertEqual(count, 1)
            url = db.execute("SELECT url FROM drawings WHERE part_id=?", (part_id,)).fetchone()[0]
            self.assertTrue(url.endswith("/images/10/AB/AB123.jpg"))
            db.close()

    def test_vehicle_compatibility_joins_article_supplier_and_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            fixtures = {
                "manufacturers.csv": "1\tFORD\t0\tFord\t1\n",
                "models.csv": "2\t1\t2009-01-01\t2013-01-01\tFusion (JU_)\t1\n",
                "passengercars.csv": (
                    "20\t2000\t2\tFORD\t1\t2010-01-01\t2012-12-31"
                    "\tFord Fusion\tFusion 1.4\t1\n"
                ),
                "engines.csv": "30\t3000\t1\t1.4 TDCi\t1\tDiesel\t2010-01-01\t2012-12-31\t1\n",
                "passengercars_link_engines.csv": "20\t30\n",
                "articles_linkages.csv": "1\t2000\t260\t10\tAB-123\t0\t\n",
            }
            for filename, content in fixtures.items():
                (folder / filename).write_text(content, encoding="utf-8")

            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            part_id = "2Q2018:10:100:AB123"
            db.execute(
                """INSERT INTO parts(
                     part_id,sku,source_snapshot,article_id,supplier_id,supplier_internal_id,brand,name
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (part_id, "AB123", "2Q2018", "100", "10", "1000", "BRANDA", "Part"),
            )
            count = splitter.load_vehicle_compatibility(db, folder, {"10": "1000"})
            self.assertEqual(count, 1)
            self.assertEqual(
                list(db.execute(
                    "SELECT brand,model,years,engine FROM compatibility WHERE part_id=?",
                    (part_id,),
                )),
                [("Ford", "Fusion 1.4", "2010-2012", "1.4 TDCi")],
            )
            db.close()

    def test_vehicle_linkage_supplier_uses_supplier_id_not_internal_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            fixtures = {
                "manufacturers.csv": "1\tFORD\t0\tFord\t1\n",
                "models.csv": "2\t1\t2009-01-01\t2013-01-01\tFusion (JU_)\t1\n",
                "passengercars.csv": "20\t2000\t2\tFORD\t1\t2010-01-01\t2012-12-31\tFord Fusion\tFusion 1.4\t1\n",
                "engines.csv": "30\t3000\t1\t1.4 TDCi\t1\tDiesel\t2010-01-01\t2012-12-31\t1\n",
                "passengercars_link_engines.csv": "20\t30\n",
                # supplier column is suppliers.id (10), not InternalID (1000).
                "articles_linkages.csv": "1\t2000\t260\t10\tAB123\t0\t\n",
            }
            for filename, content in fixtures.items():
                (folder / filename).write_text(content, encoding="utf-8")
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            db.execute(
                """INSERT INTO parts(
                     part_id,sku,source_snapshot,article_id,supplier_id,supplier_internal_id
                   ) VALUES(?,?,?,?,?,?)""",
                ("2Q2018:10:100:AB123", "AB123", "2Q2018", "100", "10", "1000"),
            )
            count = splitter.load_vehicle_compatibility(db, folder, {"10": "1000"})
            self.assertEqual(count, 1)
            db.close()

    def test_vehicle_compatibility_rejects_duplicate_supplier_sku_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            fixtures = {
                "manufacturers.csv": "1\tFORD\t0\tFord\t1\n",
                "models.csv": "2\t1\t2009-01-01\t2013-01-01\tFusion (JU_)\t1\n",
                "passengercars.csv": "20\t2000\t2\tFORD\t1\t2010-01-01\t2012-12-31\tFord Fusion\tFusion 1.4\t1\n",
                "engines.csv": "30\t3000\t1\t1.4 TDCi\t1\tDiesel\t2010-01-01\t2012-12-31\t1\n",
                "passengercars_link_engines.csv": "20\t30\n",
                "articles_linkages.csv": "1\t2000\t260\t10\tAB123\t0\t\n",
            }
            for filename, content in fixtures.items():
                (folder / filename).write_text(content, encoding="utf-8")
            db = sqlite3.connect(":memory:")
            splitter.create_db(db)
            db.executemany(
                """INSERT INTO parts(
                     part_id,sku,source_snapshot,article_id,supplier_id,supplier_internal_id
                   ) VALUES(?,?,?,?,?,?)""",
                [
                    ("2Q2018:10:100:AB123", "AB123", "2Q2018", "100", "10", "1000"),
                    ("2Q2018:10:101:AB123", "AB123", "2Q2018", "101", "10", "1000"),
                ],
            )
            count = splitter.load_vehicle_compatibility(db, folder, {"10": "1000"})
            self.assertEqual(count, 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM compatibility").fetchone()[0], 0)
            db.close()


if __name__ == "__main__":
    unittest.main()
