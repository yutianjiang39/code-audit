"""独立 .sql 文件：切分、方言判定、表结构、索引与 JOIN 规则，以及 08 类整体。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from auditkit.checks import sqlfile, tests_db
from auditkit.checks.sqlfile import (
    Schema,
    check_schema,
    check_statement,
    detect,
    split_statements,
)
from tests import make_ctx


def stmts(text: str) -> list[str]:
    return [s for _, s in split_statements(text)]


def schema_of(sql: str, dialect: str = "unknown", file: str = "schema.sql") -> Schema:
    sc = Schema()
    for line, s in split_statements(sql):
        sc.feed(s, file, line, dialect)
    return sc


def rules(hits: list[dict]) -> list[str]:
    return [h["rule"] for h in hits]


class Splitter(unittest.TestCase):
    def test_basic_and_line_numbers(self) -> None:
        got = split_statements("SELECT 1;\n\n-- note\nSELECT 2\n  FROM t;\nSELECT 3")
        self.assertEqual(got, [(1, "SELECT 1"), (4, "SELECT 2\n  FROM t"), (6, "SELECT 3")])

    def test_semicolons_inside_strings_and_identifiers(self) -> None:
        self.assertEqual(stmts("INSERT INTO t VALUES ('a;b', \"c;d\", `e;f`); SELECT 'it''s; fine';"),
                         ["INSERT INTO t VALUES ('a;b', \"c;d\", `e;f`)", "SELECT 'it''s; fine'"])

    def test_comments_are_dropped(self) -> None:
        self.assertEqual(stmts("/* a; b */ SELECT 1; -- x; y\n# mysql; comment\nSELECT 2;"), ["SELECT 1", "SELECT 2"])
        self.assertEqual(stmts("-- only comments\n/* nothing */\n"), [])

    def test_postgres_dollar_quoting(self) -> None:
        text = "CREATE FUNCTION f() RETURNS int AS $body$\nBEGIN\n  RETURN 1; -- inside\nEND;\n$body$ LANGUAGE plpgsql;\nSELECT 2;"
        got = stmts(text)
        self.assertEqual(len(got), 2)
        self.assertIn("RETURN 1;", got[0])
        self.assertEqual(got[1], "SELECT 2")
        self.assertEqual(len(stmts("DO $$ BEGIN PERFORM 1; PERFORM 2; END $$; SELECT 1;")), 2)

    def test_mysql_delimiter(self) -> None:
        text = "DELIMITER //\nCREATE PROCEDURE p()\nBEGIN\n  SELECT 1;\n  SELECT 2;\nEND//\nDELIMITER ;\nSELECT 3;\n"
        got = split_statements(text)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][0], 2)
        self.assertTrue(got[0][1].endswith("END"))
        self.assertEqual(got[1], (8, "SELECT 3"))

    def test_sqlite_trigger_body(self) -> None:
        text = "CREATE TRIGGER trg AFTER INSERT ON t BEGIN\n  UPDATE t SET n = n + 1 WHERE id = NEW.id;\n  DELETE FROM log WHERE id < 0;\nEND;\nSELECT 1;"
        got = stmts(text)
        self.assertEqual(len(got), 2)
        self.assertTrue(got[0].rstrip().upper().endswith("END"))


class Dialect(unittest.TestCase):
    def test_detect(self) -> None:
        self.assertEqual(detect("CREATE TABLE `u` (id INT AUTO_INCREMENT PRIMARY KEY) ENGINE=InnoDB;"), "mysql")
        self.assertEqual(detect("CREATE TABLE u (id BIGSERIAL PRIMARY KEY, data JSONB); SELECT x::text FROM u;"), "postgresql")
        self.assertEqual(detect("PRAGMA journal_mode=WAL; CREATE TABLE u (id INTEGER PRIMARY KEY AUTOINCREMENT);"), "sqlite")
        self.assertEqual(detect("SELECT id FROM users WHERE id = 1;"), "unknown")

    def test_keywords_inside_strings_do_not_count(self) -> None:
        self.assertEqual(detect("INSERT INTO notes (body) VALUES ('use ENGINE=MyISAM and AUTO_INCREMENT');"), "unknown")

    def test_code_drivers(self) -> None:
        self.assertEqual(sqlfile.detect_code("import psycopg2\n"), "postgresql")
        self.assertEqual(sqlfile.detect_code("import pymysql\n"), "mysql")
        self.assertEqual(sqlfile.detect_code("import sqlite3\n"), "sqlite")
        self.assertEqual(sqlfile.detect_code("x = 1\n"), "sqlite")


class SchemaModel(unittest.TestCase):
    def test_tables_columns_keys(self) -> None:
        sc = schema_of("""
            CREATE TABLE IF NOT EXISTS "orders" (
                id BIGINT NOT NULL,
                user_id INT REFERENCES users(id),
                code VARCHAR(32) UNIQUE,
                total NUMERIC(10, 2) DEFAULT 0,
                created TIMESTAMP WITH TIME ZONE,
                CONSTRAINT pk PRIMARY KEY (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
            CREATE INDEX idx_orders_user ON orders (user_id, created DESC);
            ALTER TABLE orders ADD COLUMN note TEXT;
        """)
        t = sc.tables["orders"]
        self.assertEqual(list(t.columns), ["id", "user_id", "code", "total", "created", "note"])
        self.assertEqual(t.columns["total"], "NUMERIC(10, 2)")
        self.assertEqual(t.pk, ["id"])
        self.assertEqual(t.leading(), {"id", "code", "user_id"})
        self.assertEqual([i.columns for i in t.indexes if not i.implicit], [["user_id", "created"]])
        self.assertEqual(t.fks[0][:2], (["user_id"], "users"))

    def test_index_before_table(self) -> None:
        sc = schema_of("CREATE INDEX i ON t (a); CREATE TABLE t (id INT PRIMARY KEY, a INT);")
        self.assertEqual([i.name for i in sc.tables["t"].indexes], ["i"])


class SchemaRules(unittest.TestCase):
    def test_table_without_primary_key(self) -> None:
        self.assertIn("table-without-primary-key", rules(check_schema(schema_of("CREATE TABLE t (a INT, b INT);", "mysql"))))
        self.assertNotIn("table-without-primary-key", rules(check_schema(schema_of("CREATE TABLE t (a INT PRIMARY KEY);", "mysql"))))
        # SQLite 的普通表自带 rowid
        self.assertNotIn("table-without-primary-key", rules(check_schema(schema_of("CREATE TABLE t (a INT, b INT);", "sqlite"))))

    def test_duplicate_and_redundant_indexes(self) -> None:
        sc = schema_of("""
            CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT, c INT);
            CREATE INDEX i_ab ON t (a, b);
            CREATE INDEX i_a ON t (a);
            CREATE INDEX i_ab2 ON t (a, b);
            CREATE INDEX i_c ON t (c);
            CREATE INDEX i_pk ON t (id);
        """, "mysql")
        hits = {(h["rule"], h["message"].split("索引 ")[1].split("(")[0]) for h in check_schema(sc)
                if h["rule"] in ("duplicate-index", "redundant-index")}
        self.assertEqual(hits, {("redundant-index", "i_a"), ("duplicate-index", "i_ab2"), ("duplicate-index", "i_pk")})

    def test_unique_prefix_index_is_kept(self) -> None:
        sc = schema_of("CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT); CREATE INDEX i_ab ON t (a, b); CREATE UNIQUE INDEX u_a ON t (a);", "mysql")
        self.assertEqual(rules(check_schema(sc)), [])

    def test_too_many_indexes(self) -> None:
        cols = "abcdefgh"
        sql = f"CREATE TABLE t (id INT PRIMARY KEY, {', '.join(c + ' INT' for c in cols)});" + \
              "".join(f"CREATE INDEX i_{c} ON t ({c});" for c in cols)
        self.assertIn("too-many-indexes", rules(check_schema(schema_of(sql, "mysql"))))
        self.assertNotIn("too-many-indexes", rules(check_schema(schema_of(sql, "mysql"), max_indexes=8)))

    def test_mysql_rules(self) -> None:
        sc = schema_of("CREATE TABLE t (id INT PRIMARY KEY, n VARCHAR(10)) ENGINE=MyISAM DEFAULT CHARSET=utf8;", "mysql")
        self.assertEqual(sorted(rules(check_schema(sc))), ["mysql-myisam", "mysql-utf8mb3"])
        sc = schema_of("CREATE TABLE t (id INT PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;", "mysql")
        self.assertEqual(rules(check_schema(sc)), [])

    def test_postgres_rules(self) -> None:
        sc = schema_of("""
            CREATE TABLE users (id BIGSERIAL PRIMARY KEY);
            CREATE TABLE orders (id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id), created TIMESTAMP);
        """, "postgresql")
        self.assertEqual(sorted(rules(check_schema(sc))), ["pg-fk-without-index", "pg-timestamp-without-tz"])
        sc = schema_of("""
            CREATE TABLE users (id BIGSERIAL PRIMARY KEY);
            CREATE TABLE orders (id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id), created TIMESTAMPTZ);
            CREATE INDEX idx_orders_user ON orders (user_id);
        """, "postgresql")
        self.assertEqual(rules(check_schema(sc)), [])

    def test_index_on_existing_table_in_migration(self) -> None:
        sc = Schema()
        sc.feed("CREATE TABLE t (id INT PRIMARY KEY, a INT)", "001_init.sql", 1, "postgresql")
        sc.feed("CREATE INDEX i_a ON t (a)", "002_add_index.sql", 1, "postgresql")
        self.assertEqual(rules(check_schema(sc)), ["pg-index-not-concurrently"])
        sc = Schema()
        sc.feed("CREATE TABLE t (id INT PRIMARY KEY, a INT)", "001_init.sql", 1, "postgresql")
        sc.feed("CREATE INDEX CONCURRENTLY i_a ON t (a)", "002_add_index.sql", 1, "postgresql")
        self.assertEqual(rules(check_schema(sc)), [])


SCHEMA = """
    CREATE TABLE users (id INT PRIMARY KEY, email VARCHAR(255), name VARCHAR(64), created DATETIME);
    CREATE TABLE orders (id INT PRIMARY KEY, user_id INT, user_code VARCHAR(32), status VARCHAR(16), total DECIMAL(10,2));
    CREATE INDEX idx_users_email ON users (email);
    CREATE INDEX idx_orders_user ON orders (user_id);
"""


class StatementRules(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = schema_of(SCHEMA, "mysql")

    def check(self, sql: str, **kw) -> list[str]:
        return rules(check_statement(sql, self.schema, kw.pop("dialect", "mysql"), **kw))

    def test_clean_statements(self) -> None:
        for sql in ("SELECT id, name FROM users WHERE email = ?",
                    "SELECT o.id, u.name FROM orders o JOIN users u ON u.id = o.user_id WHERE o.user_id = ?",
                    "UPDATE users SET name = ? WHERE id = ?", "DELETE FROM orders WHERE id = ?",
                    "INSERT INTO users (id, email) VALUES (?, ?)", "SELECT COUNT(*) FROM users"):
            with self.subTest(sql):
                self.assertEqual(self.check(sql), [])

    def test_select_star(self) -> None:
        self.assertEqual(self.check("SELECT * FROM users WHERE id = 1"), ["select-star"])
        self.assertEqual(self.check("SELECT u.* FROM users u WHERE u.id = 1"), ["select-star"])

    def test_update_delete_without_where(self) -> None:
        self.assertEqual(self.check("DELETE FROM orders"), ["update-delete-without-where"])
        self.assertEqual(self.check("UPDATE users SET name = 'x'"), ["update-delete-without-where"])
        self.assertEqual(self.check("UPDATE users SET name = 'no WHERE here'"), ["update-delete-without-where"])

    def test_like_function_notin_random(self) -> None:
        self.assertIn("leading-wildcard-like", self.check("SELECT id FROM users WHERE email LIKE '%@x.com'"))
        self.assertNotIn("leading-wildcard-like", self.check("SELECT id FROM users WHERE email LIKE 'bob%'"))
        self.assertIn("function-on-filter-column", self.check("SELECT id FROM users WHERE LOWER(email) = ?"))
        self.assertIn("function-on-filter-column", self.check("SELECT id FROM users WHERE DATE(created) = '2024-01-01'"))
        self.assertNotIn("function-on-filter-column", self.check("SELECT LOWER(email) FROM users WHERE id = ?"))
        self.assertIn("not-in-subquery", self.check("SELECT id FROM users WHERE id NOT IN (SELECT user_id FROM orders)"))
        self.assertIn("order-by-random", self.check("SELECT id FROM users ORDER BY RAND() LIMIT 1"))

    def test_filter_column_not_indexed(self) -> None:
        self.assertEqual(self.check("SELECT id FROM orders WHERE status = ?"), ["filter-column-not-indexed"])
        self.assertEqual(self.check("SELECT id FROM orders WHERE status = ? AND user_id = ?"), [])
        self.assertEqual(self.check("SELECT id FROM unknown_table WHERE status = ?"), [])  # 表结构未知就不猜
        self.assertEqual(self.check("SELECT id FROM orders WHERE status = ?", verified=True), [])  # 已有真实执行计划

    def test_join_without_condition(self) -> None:
        self.assertEqual(self.check("SELECT u.id FROM users u JOIN orders o"), ["join-without-condition"])
        self.assertEqual(self.check("SELECT u.id FROM users u, orders o"), ["join-without-condition"])
        self.assertEqual(self.check("SELECT u.id FROM users u, orders o WHERE o.user_id = u.id"), [])
        self.assertEqual(self.check("SELECT u.id FROM users u CROSS JOIN orders o"), [])
        self.assertEqual(self.check("SELECT u.id FROM users u JOIN orders o USING (id)"), [])
        self.assertEqual(self.check("SELECT u.id FROM users u LEFT OUTER JOIN orders AS o ON o.user_id = u.id"), [])

    def test_join_column_not_indexed(self) -> None:
        sql = "SELECT u.id FROM users u JOIN orders o ON o.status = u.name"
        self.assertEqual(self.check(sql), ["join-column-not-indexed"])
        self.assertEqual(self.check(sql, verified=True), [])

    def test_join_type_mismatch(self) -> None:
        sql = "SELECT u.id FROM orders o JOIN users u ON u.id = o.user_code"
        self.assertEqual(self.check(sql), ["join-type-mismatch"])
        self.assertEqual(self.check(sql, dialect="sqlite"), [])  # SQLite 是动态类型

    def test_too_many_joins(self) -> None:
        sql = "SELECT a.id FROM t0 a " + " ".join(f"JOIN t{i} x{i} ON x{i}.id = a.id" for i in range(1, 9))
        self.assertEqual(self.check(sql), ["too-many-joins"])
        self.assertEqual(self.check(sql, max_joins=8), [])

    def test_ddl_is_ignored(self) -> None:
        self.assertEqual(self.check("CREATE TABLE x (a INT)"), [])


FILES = {
    "db/schema.sql": """-- 订单库
CREATE TABLE `users` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `email` VARCHAR(255) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `orders` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` INT UNSIGNED NOT NULL,
  `status` VARCHAR(16) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_user` (`user_id`),
  KEY `idx_user_dup` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
""",
    "db/queries.sql": """SELECT * FROM orders WHERE status = 'paid';
SELECT o.id FROM orders o JOIN users u;
DELETE FROM orders;
""",
    "db/empty.sql": "-- 还没写\n",
}


class Category08(unittest.TestCase):
    def run_db(self, files: dict[str, str], cfg: dict | None = None):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        ctx = make_ctx(Path(os.path.realpath(d.name)), files, cfg=cfg)
        tests_db.run_database(ctx)
        return ctx.cat("08")

    def test_sql_only_project_is_actually_checked(self) -> None:
        res = self.run_db(FILES)
        self.assertTrue(res.applicable)
        self.assertTrue(res.tools_used, "只有 .sql 文件的项目必须真的被检查，而不是只算“适用”")
        m = res.metrics
        self.assertEqual((m["sql_statements"], m["sql_from_files"]), (5, 5))
        self.assertEqual(m["sql_static_only"], 3)
        self.assertEqual(m["sql_checked"], 0)
        self.assertEqual(m["sql_dialects"], {"MySQL": 3})
        self.assertEqual(sorted(m["schema_tables"]), ["orders", "users"])
        got = {(f.rule, f.file, f.line) for f in res.findings}
        self.assertEqual(got, {
            ("select-star", "db/queries.sql", 1), ("filter-column-not-indexed", "db/queries.sql", 1),
            ("join-without-condition", "db/queries.sql", 2), ("update-delete-without-where", "db/queries.sql", 3),
            ("duplicate-index", "db/schema.sql", 8)})
        self.assertTrue(any("EXPLAIN（MySQL / PostgreSQL）" in t for t, _ in res.tools_skipped))
        self.assertTrue(any("db/empty.sql" in n for n in res.notes))

    def test_sqlite_file_statements_get_a_real_plan(self) -> None:
        res = self.run_db({"schema.sql": "PRAGMA foreign_keys=ON;\nCREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT, b TEXT);\n"
                                         "CREATE INDEX i_a ON t (a);\n",
                           "q.sql": "SELECT id FROM t WHERE a = ?;\nSELECT id FROM t WHERE b = ?;\n"},
                          cfg={"database": {"dialect": "sqlite"}})
        self.assertEqual(res.metrics["sql_checked"], 2)
        self.assertEqual({(f.rule, f.line) for f in res.findings}, {("full-table-scan", 2)})
        self.assertTrue(any(t.startswith("EXPLAIN QUERY PLAN") for t in res.tools_used))

    def test_sql_in_python_still_works(self) -> None:
        code = ("import sqlite3\n\n\ndef load(db):\n"
                "    db.execute('CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)')\n"
                "    return db.execute('SELECT * FROM kv WHERE v = ?', ('x',)).fetchall()\n")
        res = self.run_db({"app/store.py": code})
        self.assertEqual(res.metrics["sql_from_files"], 0)
        self.assertEqual({f.rule for f in res.findings}, {"select-star", "full-table-scan"})

    def test_project_without_sql_is_not_applicable(self) -> None:
        self.assertFalse(self.run_db({"app/main.py": "print('hi')\n"}).applicable)

    def test_comment_only_sql_is_not_reported_as_checked(self) -> None:
        res = self.run_db({"db/empty.sql": "-- nothing\n"})
        self.assertEqual(res.tools_used, [])
        self.assertTrue(any("没有解析出任何语句" in n for n in res.notes))


if __name__ == "__main__":
    unittest.main()
