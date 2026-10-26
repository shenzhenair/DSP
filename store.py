# -*- coding: utf-8 -*-
"""SQLite 存储层：数据源 / 任务 / 运行记录 / 系统设置"""
import json
import os
import sqlite3
import threading
import datetime

import config
from logutil import get_logger

_log = get_logger("store")
_lock = threading.RLock()


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn():
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")  # 写锁最长等待 15 秒，避免并发下 locked 异常
    return conn


def init_db():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with _lock, get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS datasources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                db_type TEXT NOT NULL DEFAULT 'oracle',   -- oracle / sqlite / mysql
                config TEXT NOT NULL DEFAULT '{}',
                remark TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                grp TEXT DEFAULT '默认分组',
                owner TEXT DEFAULT '',
                cron TEXT DEFAULT '0 9 * * 1-5',
                timeout_min INTEGER DEFAULT 30,
                retry_times INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                config TEXT NOT NULL DEFAULT '{}',
                last_run_at TEXT,
                last_status TEXT,
                last_duration REAL,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                task_name TEXT,
                trigger TEXT DEFAULT 'auto',   -- auto / manual
                status TEXT DEFAULT 'running', -- running / success / failed
                start_at TEXT,
                end_at TEXT,
                duration_sec REAL,
                log TEXT DEFAULT '',
                outputs TEXT DEFAULT '[]',
                error TEXT DEFAULT '',
                FOREIGN KEY(task_id) REFERENCES tasks(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                open_id TEXT,
                name TEXT,
                user_info TEXT NOT NULL DEFAULT '{}',
                created_at TEXT,
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS users (
                open_id TEXT PRIMARY KEY,
                name TEXT,
                user_id TEXT,
                email TEXT,
                mobile TEXT,
                avatar_url TEXT,
                first_seen_at TEXT,
                last_login_at TEXT,
                last_login_ip TEXT,
                login_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id, id);
            CREATE INDEX IF NOT EXISTS idx_runs_start ON runs(start_at);

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT,
                user_name TEXT,
                open_id TEXT,
                action TEXT,
                target TEXT,
                detail TEXT,
                ip TEXT,
                result TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_id ON audit_logs(id DESC);
            """
        )
        # 迁移：users 表补 role 列（老库平滑升级），并把默认管理员置为 admin
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
        if "role" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'pending'")
        conn.execute("UPDATE users SET role='admin' WHERE name IN ({})".format(
            ",".join("?" * len(config.ADMIN_NAMES)) or "''"), tuple(config.ADMIN_NAMES))
        # 迁移：tasks 表补创建人列（老库回填给默认管理员）
        tcols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
        if "created_by" not in tcols:
            conn.execute("ALTER TABLE tasks ADD COLUMN created_by TEXT")
        if "created_by_name" not in tcols:
            conn.execute("ALTER TABLE tasks ADD COLUMN created_by_name TEXT")
        admin = conn.execute(
            "SELECT open_id, name FROM users WHERE role='admin' ORDER BY last_login_at DESC LIMIT 1"
        ).fetchone()
        if admin:
            conn.execute("UPDATE tasks SET created_by=?, created_by_name=? WHERE created_by IS NULL",
                         (admin["open_id"], admin["name"]))
        dcols = [r[1] for r in conn.execute("PRAGMA table_info(datasources)")]
        if "created_by" not in dcols:
            conn.execute("ALTER TABLE datasources ADD COLUMN created_by TEXT")
        if "created_by_name" not in dcols:
            conn.execute("ALTER TABLE datasources ADD COLUMN created_by_name TEXT")
        if admin:
            conn.execute("UPDATE datasources SET created_by=?, created_by_name=? WHERE created_by IS NULL",
                         (admin["open_id"], admin["name"]))
        conn.commit()


# ---------------- 工具 ----------------
def _row_to_dict(row):
    return dict(row) if row is not None else None


def _parse_config(task_row):
    d = _row_to_dict(task_row)
    if d:
        try:
            d["config"] = json.loads(d.get("config") or "{}")
        except Exception:
            d["config"] = {}
    return d


# ---------------- 数据源 ----------------
def list_datasources():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM datasources ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["config"] = json.loads(d.get("config") or "{}")
        out.append(d)
    return out


def get_datasource(ds_id):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM datasources WHERE id=?", (ds_id,)).fetchone()
    d = _row_to_dict(r)
    if d:
        d["config"] = json.loads(d.get("config") or "{}")
    return d


def save_datasource(data):
    cfg = json.dumps(data.get("config", {}), ensure_ascii=False)
    now = _now()
    with _lock, get_conn() as conn:
        if data.get("id"):
            conn.execute(
                "UPDATE datasources SET name=?, db_type=?, config=?, remark=?, updated_at=? WHERE id=?",
                (data["name"], data.get("db_type", "oracle"), cfg,
                 data.get("remark", ""), now, data["id"]),
            )
            return data["id"]
        cur = conn.execute(
            "INSERT INTO datasources(name, db_type, config, remark, created_at, updated_at, created_by, created_by_name) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (data["name"], data.get("db_type", "oracle"), cfg,
             data.get("remark", ""), now, now, data.get("created_by"), data.get("created_by_name")),
        )
        return cur.lastrowid


def delete_datasource(ds_id):
    with _lock, get_conn() as conn:
        conn.execute("DELETE FROM datasources WHERE id=?", (ds_id,))


# ---------------- 任务 ----------------
def list_tasks(created_by=None):
    sql, args = "SELECT * FROM tasks", []
    if created_by:
        sql += " WHERE created_by=?"
        args.append(created_by)
    sql += " ORDER BY id"
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_parse_config(r) for r in rows]


def get_task(task_id):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _parse_config(r)


def save_task(data):
    cfg = json.dumps(data.get("config", {}), ensure_ascii=False)
    now = _now()
    with _lock, get_conn() as conn:
        if data.get("id"):
            conn.execute(
                "UPDATE tasks SET name=?, grp=?, owner=?, cron=?, timeout_min=?, retry_times=?, "
                "enabled=?, config=?, updated_at=? WHERE id=?",
                (data["name"], data.get("grp", ""), data.get("owner", ""),
                 data.get("cron", "0 9 * * 1-5"), int(data.get("timeout_min", 30)),
                 int(data.get("retry_times", 0)), 1 if data.get("enabled", True) else 0,
                 cfg, now, data["id"]),
            )
            return data["id"]
        cur = conn.execute(
            "INSERT INTO tasks(name, grp, owner, cron, timeout_min, retry_times, enabled, config, created_at, updated_at, created_by, created_by_name) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (data["name"], data.get("grp", ""), data.get("owner", ""),
             data.get("cron", "0 9 * * 1-5"), int(data.get("timeout_min", 30)),
             int(data.get("retry_times", 0)), 1 if data.get("enabled", True) else 0,
             cfg, now, now, data.get("created_by"), data.get("created_by_name")),
        )
        return cur.lastrowid


def set_task_enabled(task_id, enabled):
    with _lock, get_conn() as conn:
        conn.execute("UPDATE tasks SET enabled=?, updated_at=? WHERE id=?",
                     (1 if enabled else 0, _now(), task_id))


def delete_task(task_id):
    with _lock, get_conn() as conn:
        conn.execute("DELETE FROM runs WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))


def mark_task_result(task_id, status, duration_sec):
    with _lock, get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET last_run_at=?, last_status=?, last_duration=?, updated_at=? WHERE id=?",
            (_now(), status, round(duration_sec, 2), _now(), task_id),
        )


# ---------------- 运行记录 ----------------
def create_run(task_id, task_name, trigger):
    now = _now()
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO runs(task_id, task_name, trigger, status, start_at) VALUES(?,?,?,?,?)",
            (task_id, task_name, trigger, "running", now),
        )
        return cur.lastrowid


def append_run_log(run_id, line):
    try:
        with _lock, get_conn() as conn:
            conn.execute("UPDATE runs SET log = SUBSTR(COALESCE(log,'') || ?, -?) WHERE id=?",
                         (str(line) + "\n", config.LOG_MAX_CHARS, run_id))
    except Exception as e:
        _log.error(f"运行日志落库失败 run#{run_id}: {e}")


def finish_run(run_id, status, outputs=None, error=""):
    with _lock, get_conn() as conn:
        r = conn.execute("SELECT start_at FROM runs WHERE id=?", (run_id,)).fetchone()
        end = _now()
        dur = None
        if r and r["start_at"]:
            try:
                t0 = datetime.datetime.strptime(r["start_at"], "%Y-%m-%d %H:%M:%S")
                dur = round((datetime.datetime.now() - t0).total_seconds(), 2)
            except Exception:
                pass
        conn.execute(
            "UPDATE runs SET status=?, end_at=?, duration_sec=?, outputs=?, error=? WHERE id=?",
            (status, end, dur, json.dumps(outputs or [], ensure_ascii=False), error, run_id),
        )
    return dur


def get_run(run_id):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    d = _row_to_dict(r)
    if d:
        d["outputs"] = json.loads(d.get("outputs") or "[]")
    return d


def list_runs(task_id=None, limit=100, offset=0):
    sql = "SELECT * FROM runs"
    args = []
    if task_id:
        sql += " WHERE task_id=?"
        args.append(task_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_runs(task_id=None):
    sql = "SELECT COUNT(*) FROM runs"
    args = []
    if task_id:
        sql += " WHERE task_id=?"
        args.append(task_id)
    with get_conn() as conn:
        return conn.execute(sql, args).fetchone()[0]


def recent_runs(task_id, n=7):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status FROM runs WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, n)
        ).fetchall()
    return [r["status"] for r in reversed(rows)]


def runs_between(start_str, end_str):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE start_at>=? AND start_at<? ORDER BY id",
            (start_str, end_str),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def reset_stale_running():
    """服务重启时，把上次残留的 running 标记为 failed"""
    with _lock, get_conn() as conn:
        conn.execute(
            "UPDATE runs SET status='failed', error='服务重启，运行中断', end_at=? "
            "WHERE status='running'",
            (_now(),),
        )


def overdue_runs():
    """running 状态且超出「任务超时 + 5 分钟」的运行记录（心跳巡检疑似卡死）"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT r.id, r.task_name, r.start_at, t.timeout_min "
            "FROM runs r LEFT JOIN tasks t ON t.id = r.task_id "
            "WHERE r.status='running'"
        ).fetchall()
    out = []
    now = datetime.datetime.now()
    for r in rows:
        try:
            t0 = datetime.datetime.strptime(r["start_at"], "%Y-%m-%d %H:%M:%S")
            limit = (int(r["timeout_min"] or 30) + 5) * 60
            if (now - t0).total_seconds() > limit:
                out.append({"id": r["id"], "task_name": r["task_name"],
                            "start_at": r["start_at"], "timeout_min": int(r["timeout_min"] or 30)})
        except Exception:
            continue
    return out


# ---------------- 设置 ----------------
DEFAULT_SETTINGS = {
    "feishu_webhook": "",
    "feishu_enabled": "false",
    "oracle_thick": "false",
    "oracle_client_dir": "",
    "notify_on_failure": "true",
    "notify_on_success": "false",
}


def get_settings():
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    data = dict(DEFAULT_SETTINGS)
    data.update({r["key"]: r["value"] for r in rows})
    return data


def save_settings(pairs):
    now_keys = list(pairs.keys())
    with _lock, get_conn() as conn:
        for k, v in pairs.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v)),
            )
    # 应用侧副作用：oracle thick 开关由 engine 读取，无需即时处理
    return {k: pairs[k] for k in now_keys}


# ---------------- 心跳 ----------------
def save_heartbeat(alive=True, note=""):
    """心跳线程定期调用：记录最近心跳时间与状态"""
    try:
        with _lock, get_conn() as conn:
            now = _now()
            pairs = [("last_heartbeat", now), ("heartbeat_status", "alive" if alive else "degraded")]
            if note:
                pairs.append(("heartbeat_note", note))
            for k, v in pairs:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, v),
                )
    except Exception as e:
        _log.error(f"心跳写入失败: {e}")


def get_heartbeat():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('last_heartbeat','heartbeat_status','heartbeat_note','server_started_at')"
        ).fetchall()
    return {r["key"]: r["value"] for r in rows}


# ---------------- 登录会话 ----------------
def create_session(token, user, ttl_hours):
    """新建登录会话。user 为飞书用户信息 dict；token 由调用方生成。"""
    now = datetime.datetime.now()
    expires = (now + datetime.timedelta(hours=ttl_hours)).strftime("%Y-%m-%d %H:%M:%S")
    with _lock, get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions(token, open_id, name, user_info, created_at, expires_at) "
            "VALUES(?,?,?,?,?,?)",
            (token, user.get("open_id"), user.get("name"), json.dumps(user, ensure_ascii=False),
             now.strftime("%Y-%m-%d %H:%M:%S"), expires),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))  # 顺手清理过期会话


def get_session(token):
    """按 token 取有效会话；无效/过期返回 None。"""
    if not token:
        return None
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    d = _row_to_dict(r)
    if not d or d["expires_at"] < _now():
        return None
    try:
        d["user_info"] = json.loads(d.get("user_info") or "{}")
    except Exception:
        d["user_info"] = {}
    return d


def delete_session(token):
    if not token:
        return
    with _lock, get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def upsert_user(user, ip=None):
    """登录用户落库：不存在则插入，已存在则更新资料并累加登录次数。以 open_id 为唯一键。
    默认管理员（config.ADMIN_NAMES）插入/登录时强制 role='admin'，其余用户角色保持不变。"""
    if not user or not user.get("open_id"):
        return
    now = _now()
    is_admin = user.get("name") in config.ADMIN_NAMES
    role = "admin" if is_admin else "pending"
    with _lock, get_conn() as conn:
        conn.execute(
            "INSERT INTO users(open_id, name, user_id, email, mobile, avatar_url,"
            " first_seen_at, last_login_at, last_login_ip, login_count, role)"
            " VALUES(?,?,?,?,?,?,?,?,?,1,?)"
            " ON CONFLICT(open_id) DO UPDATE SET"
            "  name=excluded.name, user_id=excluded.user_id, email=excluded.email,"
            "  mobile=excluded.mobile, avatar_url=excluded.avatar_url,"
            "  last_login_at=excluded.last_login_at, last_login_ip=excluded.last_login_ip,"
            "  login_count=users.login_count+1"
            + (", role='admin'" if is_admin else ""),
            (user.get("open_id"), user.get("name"), user.get("user_id"),
             user.get("email"), user.get("mobile"), user.get("avatar_url"),
             now, now, ip, role),
        )


def set_user_role(open_id, role):
    if role not in ("admin", "senior", "member", "pending"):
        raise ValueError(f"非法角色: {role}")
    with _lock, get_conn() as conn:
        conn.execute("UPDATE users SET role=? WHERE open_id=?", (role, open_id))


def get_role(open_id):
    """取用户当前角色；未登记用户返回 None（视同无权限）。"""
    if not open_id:
        return None
    with get_conn() as conn:
        r = conn.execute("SELECT role FROM users WHERE open_id=?", (open_id,)).fetchone()
    return r["role"] if r else None


def list_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT open_id, name, user_id, email, mobile, avatar_url,"
            " first_seen_at, last_login_at, last_login_ip, login_count, role"
            " FROM users ORDER BY (role='admin') DESC, last_login_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_audit(user, action, target="", detail="", ip=None, result="成功"):
    """记录一条人为操作审计。user 为 {'name':..,'open_id':..} 或 None（未登录）。"""
    try:
        with _lock, get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_logs(at, user_name, open_id, action, target, detail, ip, result)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (_now(), (user or {}).get("name") or "-",
                 (user or {}).get("open_id"), action, target,
                 str(detail or "")[:500], ip, result),
            )
    except Exception:
        pass  # 审计失败绝不影响业务请求


def list_audit(limit=50, offset=0, user=None):
    """分页查询审计记录，user 可选按操作人过滤。返回 {total, rows}。"""
    cond, args = "", []
    if user:
        cond = "WHERE user_name=?"
        args.append(user)
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM audit_logs {cond}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, at, user_name, open_id, action, target, detail, ip, result"
            f" FROM audit_logs {cond} ORDER BY id DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
    return {"total": total, "rows": [_row_to_dict(r) for r in rows]}
