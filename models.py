import os
import re
import sqlite3
from datetime import datetime, date
from decimal import Decimal

# 若设置了 DATABASE_URL（如 Neon 的 postgres:// 连接串）则使用 Postgres，
# 否则回退到本地 SQLite 文件（便于本机开发调试）。
DATABASE_URL = (os.environ.get('DATABASE_URL') or '').strip()
USE_POSTGRES = DATABASE_URL.startswith('postgres')

DB_PATH = os.environ.get('DB_PATH') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'sparkjoy.db'
)


SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        class_name TEXT NOT NULL,
        student_id TEXT NOT NULL UNIQUE,
        phone TEXT NOT NULL,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        price REAL NOT NULL,
        image_url TEXT,
        styles TEXT,
        stock INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 1,
        style TEXT,
        name TEXT NOT NULL,
        class_name TEXT NOT NULL,
        student_id TEXT NOT NULL,
        phone TEXT NOT NULL,
        amount REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending_payment',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );

    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        sender_type TEXT NOT NULL,
        sender_id INTEGER,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL UNIQUE,
        user_id INTEGER NOT NULL,
        rating INTEGER NOT NULL,
        content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES orders(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount REAL NOT NULL,
        description TEXT,
        cost_date TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
"""


def _convert(value):
    """把 Postgres 返回的特殊类型转成可直接 JSON 序列化的值，保持与 SQLite 输出一致。"""
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, Decimal):
        return float(value)
    return value


class Row:
    """兼容 sqlite3.Row：支持 row['key']、row[0]、list(row)（返回各列值）、dict(row)。"""

    __slots__ = ('_keys', '_values', '_index')

    def __init__(self, keys, values):
        self._keys = list(keys)
        self._values = list(values)
        self._index = {k: i for i, k in enumerate(self._keys)}

    def keys(self):
        return list(self._keys)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __contains__(self, key):
        return key in self._index

    def get(self, key, default=None):
        return self._values[self._index[key]] if key in self._index else default


def _row_from_mapping(mapping):
    if mapping is None:
        return None
    keys = list(mapping.keys())
    return Row(keys, [_convert(mapping[k]) for k in keys])


class _PgResult:
    """模拟 sqlite3 游标的结果集接口。"""

    def __init__(self, rows):
        self._rows = rows
        self._pos = 0

    def fetchone(self):
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class _PgConnection:
    """把 Postgres 连接包装成与 sqlite3 连接相同的用法：conn.execute / commit / close / cursor()."""

    def __init__(self, dsn):
        import psycopg2
        import psycopg2.extras
        self._psycopg2 = psycopg2
        self._RealDictCursor = psycopg2.extras.RealDictCursor
        self._conn = psycopg2.connect(dsn)
        self._lastrowid = None

    @staticmethod
    def _translate_schema(script):
        script = script.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')
        script = script.replace('REAL', 'DOUBLE PRECISION')
        # 时间统一按 TEXT 存储，格式与 SQLite 完全一致，便于按月统计与前端展示
        script = script.replace(
            'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
            "TEXT DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')"
        )
        return script

    @staticmethod
    def _translate(sql):
        # strftime('%Y-%m', x)  -> substring(x from 1 for 7)
        sql = re.sub(r"strftime\('%Y-%m',\s*([^(),]+?)\)",
                     r"substring(\1 from 1 for 7)", sql)
        return sql.replace('?', '%s')

    def execute(self, sql, params=None):
        s = sql.strip()
        low = s.lower()
        if low.startswith('select last_insert_rowid()'):
            return _PgResult([Row(['last_insert_rowid()'], [self._lastrowid])])
        translated = self._translate(s)
        is_insert = low.startswith('insert')
        if is_insert and ' returning ' not in low:
            translated += ' RETURNING id'
        cur = self._conn.cursor(cursor_factory=self._RealDictCursor)
        cur.execute(translated, params)
        try:
            data = cur.fetchall()
        except self._psycopg2.ProgrammingError:
            data = []
        cur.close()
        rows = [_row_from_mapping(m) for m in data]
        if is_insert and rows:
            self._lastrowid = rows[0]['id']
        return _PgResult(rows)

    def cursor(self):
        return self

    def executescript(self, script):
        cur = self._conn.cursor()
        cur.execute(self._translate_schema(script))
        cur.close()

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_db():
    if USE_POSTGRES:
        return _PgConnection(DATABASE_URL)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript(SCHEMA)
    conn.commit()
    conn.close()


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows):
    return [dict(r) for r in rows]
