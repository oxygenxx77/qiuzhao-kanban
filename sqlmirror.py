"""
SQLite 镜像：把看板的 JSON 数据同步一份到 kanban.db，用来写 SQL 查询、练习 SQL。

JSON 文件仍然是看板的正式数据；kanban.db 每次看板数据变化后整库重建，
所以直接改 kanban.db 不会影响看板，下次同步也会被覆盖。
"""
import os
import sqlite3
import threading
import time
from datetime import datetime

import storage as st
from board import _split_positions
from jobhunter import JOB_HUNTER_DB

DB_FILE = os.path.join(st.BASE_DIR, "kanban.db")
SOURCES = (st.DATA_FILE, st.INTERVIEW_FILE, st.HISTORY_FILE)
MAX_ROWS = 500
TIME_LIMIT = 3  # 单条查询最多跑几秒

SCHEMA = """
CREATE TABLE statuses (
    code       TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    sort_order INTEGER NOT NULL
);
CREATE TABLE companies (
    id         INTEGER PRIMARY KEY,
    company    TEXT NOT NULL,
    positions  TEXT,
    status     TEXT REFERENCES statuses(code),
    deadline   TEXT,
    applied_at TEXT,
    note       TEXT,
    updated_at TEXT
);
CREATE TABLE positions (
    company_id INTEGER REFERENCES companies(id),
    position   TEXT NOT NULL
);
CREATE TABLE interviews (
    id         INTEGER PRIMARY KEY,
    company    TEXT NOT NULL,
    position   TEXT,
    round      TEXT,
    date       TEXT,
    time       TEXT,
    result     TEXT,
    questions  TEXT,
    reflection TEXT,
    created_at TEXT
);
CREATE TABLE history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  TEXT,
    company    TEXT,
    old_status TEXT,
    new_status TEXT,
    note       TEXT
);
CREATE INDEX idx_positions_company ON positions(company_id);
CREATE INDEX idx_interviews_company ON interviews(company);
CREATE INDEX idx_history_company ON history(company);
"""

# 表和字段的中文说明，显示在 SQL 练习窗口里
DOCS = {
    "statuses": ("状态字典（code → 中文名），可以和 companies 做 JOIN", {
        "code": "状态代码", "label": "中文名", "sort_order": "流程先后顺序"}),
    "companies": ("看板里的公司卡片，一家公司一行", {
        "id": "编号", "company": "公司名", "positions": "岗位（顿号分隔的原文）", "status": "状态代码，见 statuses",
        "deadline": "测评/笔试截止时间 YYYY-MM-DDTHH:MM", "applied_at": "投递日期 YYYY-MM-DD",
        "note": "备注", "updated_at": "最后修改时间"}),
    "positions": ("每家公司投的岗位拆成一行一个，适合练 GROUP BY / JOIN", {
        "company_id": "对应 companies.id", "position": "岗位名"}),
    "interviews": ("面试记录（含模拟面试，轮次带「模拟」字样）", {
        "id": "编号", "company": "公司名", "position": "岗位", "round": "轮次", "date": "日期 YYYY-MM-DD",
        "time": "时间 HH:MM", "result": "待定 / 通过 / 已挂", "questions": "面试题目", "reflection": "复盘",
        "created_at": "记录创建时间"}),
    "history": ("每次状态变化的流水，时间线和周报都来自这里", {
        "timestamp": "发生时间", "company": "公司名", "old_status": "原状态（空 = 新增）",
        "new_status": "新状态", "note": "说明"}),
    "jh.jobs": ("求职助手采集的岗位（只读，来自 jobs.db）", {
        "company": "公司名", "title": "岗位名", "city": "城市", "status": "pending 待确认 / approved 待投递 / applied 已投递 / rejected 已拒绝",
        "match_score": "AI 匹配分", "match_advice": "AI 建议", "jd_text": "JD 文本", "applied_at": "投递时间"}),
    "jh.preferences": ("求职助手里的求职偏好", {}),
    "jh.risk_logs": ("求职助手的投递风险日志", {}),
}

EXAMPLES = [
    ("① 查看全部", "SELECT company, positions, status, applied_at\nFROM companies\nORDER BY applied_at DESC;"),
    ("② WHERE 筛选", "SELECT company, note\nFROM companies\nWHERE status = 'waiting'\n  AND note LIKE '%测评%';"),
    ("③ GROUP BY 统计", "SELECT status, COUNT(*) AS 数量\nFROM companies\nGROUP BY status\nORDER BY 数量 DESC;"),
    ("④ LEFT JOIN 字典", "SELECT s.label AS 状态, COUNT(c.id) AS 数量\nFROM statuses s\nLEFT JOIN companies c ON c.status = s.code\nGROUP BY s.code\nORDER BY s.sort_order;"),
    ("⑤ 按周统计投递", "SELECT strftime('%Y 第%W周', applied_at) AS 周, COUNT(*) AS 投递数\nFROM companies\nWHERE applied_at <> ''\nGROUP BY 周\nORDER BY 周;"),
    ("⑥ 岗位最多的公司", "SELECT c.company, COUNT(p.position) AS 岗位数\nFROM companies c\nJOIN positions p ON p.company_id = c.id\nGROUP BY c.id\nORDER BY 岗位数 DESC\nLIMIT 10;"),
    ("⑦ 子查询", "-- 已投递、但还没有任何面试记录的公司\nSELECT company, applied_at\nFROM companies\nWHERE status = 'submitted'\n  AND company NOT IN (SELECT company FROM interviews);"),
    ("⑧ 日期计算", "-- 等结果的公司，距离投递过了多少天\nSELECT company,\n       applied_at,\n       CAST(julianday('now', 'localtime') - julianday(applied_at) AS INTEGER) AS 已过天数\nFROM companies\nWHERE status = 'waiting' AND applied_at <> ''\nORDER BY 已过天数 DESC;"),
    ("⑨ CASE 分组", "SELECT CASE\n         WHEN status IN ('interview', 'offer') THEN '进入面试'\n         WHEN status IN ('pending', 'waiting') THEN '测评阶段'\n         WHEN status = 'ended' THEN '已结束'\n         ELSE '还没消息'\n       END AS 阶段,\n       COUNT(*) AS 数量\nFROM companies\nGROUP BY 阶段;"),
    ("⑩ 跨库 JOIN", "-- 看板公司在求职助手里还有哪些岗位（jh 是求职助手的 jobs.db）\nSELECT c.company AS 看板公司, j.title AS 岗位, j.match_score AS 匹配分, j.status\nFROM companies c\nJOIN jh.jobs j ON j.company LIKE '%' || c.company || '%'\nORDER BY j.match_score DESC;"),
]

_lock = threading.Lock()


class QueryError(Exception):
    """SQL 有误或不被允许，消息原样展示给用户"""


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sync():
    """整库重建：先写临时文件再替换，别的程序（如 DB Browser）正开着 kanban.db 也不会读到一半的数据"""
    with _lock:
        tmp = DB_FILE + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        conn = sqlite3.connect(tmp)
        try:
            conn.executescript(SCHEMA)
            conn.executemany("INSERT INTO statuses VALUES (?, ?, ?)",
                             [(k, v, i) for i, (k, v) in enumerate(st.STATUS.items())])
            used = set()
            for c in st.load_cards():
                cid = _int(c.get("id"))
                cid = cid if cid is not None and cid not in used else None   # 旧数据里可能有重复编号
                cur = conn.execute(
                    "INSERT INTO companies (id, company, positions, status, deadline, applied_at, note, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, c.get("company", ""), c.get("positions", ""), c.get("status", ""), c.get("deadline", ""),
                     c.get("applied_at", ""), c.get("note", ""), c.get("updated_at", "")))
                used.add(cur.lastrowid)
                conn.executemany("INSERT INTO positions VALUES (?, ?)",
                                 [(cur.lastrowid, p) for p in _split_positions(c.get("positions", ""))])
            used = set()
            for iv in st.load_interviews():
                iid = _int(iv.get("id"))
                iid = iid if iid is not None and iid not in used else None
                cur = conn.execute(
                    "INSERT INTO interviews (id, company, position, round, date, time, result, questions, reflection, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (iid, iv.get("company", ""), iv.get("position", ""), iv.get("round", ""), iv.get("date", ""),
                     iv.get("time", ""), iv.get("result", ""), iv.get("questions", ""), iv.get("reflection", ""),
                     iv.get("created_at", "")))
                used.add(cur.lastrowid)
            conn.executemany(
                "INSERT INTO history (timestamp, company, old_status, new_status, note) VALUES (?, ?, ?, ?, ?)",
                [(h.get("timestamp", ""), h.get("company", ""), h.get("old_status", ""), h.get("new_status", ""),
                  h.get("note", "")) for h in st.load_history()])
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, DB_FILE)


def _sync_quietly():
    try:
        sync()
    except Exception as e:
        print(f"同步 kanban.db 失败: {e}")


def ensure_fresh():
    """求职助手会直接改看板的 JSON，所以查询前按修改时间判断一下要不要重新同步"""
    try:
        db_time = os.path.getmtime(DB_FILE)
    except OSError:
        db_time = 0
    if any(os.path.exists(f) and os.path.getmtime(f) > db_time for f in SOURCES):
        _sync_quietly()


def synced_at():
    try:
        return datetime.fromtimestamp(os.path.getmtime(DB_FILE)).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return ""


# 只放行读操作：查询里写 INSERT / UPDATE / DROP / ATTACH 等都会被拒绝
_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


def _connect(guard=True):
    ensure_fresh()
    conn = sqlite3.connect(DB_FILE, timeout=3)
    if os.path.exists(JOB_HUNTER_DB):
        conn.execute("ATTACH DATABASE ? AS jh", (JOB_HUNTER_DB,))
    conn.execute("PRAGMA query_only = ON")
    if guard:
        conn.set_authorizer(lambda action, *args: sqlite3.SQLITE_OK if action in _READ_ACTIONS else sqlite3.SQLITE_DENY)
    return conn


def _cell(value):
    return value.hex() if isinstance(value, bytes) else value


def query(sql):
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise QueryError("先写一条 SQL 吧")
    conn = _connect()
    deadline = time.time() + TIME_LIMIT
    conn.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
    started = time.perf_counter()
    try:
        cur = conn.execute(sql)
        if cur.description is None:
            raise QueryError("这条语句没有返回结果。这里只能用 SELECT 查询")
        rows = cur.fetchmany(MAX_ROWS + 1)
    except (sqlite3.Warning, sqlite3.DatabaseError) as e:
        msg = str(e)
        if "one statement" in msg:
            raise QueryError("一次只能执行一条语句，多条请分开运行")
        if "not authorized" in msg or "readonly" in msg:
            raise QueryError("只读模式：这里只能用 SELECT 查询。想改数据请在看板里操作或者跟小招说")
        if "interrupted" in msg:
            raise QueryError(f"查询超过 {TIME_LIMIT} 秒被中止了，检查一下是不是 JOIN 少了条件")
        raise QueryError(f"SQL 有误：{msg}")
    finally:
        conn.close()
    return {
        "columns": [d[0] for d in cur.description],
        "rows": [[_cell(v) for v in r] for r in rows[:MAX_ROWS]],
        "truncated": len(rows) > MAX_ROWS,
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


def schema():
    conn = _connect(guard=False)
    try:
        tables = []
        for db in ("main", "jh"):
            try:
                names = [r[0] for r in conn.execute(
                    f"SELECT name FROM {db}.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
            except sqlite3.DatabaseError:
                continue
            for name in names:
                full = name if db == "main" else f"jh.{name}"
                desc, col_docs = DOCS.get(full, ("", {}))
                cols = [{"name": r[1], "type": r[2], "desc": col_docs.get(r[1], "")}
                        for r in conn.execute(f'PRAGMA {db}.table_info("{name}")')]
                count = conn.execute(f'SELECT COUNT(*) FROM {db}."{name}"').fetchone()[0]
                tables.append({"name": full, "desc": desc, "rows": count, "columns": cols})
    finally:
        conn.close()
    return {"tables": tables, "examples": [{"title": t, "sql": s} for t, s in EXAMPLES],
            "synced_at": synced_at(), "db_path": DB_FILE, "job_hunter_db": JOB_HUNTER_DB if os.path.exists(JOB_HUNTER_DB) else ""}


st.on_change.append(_sync_quietly)
