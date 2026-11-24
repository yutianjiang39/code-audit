"""SQL 静态检查：语句切分、方言判定、表结构模型，以及不需要连接数据库的规则（索引、JOIN、方言）。

只用标准库。这里不做执行计划分析——SQLite 的 EXPLAIN 在 tests_db.py；MySQL / PostgreSQL 需要真实的服务器，本系统不做。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

IDENT = r'(?:`[^`]+`|"[^"]+"|\[[^\]]+\]|[A-Za-z_][\w$]*)'
QNAME = rf"{IDENT}(?:\s*\.\s*{IDENT})*"
_STRING = re.compile(r"'(?:[^'\\]|\\.|'')*'", re.S)
_DOLLAR = re.compile(r"\$[A-Za-z_]*\$")
_TRIGGER = re.compile(r"\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TRIGGER\b", re.I)
NOT_ALIAS = {"on", "using", "where", "join", "inner", "left", "right", "full", "cross", "natural", "group", "order",
             "limit", "having", "union", "set", "as", "outer", "window", "for", "offset", "fetch", "returning",
             "values", "straight_join", "lateral", "select", "except", "intersect", "partition", "force", "use"}
DML_HEAD = re.compile(r"\s*(SELECT|INSERT|UPDATE|DELETE|WITH|REPLACE)\b", re.I)


# ---------------------------------------------------------------- 切分
def split_statements(text: str) -> list[tuple[int, str]]:
    """把脚本切成语句，返回 [(起始行号, 语句)]。注释被去掉，字符串原样保留。

    处理：单 / 双引号与反引号、`--` 与 `/* */` 注释、PostgreSQL 的 $tag$…$tag$、MySQL 的 DELIMITER、
    SQLite 触发器里 BEGIN … END 之间的分号。
    """
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    delim = ";"
    i, n, line, start = 0, len(text), 1, 0

    def flush() -> None:
        nonlocal buf, start
        s = "".join(buf).strip()
        if s:
            out.append((start, s))
        buf, start = [], 0

    def take(s: str) -> None:
        nonlocal line, start
        if not start and s.strip():
            start = line + s[:len(s) - len(s.lstrip())].count("\n")
        buf.append(s)
        line += s.count("\n")

    directive = re.compile(r"[ \t]*DELIMITER[ \t]+(\S+)[ \t]*(?:\r?\n|$)", re.I)
    while i < n:
        c = text[i]
        if not start and (i == 0 or text[i - 1] == "\n"):
            m = directive.match(text, i)
            if m:
                delim = m.group(1)
                line += m.group(0).count("\n")
                i = m.end()
                buf = []
                continue
        if text.startswith("--", i) or (c == "#" and (i == 0 or text[i - 1] == "\n")):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += text.count("\n", i, j)
            buf.append(" ")
            i = j
            continue
        if c in "'\"`":
            j = i + 1
            while j < n:
                if text[j] == "\\" and c != "`" and j + 1 < n:
                    j += 2
                elif text[j] == c:
                    if j + 1 < n and text[j + 1] == c:
                        j += 2
                    else:
                        break
                else:
                    j += 1
            take(text[i:j + 1])
            i = j + 1
            continue
        if c == "$":
            m = _DOLLAR.match(text, i)
            if m:
                j = text.find(m.group(0), m.end())
                j = n if j < 0 else j + len(m.group(0))
                take(text[i:j])
                i = j
                continue
        if text.startswith(delim, i):
            if delim == ";" and start:
                cur = "".join(buf)
                if _TRIGGER.match(cur) and not re.search(r"\bEND\s*$", cur, re.I):
                    take(";")
                    i += 1
                    continue
            flush()
            i += len(delim)
            continue
        take(c)
        i += 1
    flush()
    return out


# ---------------------------------------------------------------- 方言
_MYSQL = (r"`\w+`", r"\bENGINE\s*=", r"\bAUTO_INCREMENT\b", r"^\s*DELIMITER\b", r"\bUNSIGNED\b", r"\bCHARSET\s*=",
          r"\bON\s+DUPLICATE\s+KEY\b", r"\b(TINY|MEDIUM|LONG)(TEXT|BLOB)\b")
_PG = (r"\b(BIG|SMALL)?SERIAL\b", r"\$\$", r"::\s*\w+", r"\bRETURNING\b", r"\bILIKE\b", r"\bJSONB\b", r"\$\d+\b",
       r"\bTIMESTAMPTZ\b", r"\bCONCURRENTLY\b", r"\bCREATE\s+EXTENSION\b", r"\bLANGUAGE\s+plpgsql\b", r"\bBYTEA\b")
_SQLITE = (r"\bAUTOINCREMENT\b", r"\bPRAGMA\b", r"\bWITHOUT\s+ROWID\b", r"\bINSERT\s+OR\s+(REPLACE|IGNORE)\b",
           r"\bsqlite_master\b", r"\bstrftime\s*\(")
_CODE = (("postgresql", r"psycopg|asyncpg|tokio_postgres|sqlx::postgres|postgres(ql)?://|PgPool|pg8000"),
         ("mysql", r"pymysql|mysql\.connector|MySQLdb|aiomysql|mysql://|sqlx::mysql|MySqlPool|mysql_async"),
         ("sqlite", r"sqlite3|rusqlite|aiosqlite|sqlx::sqlite|SqlitePool|sqlite://"))


def detect(text: str) -> str:
    """按 .sql 文件里的特征写法判断方言：mysql / postgresql / sqlite / unknown。"""
    t = _STRING.sub("''", text)
    score = {name: sum(1 for p in pats if re.search(p, t, re.I | re.M))
             for name, pats in (("mysql", _MYSQL), ("postgresql", _PG), ("sqlite", _SQLITE))}
    best = max(score, key=score.get)
    return best if score[best] and list(score.values()).count(score[best]) == 1 else "unknown"


def detect_code(text: str) -> str:
    """代码文件用的是哪种数据库驱动；看不出来按 SQLite 处理（会用内存库验证，验证不了的记为未核查）。"""
    hits = [name for name, pat in _CODE if re.search(pat, text)]
    return hits[0] if len(hits) == 1 else "sqlite" if "sqlite" in hits or not hits else "unknown"


# ---------------------------------------------------------------- 表结构
def norm(name: str) -> str:
    last = re.findall(IDENT, name)[-1] if re.findall(IDENT, name) else name
    return last.strip('`"[]').lower()


@dataclass
class Index:
    name: str
    table: str
    columns: list[str]
    unique: bool = False
    implicit: bool = False  # 由 PRIMARY KEY / UNIQUE 约束带出来的
    concurrently: bool = False
    dialect: str = "unknown"
    file: str = ""
    line: int = 0


@dataclass
class Table:
    name: str
    file: str = ""
    line: int = 0
    dialect: str = "unknown"
    is_test: bool = False
    columns: dict[str, str] = field(default_factory=dict)  # 列名 -> 类型
    pk: list[str] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    fks: list[tuple[list[str], str, list[str]]] = field(default_factory=list)
    options: str = ""
    without_rowid: bool = False

    def leading(self) -> set[str]:
        return {i.columns[0] for i in self.indexes if i.columns} | set(self.pk[:1])

    def covered(self, cols: list[str]) -> bool:
        """cols 是某个索引（或主键）的前缀。"""
        keys = [i.columns for i in self.indexes] + [self.pk]
        return any(k[:len(cols)] == cols for k in keys if k)


def _close(s: str, i: int) -> int:
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _split_top(s: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _cols(s: str) -> list[str]:
    """`(a, b DESC, c(10))` 里的列名；表达式索引记成 <expr>。"""
    out = []
    for p in _split_top(s):
        m = re.match(rf"({IDENT})\s*(?:\(\s*\d+\s*\))?\s*(?:COLLATE\s+\S+\s*)?(?:ASC|DESC)?\s*(?:NULLS\s+\w+)?\s*$", p, re.I)
        out.append(norm(m.group(1)) if m else "<expr>")
    return out


_CONSTRAINT_WORD = re.compile(r"(PRIMARY|NOT|NULL|UNIQUE|DEFAULT|REFERENCES|CHECK|COLLATE|GENERATED|CONSTRAINT|"
                              r"AUTO_INCREMENT|AUTOINCREMENT|COMMENT|ON)\b", re.I)
_TYPE = re.compile(r"[A-Za-z_]\w*(?:\s+(?:VARYING|PRECISION))?\s*(?:\([^)]*\))?"
                   r"(?:\s+(?:UNSIGNED|ZEROFILL|WITH(?:OUT)?\s+TIME\s+ZONE))*(?:\s*\[\])?", re.I)
_CREATE_TABLE = re.compile(rf"\s*CREATE\s+(?:(?:GLOBAL\s+|LOCAL\s+)?(?:TEMP|TEMPORARY|UNLOGGED)\s+)?TABLE\s+"
                           rf"(?:IF\s+NOT\s+EXISTS\s+)?({QNAME})\s*\(", re.I)
_CREATE_INDEX = re.compile(rf"\s*CREATE\s+(UNIQUE\s+)?(?:FULLTEXT\s+|SPATIAL\s+)?INDEX\s+(CONCURRENTLY\s+)?"
                           rf"(?:IF\s+NOT\s+EXISTS\s+)?(?:({QNAME})\s+)?ON\s+(?:ONLY\s+)?({QNAME})\s*(?:USING\s+\w+\s*)?\(", re.I)
_ALTER = re.compile(rf"\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?({QNAME})\s+", re.I)
_FK = re.compile(rf"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+({QNAME})\s*(?:\(([^)]*)\))?", re.I)


class Schema:
    """从 CREATE TABLE / CREATE INDEX / ALTER TABLE ADD 里拼出来的表结构。"""

    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}
        self.indexes: list[Index] = []  # 所有显式 CREATE INDEX，包括表结构未知的

    def feed(self, sql: str, file: str = "", line: int = 0, dialect: str = "unknown", is_test: bool = False) -> None:
        s = _STRING.sub("''", sql)
        m = _CREATE_TABLE.match(s)
        if m:
            self._table(s, m, file, line, dialect, is_test)
            return
        m = _CREATE_INDEX.match(s)
        if m:
            end = _close(s, m.end() - 1)
            idx = Index(name=norm(m.group(3)) if m.group(3) else "", table=norm(m.group(4)),
                        columns=_cols(s[m.end():end]) if end > 0 else [], unique=bool(m.group(1)),
                        concurrently=bool(m.group(2)), dialect=dialect, file=file, line=line)
            self.indexes.append(idx)
            if idx.table in self.tables:
                self.tables[idx.table].indexes.append(idx)
            return
        m = _ALTER.match(s)
        if m and norm(m.group(1)) in self.tables:
            t = self.tables[norm(m.group(1))]
            for action in _split_top(s[m.end():]):
                a = re.match(r"ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(.*)", action, re.I | re.S)
                if a:
                    self._item(t, a.group(1), file, line)

    def _table(self, s: str, m: re.Match, file: str, line: int, dialect: str, is_test: bool) -> None:
        name = norm(m.group(1))
        end = _close(s, m.end() - 1)
        if end < 0 or name in self.tables:  # 先出现的定义为准（调用方把非测试代码排在前面）
            return
        t = Table(name=name, file=file, line=line, dialect=dialect, is_test=is_test, options=s[end + 1:])
        t.without_rowid = bool(re.search(r"WITHOUT\s+ROWID", t.options, re.I))
        self.tables[name] = t
        for item in _split_top(s[m.end():end]):
            self._item(t, item, file, line)
        for idx in self.indexes:
            if idx.table == name and idx not in t.indexes:
                t.indexes.append(idx)

    def _item(self, t: Table, item: str, file: str, line: int) -> None:
        body = re.sub(rf"^CONSTRAINT\s+{IDENT}\s+", "", item.strip(), flags=re.I)
        m = re.match(r"PRIMARY\s+KEY\s*\(", body, re.I)
        if m:
            t.pk = _cols(body[m.end():_close(body, m.end() - 1)])
            return
        fk = _FK.match(body)
        if fk:
            t.fks.append((_cols(fk.group(1)), norm(fk.group(2)), _cols(fk.group(3) or "")))
            return
        m = re.match(rf"(UNIQUE\s+)?(?:KEY|INDEX)\s*({IDENT})?\s*(?:USING\s+\w+\s*)?\(|(UNIQUE)\s*({IDENT})?\s*\(", body, re.I)
        if m:
            unique = bool(m.group(1) or m.group(3))
            t.indexes.append(Index(name=norm(m.group(2) or m.group(4) or ""), table=t.name, unique=unique,
                                   columns=_cols(body[m.end():_close(body, m.end() - 1)]),
                                   implicit=bool(m.group(3)), file=file, line=line))
            return
        if re.match(r"(CHECK|EXCLUDE|FULLTEXT|SPATIAL|LIKE|PERIOD)\b", body, re.I):
            return
        m = re.match(rf"({IDENT})\s*(.*)", body, re.S)
        if not m:
            return
        col, rest = norm(m.group(1)), m.group(2)
        tm = None if _CONSTRAINT_WORD.match(rest) else _TYPE.match(rest)
        t.columns[col] = " ".join(tm.group(0).split()).upper() if tm else ""
        tail = rest[tm.end():] if tm else rest
        if re.search(r"\bPRIMARY\s+KEY\b", tail, re.I):
            t.pk = [col]
        if re.search(r"\bUNIQUE\b", tail, re.I):
            t.indexes.append(Index(name="", table=t.name, columns=[col], unique=True, implicit=True, file=file, line=line))
        ref = re.search(rf"\bREFERENCES\s+({QNAME})\s*(?:\(([^)]*)\))?", tail, re.I)
        if ref:
            t.fks.append(([col], norm(ref.group(1)), _cols(ref.group(2) or "")))


def family(col_type: str) -> str | None:
    t = col_type.upper()
    for fam, pat in (("int", r"\b(TINY|SMALL|MEDIUM|BIG)?INT(EGER|[248])?\b|SERIAL"), ("uuid", r"UUID"),
                     ("text", r"CHAR|TEXT|CLOB|ENUM"), ("num", r"REAL|FLOAT|DOUBLE|DEC|NUMERIC|MONEY"),
                     ("time", r"DATE|TIME"), ("bool", r"BOOL"), ("json", r"JSON"), ("blob", r"BLOB|BYTEA|BINARY")):
        if re.search(pat, t):
            return fam
    return None


# ---------------------------------------------------------------- 规则：表结构
def _f(rule: str, severity: str, message: str, cause: str, fix: str, fix_code: str = "", **kw) -> dict:
    return {"rule": rule, "severity": severity, "message": message, "cause": cause, "fix": fix, "fix_code": fix_code, **kw}


def check_schema(schema: Schema, max_indexes: int = 6) -> list[dict]:
    out: list[dict] = []
    for t in schema.tables.values():
        if t.is_test:
            continue
        at = {"file": t.file, "line": t.line}
        if not t.pk and t.columns and (t.dialect != "sqlite" or t.without_rowid):
            out.append(_f("table-without-primary-key", "medium" if t.dialect in ("mysql", "postgresql") else "low",
                          f"表 {t.name} 没有主键",
                          "没有主键就没有稳定的行标识：InnoDB 会生成隐藏聚簇键，PostgreSQL 的逻辑复制和很多 ORM 都要求主键，"
                          "去重和按行更新也只能靠全表比对。",
                          "加一个自增 / 标识列主键，或把天然唯一的列组合声明为 PRIMARY KEY。", **at))
        explicit = [i for i in t.indexes if not i.implicit]
        # 主键和约束带出来的索引排前面：重复时报告的是后来显式建的那个
        keys = ([(t.pk, None, "主键")] if t.pk else []) + sorted(
            ((i.columns, i, f"索引 {i.name or '(未命名)'}") for i in t.indexes if i.columns and "<expr>" not in i.columns),
            key=lambda k: not k[1].implicit)
        for pos, (cols, idx, _label) in enumerate(keys):
            if idx is None or idx.implicit:
                continue
            ia = {"file": idx.file or t.file, "line": idx.line or t.line}
            same = [lab for c, _o, lab in keys[:pos] if c == cols]
            longer = [lab + "(" + ", ".join(c) + ")" for c, o, lab in keys if o is not idx and len(c) > len(cols) and c[:len(cols)] == cols]
            if same:
                out.append(_f("duplicate-index", "medium", f"表 {t.name} 的索引 {idx.name or '(未命名)'}({', '.join(cols)}) 与{same[0]}的列完全相同",
                              "两个索引覆盖同样的列：每次写入都要多维护一份，占空间，对查询没有任何好处。",
                              "删掉其中一个。", f"DROP INDEX {idx.name};" if idx.name else "", **ia))
            elif longer and not idx.unique:
                out.append(_f("redundant-index", "low", f"表 {t.name} 的索引 {idx.name or '(未命名)'}({', '.join(cols)}) 是{longer[0]}的前缀",
                              "复合索引的最左前缀已经能服务这些列上的查询，单独的短索引通常是多余的写入开销。",
                              "确认没有查询专门依赖这个短索引（比如需要更小的索引做覆盖扫描）后删除。",
                              f"DROP INDEX {idx.name};" if idx.name else "", **ia))
        if len(explicit) > max_indexes:
            out.append(_f("too-many-indexes", "low", f"表 {t.name} 有 {len(explicit)} 个索引（阈值 {max_indexes}）",
                          "每个索引都会拖慢 INSERT / UPDATE / DELETE，并占用缓存；索引多往往说明有重叠或已经没人用的。",
                          "结合实际查询合并成复合索引，删掉用不上的；阈值可以用 profile 的 database.max_indexes 调整。", **at))
        if t.dialect == "postgresql":
            for cols, ref, _ in t.fks:
                if not t.covered(cols):
                    out.append(_f("pg-fk-without-index", "medium", f"表 {t.name} 的外键 ({', '.join(cols)}) → {ref} 没有索引",
                                  "PostgreSQL 不会给外键的引用列自动建索引：删除 / 更新父表行时要全表扫描子表，按外键 JOIN 也走不了索引。",
                                  "给外键列建索引。", f"CREATE INDEX idx_{t.name}_{'_'.join(cols)} ON {t.name} ({', '.join(cols)});", **at))
            for col, typ in t.columns.items():
                if re.match(r"TIMESTAMP\b", typ) and "WITH TIME ZONE" not in typ:
                    out.append(_f("pg-timestamp-without-tz", "low", f"{t.name}.{col} 是不带时区的 TIMESTAMP",
                                  "timestamp without time zone 存的是“墙上时间”，会话时区一变或跨时区部署，同一时刻就对不上。",
                                  "改用 TIMESTAMPTZ（内部按 UTC 存储）。", f"ALTER TABLE {t.name} ALTER COLUMN {col} TYPE timestamptz;", **at))
        if t.dialect == "mysql":
            if re.search(r"ENGINE\s*=\s*MyISAM", t.options, re.I):
                out.append(_f("mysql-myisam", "medium", f"表 {t.name} 使用 MyISAM 引擎",
                              "MyISAM 没有事务、没有行级锁，崩溃后可能需要修复表，写入时锁整张表。",
                              "改用 InnoDB。", f"ALTER TABLE {t.name} ENGINE=InnoDB;", **at))
            charsets = " ".join([t.options, *t.columns.values()])
            if re.search(r"(CHARSET|CHARACTER\s+SET)\s*=?\s*utf8(mb3)?\b", charsets, re.I):
                out.append(_f("mysql-utf8mb3", "low", f"表 {t.name} 使用 utf8（utf8mb3）字符集",
                              "MySQL 的 utf8 只支持 3 字节，存不了 emoji 和部分生僻字，写入时会报错或被截断；utf8mb3 也已被弃用。",
                              "改用 utf8mb4。", f"ALTER TABLE {t.name} CONVERT TO CHARACTER SET utf8mb4;", **at))
    for idx in schema.indexes:
        t = schema.tables.get(idx.table)
        if idx.concurrently or (t and (t.is_test or t.file == idx.file)):
            continue
        if idx.dialect == "postgresql":
            out.append(_f("pg-index-not-concurrently", "low", f"在已有的表 {idx.table} 上 CREATE INDEX 没有用 CONCURRENTLY",
                          "普通的 CREATE INDEX 在建索引期间阻塞这张表的所有写入；表大的时候线上会卡住。",
                          "迁移里改用 CREATE INDEX CONCURRENTLY（不能放在事务块里）；表很小或可以停机时可以忽略。",
                          file=idx.file, line=idx.line))
    return out


# ---------------------------------------------------------------- 规则：语句
_FUNCS = (r"LOWER|UPPER|DATE|YEAR|MONTH|DAY|SUBSTR|SUBSTRING|TRIM|LTRIM|RTRIM|CAST|COALESCE|IFNULL|NVL|DATE_FORMAT|"
          r"TO_CHAR|STRFTIME|LENGTH|ABS|DATE_TRUNC")
_FUNC_ON_COL = re.compile(rf"\b({_FUNCS})\s*\(\s*({QNAME})\s*(?:(?:,|\bAS\b)[^()]*)?\)\s*(=|<>|!=|<=|>=|<|>|LIKE\b|IN\b|BETWEEN\b)", re.I)
_CLAUSE_END = r"\b(?:GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING|UNION|RETURNING|WINDOW|FETCH|OFFSET)\b"
_JOIN_END = re.compile(r"\b(?:(?:NATURAL\s+|CROSS\s+)?(?:INNER\s+|LEFT\s+|RIGHT\s+|FULL\s+)?(?:OUTER\s+)?JOIN|STRAIGHT_JOIN|WHERE)\b|" + _CLAUSE_END, re.I)
_TABLE_REF = re.compile(rf"\b(FROM|JOIN|UPDATE|INTO)\s+({QNAME})(?:\s+(?:AS\s+)?({IDENT}))?", re.I)
_PRED_COL = re.compile(rf"({QNAME})\s*(?:=|<>|!=|<=|>=|<|>|\bIN\b|\bBETWEEN\b|\bLIKE\b|\bILIKE\b)", re.I)


def _short(sql: str, n: int = 110) -> str:
    return " ".join(sql.split())[:n]


def _where(s: str) -> str:
    m = re.search(r"\bWHERE\b", s, re.I)
    if not m:
        return ""
    rest = s[m.end():]
    e = re.search(_CLAUSE_END, rest, re.I)
    return rest[:e.start()] if e else rest


def _resolve(q: str, aliases: dict[str, str]) -> tuple[str | None, str]:
    parts = [p.strip('`"[]').lower() for p in re.findall(IDENT, q)]
    if len(parts) >= 2:
        return aliases.get(parts[-2], parts[-2]), parts[-1]
    return (next(iter(set(aliases.values()))) if len(set(aliases.values())) == 1 else None), parts[-1]


def check_statement(sql: str, schema: Schema, dialect: str = "unknown", verified: bool = False,
                    max_joins: int = 6) -> list[dict]:
    """单条 DML 语句的静态规则。verified=True 表示已经有真实执行计划，索引类的猜测规则就不再报。"""
    out: list[dict] = []
    s = _STRING.sub("''", sql)
    short = _short(sql)
    if not DML_HEAD.match(s):
        return out
    if re.search(r"\bSELECT\s+(?:DISTINCT\s+)?(?:\w+\.)?\*", s, re.I):
        out.append(_f("select-star", "low", "SELECT *：" + short,
                      "取回所有列：表结构一加列，读取量和按位置取值的代码都会受影响，也用不上覆盖索引。", "列出实际用到的列。"))
    head = re.match(r"\s*(UPDATE|DELETE)\b", s, re.I)
    if head and not re.search(r"\bWHERE\b", s, re.I) and not re.search(r"\bLIMIT\b", s, re.I):
        out.append(_f("update-delete-without-where", "medium", f"{head.group(1).upper()} 没有 WHERE：" + short,
                      "不带条件的 UPDATE / DELETE 会改掉或删掉整张表；如果是有意清表，TRUNCATE 更快也更明确。",
                      "补上 WHERE；确实要清空整张表就改用 TRUNCATE（或 SQLite 的 DELETE + 注释说明）。"))
    if re.search(r"\b(?:I?LIKE)\s+(?:N|E)?'%", sql, re.I) or re.search(r"\bLIKE\s+CONCAT\s*\(\s*'%'", sql, re.I):
        out.append(_f("leading-wildcard-like", "low", "LIKE 以通配符开头：" + short,
                      "前导 % 让 B-tree 索引用不上，只能逐行比较。",
                      "能改成前缀匹配就改（'abc%'）；确实需要包含匹配，用全文索引（MySQL FULLTEXT、PostgreSQL pg_trgm + GIN、SQLite FTS5）。"))
    where = _where(s)
    m = _FUNC_ON_COL.search(where)
    if m and not re.fullmatch(r"\d+|NULL|TRUE|FALSE", m.group(2), re.I):
        out.append(_f("function-on-filter-column", "low", f"WHERE 里对列 {m.group(2)} 套了函数 {m.group(1).upper()}()：" + short,
                      "条件写成 函数(列) 比较 值，列上的普通索引就用不上了，会退化成全表扫描。",
                      "把函数挪到比较的另一侧（例如用范围条件代替 DATE(col) = ?），或建表达式索引 / 生成列。"))
    if re.search(r"\bNOT\s+IN\s*\(\s*SELECT\b", s, re.I):
        out.append(_f("not-in-subquery", "low", "NOT IN (子查询)：" + short,
                      "子查询结果里只要有一个 NULL，NOT IN 就对所有行返回空；多数优化器对它的处理也不如反连接。",
                      "改写成 NOT EXISTS (SELECT 1 … WHERE 关联条件)。"))
    if re.search(r"\bORDER\s+BY\s+(?:RAND|RANDOM)\s*\(", s, re.I):
        out.append(_f("order-by-random", "low", "ORDER BY RAND() / RANDOM()：" + short,
                      "要给每一行算一个随机数再整体排序，表越大越慢。",
                      "按主键范围随机取（WHERE id >= 随机值 LIMIT n），或用 TABLESAMPLE（PostgreSQL）。"))

    aliases: dict[str, str] = {}
    for m in _TABLE_REF.finditer(s):
        t = norm(m.group(2))
        aliases[t] = t
        if m.group(3) and m.group(3).lower() not in NOT_ALIAS:
            aliases[norm(m.group(3))] = t
    n_joins = 0
    comma = re.search(rf"\bFROM\s+{QNAME}(?:\s+(?:AS\s+)?{IDENT})?\s*,\s*{QNAME}", s, re.I)
    if comma and not where.strip():
        out.append(_f("join-without-condition", "medium", "逗号连接多张表却没有 WHERE：" + short,
                      "没有任何连接条件，结果是两张表的笛卡尔积，行数相乘。", "写成显式的 JOIN … ON …。"))
    for m in re.finditer(r"\b((?:NATURAL|CROSS)\s+)?(?:(?:INNER|LEFT|RIGHT|FULL)\s+)?(?:OUTER\s+)?JOIN\b", s, re.I):
        n_joins += 1
        tm = re.match(rf"\s+({QNAME})(?:\s+(?:AS\s+)?({IDENT}))?", s[m.end():])
        if not tm or m.group(1):
            continue
        after = m.end() + (tm.end(1) if (tm.group(2) or "").lower() in NOT_ALIAS or not tm.group(2) else tm.end())
        e = _JOIN_END.search(s, after)
        seg = s[after:e.start() if e else len(s)]
        table = norm(tm.group(1))
        on = re.match(r"\s*(ON|USING)\b(.*)", seg, re.I | re.S)
        if not on:
            out.append(_f("join-without-condition", "medium", f"JOIN {table} 没有 ON / USING：" + short,
                          "没有连接条件的 JOIN 是笛卡尔积；通常是漏写了条件。", "补上 ON 条件；确实要笛卡尔积就写 CROSS JOIN。"))
            continue
        t = schema.tables.get(table)
        if on.group(1).upper() == "USING":
            um = re.match(r"\s*\(([^)]*)\)", on.group(2))
            mine = _cols(um.group(1)) if um else []
            pairs = []
        else:
            pairs = [(_resolve(a, aliases), _resolve(b, aliases)) for a, b in re.findall(rf"({QNAME})\s*=\s*({QNAME})", on.group(2))]
            mine = [c for side in pairs for tb, c in side if tb == table]
        if t and not verified and mine and all(c in t.columns for c in mine) and not (set(mine) & t.leading()):
            out.append(_f("join-column-not-indexed", "low", f"JOIN {table} 的连接列 {', '.join(sorted(set(mine)))} 没有可用索引：" + short,
                          "连接列不是任何索引的首列：每匹配一行都要扫描被连接的表（或建临时哈希表），数据量上来后是主要的慢点。",
                          "给连接列建索引；表确定很小可以忽略。",
                          f"CREATE INDEX idx_{table}_{mine[0]} ON {table} ({mine[0]});"))
        if dialect != "sqlite":
            for (ta, ca), (tb, cb) in pairs:
                fa = family(schema.tables[ta].columns.get(ca, "")) if ta in schema.tables else None
                fb = family(schema.tables[tb].columns.get(cb, "")) if tb in schema.tables else None
                if fa and fb and fa != fb and {fa, fb} != {"int", "num"}:
                    out.append(_f("join-type-mismatch", "medium",
                                  f"JOIN 两侧的列类型不同：{ta}.{ca}（{schema.tables[ta].columns[ca]}）= {tb}.{cb}（{schema.tables[tb].columns[cb]}）",
                                  "类型不一致时数据库要做隐式转换，被转换的一侧索引失效；MySQL 里字符串和数字比较还可能匹配到意料之外的行。",
                                  "把两列改成同一类型；临时方案是在没有索引的一侧显式 CAST。"))
    if n_joins > max_joins:
        out.append(_f("too-many-joins", "low", f"一条语句里有 {n_joins} 个 JOIN（阈值 {max_joins}）：" + short,
                      "JOIN 越多，优化器可选的连接顺序呈阶乘增长，容易选到差的计划，语句也难以维护。",
                      "拆成几条查询，或把稳定的中间结果做成视图 / 物化表；阈值可以用 profile 的 database.max_joins 调整。"))
    tables = set(aliases.values())
    if not verified and not n_joins and not comma and len(tables) == 1 and where.strip():
        t = schema.tables.get(next(iter(tables)))
        if t:
            cols = [c for tb, c in (_resolve(q, aliases) for q in _PRED_COL.findall(where)) if c in t.columns]
            if cols and not (set(cols) & t.leading()):
                out.append(_f("filter-column-not-indexed", "low", f"WHERE 用到的列 {', '.join(sorted(set(cols)))} 都不是表 {t.name} 任何索引的首列：" + short,
                              "按表结构推断，这条语句没有可用的索引，会全表扫描（没有连数据库，未用真实执行计划验证）。",
                              "给选择性最高的过滤列建索引；表确定很小可以忽略。",
                              f"CREATE INDEX idx_{t.name}_{cols[0]} ON {t.name} ({cols[0]});"))
    return out
