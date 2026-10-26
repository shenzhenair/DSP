# -*- coding: utf-8 -*-
"""
数据查询调度管理平台 —— Web 服务入口
REST API + 静态页面（执行总览 / 任务控制台 / 数据源 / 运行监控 / 设置）
服务端日志：logs/server.log（按天轮转）；心跳：/api/heartbeat + 心跳守护线程
"""
import datetime
import os
import secrets
import time
import traceback

import requests
from flask import Flask, jsonify, make_response, request, send_from_directory

import config
import engine
import notifier
import scheduler
import store
from logutil import get_logger, setup_logging

setup_logging()
_log = get_logger("web")
_STARTED_AT = time.time()

app = Flask(__name__, static_folder=config.STATIC_DIR, static_url_path="")


# ---------------- 请求日志与全局错误兜底（避免服务中断） ----------------
@app.before_request
def _log_request():
    if request.path.startswith("/api/heartbeat") or not request.path.startswith("/api/"):
        return  # 静态页面与心跳探测不记请求日志，避免刷屏
    _log.info(f"{request.method} {request.path} from {request.remote_addr}")


@app.after_request
def _log_response(resp):
    if request.path.startswith("/api/") and not request.path.startswith("/api/heartbeat"):
        if resp.status_code >= 400:
            _log.warning(f"{request.method} {request.path} -> {resp.status_code}")
    else:
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.errorhandler(Exception)
def _on_error(e):
    _log.error(f"未处理异常 {request.method} {request.path}: {e}\n{traceback.format_exc(limit=6)}")
    return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ---------------- 消除浏览器对 /favicon.ico 的默认请求噪音 ----------------
@app.route("/favicon.ico")
def favicon():
    resp = make_response("", 204)
    resp.headers["Content-Type"] = "image/x-icon"
    return resp


# ---------------- 心跳（前端 + 外部探活共同入口） ----------------
@app.route("/api/heartbeat")
def api_heartbeat():
    hb = {}
    try:
        hb = store.get_heartbeat()
    except Exception as e:
        _log.error(f"心跳读取失败: {e}")
    return jsonify({
        "ok": True,
        "status": "alive",
        "interval": config.FRONTEND_HEARTBEAT_INTERVAL,
        "server_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_sec": round(time.time() - _STARTED_AT),
        "running": scheduler.running_count(),
        "workers": config.WORKER_THREADS,
        "last_heartbeat": hb.get("last_heartbeat"),
        "heartbeat_status": hb.get("heartbeat_status"),
        "server_started_at": hb.get("server_started_at"),
    })


# ---------------- 登录认证（飞书扫码 OAuth2） ----------------
_AUTH_FREE_PATHS = ("/api/heartbeat", "/api/auth/")


def _auth_enabled():
    return bool(config.FEISHU_APP_ID and config.FEISHU_APP_SECRET)


def _redirect_uri():
    """飞书回跳地址：优先取 config.FEISHU_REDIRECT_URI 显式配置；
    未配置时按「浏览器实际访问的地址」动态生成，适配局域网 IP / 主机名 / 127.0.0.1 多种访问方式。"""
    if config.FEISHU_REDIRECT_URI:
        return config.FEISHU_REDIRECT_URI
    return request.host_url.rstrip("/") + "/api/auth/feishu/callback"


@app.before_request
def _auth_guard():
    """API 守卫：启用登录后按角色放行——
    admin 全部可用；member 可用业务模块（用户管理除外）；pending/未登记 一律 403。"""
    if not _auth_enabled() or not request.path.startswith("/api/"):
        return
    if request.path.startswith(_AUTH_FREE_PATHS):
        return
    sess = store.get_session(request.cookies.get(config.SESSION_COOKIE))
    if not sess:
        return jsonify({"ok": False, "error": "未登录或会话已过期", "auth_required": True}), 401
    open_id = (sess.get("user_info") or {}).get("open_id") or sess.get("open_id")
    role = store.get_role(open_id) or "pending"
    if role in ("admin", "senior"):
        return
    if role == "member" and not request.path.startswith(("/api/users", "/api/audit")):
        return
    if request.path.startswith(("/api/users", "/api/audit")):
        msg = "无用户管理权限" if role == "member" else "当前账号未开通访问权限，请联系管理员"
        return jsonify({"ok": False, "error": msg, "auth_required": True, "role": role}), 403
    msg = "当前账号未开通访问权限，请联系管理员"
    return jsonify({"ok": False, "error": msg, "auth_required": True, "role": role}), 403


# ---------------- 人为操作审计 ----------------
def _current_user():
    sess = store.get_session(request.cookies.get(config.SESSION_COOKIE))
    if not sess:
        return None
    info = sess.get("user_info") or {}
    return {"name": info.get("name") or sess.get("name"), "open_id": info.get("open_id") or sess.get("open_id")}


def _my_role():
    u = _current_user() or {}
    return (store.get_role(u.get("open_id")) or "pending") if u else "pending"


def _deny(ok, msg):
    """权限不满足时返回 403 响应，否则返回 None。"""
    if ok:
        return None
    return jsonify({"ok": False, "error": msg}), 403


def _task_perm(task):
    """任务操作权限：run/toggle=创建人或高级用户/管理员；edit/del=创建人或管理员。"""
    uid = (_current_user() or {}).get("open_id")
    role = _my_role()
    owner = task.get("created_by")
    own = bool(owner) and owner == uid
    legacy = not owner  # 未登记归属的遗留数据：仅管理员/高级用户可操作
    mgr, admin = role in ("admin", "senior"), role == "admin"
    return {
        "run": own or mgr or (legacy and mgr),
        "toggle": own or mgr or (legacy and mgr),
        "edit": own or admin or (legacy and admin),
        "del": own or admin or (legacy and admin),
    }


def _ds_perm(ds):
    """数据源操作权限：edit=创建人或高级用户/管理员；del=创建人或管理员。"""
    uid = (_current_user() or {}).get("open_id")
    role = _my_role()
    owner = ds.get("created_by")
    own = bool(owner) and owner == uid
    return {"edit": own or role in ("admin", "senior"), "del": own or role == "admin"}


def _audit_action(method, path, body):
    """把 (method, path, body) 映射为 (动作, 对象, 详情)。未知组合返回通用标签。"""
    import re
    mm = re.fullmatch(r"/api/tasks/(\d+)(?:/(enabled|run))?", path)
    if path == "/api/tasks" and method == "POST":
        return ("新建任务", body.get("name") or "-")
    if mm:
        tid, sub = mm.group(1), mm.group(2)
        t = f"任务#{tid}"
        if sub == "run":
            return ("手动运行任务", t)
        if sub == "enabled":
            return ("修改任务", t, "启用" if body.get("enabled") else "停用")
        return ("修改任务" if method == "PUT" else "删除任务", t)
    mm = re.fullmatch(r"/api/datasources(?:/(\d+)(?:/(test))?)?", path)
    if path == "/api/datasources" and method == "POST":
        return ("新建数据源", body.get("name") or "-")
    if mm:
        did, sub = mm.group(1), mm.group(2)
        t = f"数据源#{did}"
        if sub == "test":
            return ("测试数据源连接", t)
        return ("修改数据源" if method == "PUT" else "删除数据源", t)
    if path == "/api/settings" and method == "PUT":
        return ("保存系统设置", "飞书通知" if any("feishu" in k for k in body) else "")
    if path == "/api/feishu/test" and method == "POST":
        return ("测试飞书通知", body.get("key") or "")
    if path == "/api/users/role" and method == "POST":
        return ("修改用户角色", body.get("open_id") or "-", body.get("role") or "")
    return (f"{method} {path}", "")


@app.after_request
def _audit_hook(resp):
    """自动记录所有写操作（非 GET 的 /api 请求，心跳与登录回调除外）。"""
    try:
        p = request.path
        if (request.method == "GET" or not p.startswith("/api/")
                or p == "/api/heartbeat" or p.startswith("/api/auth/")):
            return resp
        body = request.get_json(force=True, silent=True) or {}
        act = _audit_action(request.method, p, body)
        action, target = act[0], act[1] if len(act) > 1 else ""
        detail = act[2] if len(act) > 2 else ""
        user = _current_user()
        result = "成功" if resp.status_code < 400 else f"失败(HTTP {resp.status_code})"
        store.add_audit(user, action, target, detail, request.remote_addr, result)
    except Exception:
        pass
    return resp


# ---------------- 用户与权限管理（仅管理员） ----------------
@app.route("/api/users")
def api_users():
    return jsonify({"ok": True, "users": store.list_users()})


@app.route("/api/users/role", methods=["POST"])
def api_users_role():
    body = request.get_json(force=True, silent=True) or {}
    open_id, role = body.get("open_id"), body.get("role")
    if not open_id or role not in ("admin", "senior", "member", "pending"):
        return jsonify({"ok": False, "error": "参数不合法"}), 400
    if body.get("open_id") == (store.get_session(request.cookies.get(config.SESSION_COOKIE)) or {}).get("open_id"):
        return jsonify({"ok": False, "error": "不能修改自己的角色"}), 400
    store.set_user_role(open_id, role)
    _log.info(f"用户角色变更: open_id={open_id} -> {role}")
    return jsonify({"ok": True})


@app.route("/api/audit")
def api_audit():
    page = max(1, int(request.args.get("page", 1) or 1))
    size = min(200, max(10, int(request.args.get("size", 50) or 50)))
    user = request.args.get("user") or None
    d = store.list_audit(limit=size, offset=(page - 1) * size, user=user)
    return jsonify({"ok": True, "total": d["total"], "page": page, "size": size, "rows": d["rows"]})


_app_token_cache = {"token": None, "ts": 0.0}


def _app_access_token(force=False):
    """获取（并缓存）应用自身的 app_access_token —— 调用 authen/v1 系列接口必须在请求头 Bearer 携带。"""
    if not force and _app_token_cache["token"] and time.time() - _app_token_cache["ts"] < 5400:
        return _app_token_cache["token"]
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal",
        json={"app_id": config.FEISHU_APP_ID, "app_secret": config.FEISHU_APP_SECRET},
        timeout=15,
    )
    try:
        d = r.json()
    except ValueError:
        raise RuntimeError(f"app_access_token 接口返回非 JSON (HTTP {r.status_code}): {r.text[:120]}")
    if d.get("code") != 0 or not d.get("app_access_token"):
        raise RuntimeError(f"获取 app_access_token 失败: {d.get('msg') or d} (code={d.get('code')})")
    _app_token_cache["token"] = d["app_access_token"]
    _app_token_cache["ts"] = time.time()
    return d["app_access_token"]


def _feishu_user_info(code):
    """用授权码换取用户信息：app_access_token(头) + code -> user_access_token -> user_info"""
    def _exchange(at):
        return requests.post(
            "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token",
            headers={"Authorization": f"Bearer {at}"},
            json={
                "grant_type": "authorization_code",
                "client_id": config.FEISHU_APP_ID,
                "client_secret": config.FEISHU_APP_SECRET,
                "code": code,
            },
            timeout=15,
        )

    resp = _exchange(_app_access_token())
    try:
        tok = resp.json()
    except ValueError:
        raise RuntimeError(f"token 接口返回非 JSON (HTTP {resp.status_code}): {resp.text[:120]}")
    if tok.get("code") == 20014:  # 应用凭证失效：强制刷新后重试一次
        resp = _exchange(_app_access_token(force=True))
        tok = resp.json()
    if tok.get("code") != 0:
        raise RuntimeError(
            f"换取 user_access_token 失败: {tok.get('msg') or tok.get('message') or tok} (code={tok.get('code')})"
        )
    token = (tok.get("data") or {}).get("access_token")
    if not token:
        raise RuntimeError(f"token 接口未返回 access_token: {tok}")
    info = requests.get(
        "https://open.feishu.cn/open-apis/authen/v1/user_info",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    try:
        ui = info.json()
    except ValueError:
        raise RuntimeError(f"user_info 接口返回非 JSON (HTTP {info.status_code}): {info.text[:120]}")
    if ui.get("code") != 0:
        raise RuntimeError(
            f"获取用户信息失败: {ui.get('msg') or ui.get('message') or ui} (code={ui.get('code')})"
        )
    return ui.get("data") or {}


def _callback_page(ok, msg=""):
    """回调页（弹窗/iframe 内运行）：通知父窗口登录结果后自行关闭。"""
    import json as _json
    payload = _json.dumps({"type": "dsp-auth", "ok": ok, "msg": msg}, ensure_ascii=False)
    tip = "登录成功，正在返回平台…" if ok else f"登录失败：{msg}"
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>登录回跳</title></head>
<body style="font-family:system-ui;text-align:center;padding-top:40vh;color:#666">
<p>{tip}</p>
<script>
  var payload = {payload};
  try {{ (window.opener || window.parent).postMessage(payload, location.origin); }} catch (e) {{}}
  setTimeout(function() {{ window.close(); }}, 600);
</script></body></html>"""


@app.route("/api/auth/me")
def api_auth_me():
    logged = _auth_enabled() and store.get_session(request.cookies.get(config.SESSION_COOKIE))
    if logged:
        open_id = (logged.get("user_info") or {}).get("open_id") or logged.get("open_id")
        role = store.get_role(open_id) or "pending"
    else:
        role = None
    return jsonify({
        "auth_enabled": _auth_enabled(),
        "logged_in": bool(logged),
        "role": role,
        "user": logged["user_info"] if logged else None,
        "login": {
            "app_id": config.FEISHU_APP_ID,
            "redirect_uri": _redirect_uri(),
        } if _auth_enabled() else None,
    })


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    user = _current_user()
    token = request.cookies.get(config.SESSION_COOKIE)
    if token:
        store.delete_session(token)
    store.add_audit(user, "退出登录", ip=request.remote_addr)
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(config.SESSION_COOKIE, path="/")
    return resp


@app.route("/api/auth/feishu/callback")
def api_auth_feishu_callback():
    code = request.args.get("code", "")
    err = request.args.get("error") or request.args.get("error_description")
    if err:
        _log.warning(f"飞书登录被拒绝: {err}")
        return _callback_page(False, "飞书授权被取消或拒绝")
    if not code:
        return _callback_page(False, "缺少授权码 code")
    try:
        user = _feishu_user_info(code)
    except Exception as e:
        _log.error(f"飞书登录失败: {e}")
        return _callback_page(False, f"{e}")
    token = secrets.token_urlsafe(32)
    store.create_session(token, user, config.SESSION_TTL_HOURS)
    store.upsert_user(user, request.remote_addr)
    store.add_audit(user, "扫码登录", ip=request.remote_addr)
    _log.info(f"用户登录成功: {user.get('name')} ({user.get('open_id')})")
    resp = make_response(_callback_page(True))
    resp.set_cookie(
        config.SESSION_COOKIE, token, max_age=config.SESSION_TTL_HOURS * 3600,
        httponly=True, samesite="Lax", path="/",
    )
    return resp


# ---------------- 页面 ----------------
@app.route("/")
def index():
    return send_from_directory(config.STATIC_DIR, "index.html")


# ---------------- 总览 ----------------
@app.route("/api/overview")
def api_overview():
    mine = request.args.get("mine", type=int)
    cur = _current_user() if mine else None
    tasks_src = store.list_tasks(created_by=cur["open_id"]) if cur else store.list_tasks()

    today = datetime.date.today()
    start = today.strftime("%Y-%m-%d 00:00:00")
    end = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
    day_runs = store.runs_between(start, end)
    total = len(day_runs)
    ok = sum(1 for r in day_runs if r["status"] == "success")
    fail = sum(1 for r in day_runs if r["status"] == "failed")
    running_cnt = sum(1 for t in store.list_tasks() if scheduler.is_running(t["id"]))
    enabled_cnt = sum(1 for t in store.list_tasks() if t.get("enabled", 1))

    tasks = []
    for t in tasks_src:
        tasks.append({
            "id": t["id"], "name": t["name"], "grp": t["grp"], "owner": t["owner"],
            "cron": t["cron"], "enabled": bool(t.get("enabled", 1)),
            "timeout_min": t["timeout_min"], "retry_times": t["retry_times"],
            "last_run_at": t.get("last_run_at"), "last_status": t.get("last_status"),
            "last_duration": t.get("last_duration"),
            "created_by_name": t.get("created_by_name"),
            "perm": _task_perm(t),
            "running": scheduler.is_running(t["id"]),
            "hist": store.recent_runs(t["id"], 7),
            "config": t.get("config", {}),
        })

    return jsonify({
        "kpi": {
            "today_total": total, "today_success": ok, "today_failed": fail,
            "success_rate": round(ok * 100 / total, 1) if total else 100.0,
            "running": running_cnt, "enabled": enabled_cnt,
        },
        "tasks": tasks,
    })


# ---------------- 任务 CRUD ----------------
@app.route("/api/tasks", methods=["GET", "POST"])
def api_tasks():
    if request.method == "GET":
        return jsonify(store.list_tasks())
    data = request.get_json(force=True)
    user = _current_user()  # 记录创建人（编辑不改变创建人）
    if user:
        data["created_by"] = user.get("open_id")
        data["created_by_name"] = user.get("name")
    tid = store.save_task(_normalize_task(data))
    scheduler.sync_jobs()
    return jsonify({"id": tid, "ok": True})


@app.route("/api/tasks/<int:tid>", methods=["GET", "PUT", "DELETE"])
def api_task(tid):
    task = store.get_task(tid)
    if not task:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    if request.method == "GET":
        return jsonify(task)
    if request.method == "PUT":
        r = _deny(_task_perm(task)["edit"], "只能编辑自己创建的任务（如需操作他人任务请联系管理员）")
        if r:
            return r
    if request.method == "DELETE":
        r = _deny(_task_perm(task)["del"], "只能删除自己创建的任务")
        if r:
            return r
    if request.method == "DELETE":
        store.delete_task(tid)
    else:
        data = request.get_json(force=True)
        store.save_task(_normalize_task({**task, **data, "id": tid}))
    scheduler.sync_jobs()
    return jsonify({"ok": True})


@app.route("/api/tasks/<int:tid>/enabled", methods=["POST"])
def api_task_toggle(tid):
    task = store.get_task(tid)
    if not task:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    r = _deny(_task_perm(task)["toggle"], "只能启停自己创建的任务（高级用户/管理员可操作全部任务）")
    if r:
        return r
    enabled = bool(request.get_json(force=True).get("enabled", True))
    store.set_task_enabled(tid, enabled)
    scheduler.sync_jobs()
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/tasks/<int:tid>/run", methods=["POST"])
def api_task_run(tid):
    task = store.get_task(tid)
    if not task:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    r = _deny(_task_perm(task)["run"], "只能运行自己创建的任务（高级用户/管理员可运行全部任务）")
    if r:
        return r
    rc = scheduler.run_now(tid)
    if rc == -1:
        return jsonify({"ok": False, "error": "该任务正在执行中，请勿重复触发"}), 409
    return jsonify({"ok": True})


def _normalize_task(data):
    data.setdefault("name", "未命名任务")
    data.setdefault("config", {})
    return data


# ---------------- 运行记录 ----------------
@app.route("/api/runs")
def api_runs():
    task_id = request.args.get("task_id", type=int)
    limit = request.args.get("limit", default=100, type=int)
    offset = max(0, request.args.get("offset", default=0, type=int))
    runs = store.list_runs(task_id=task_id, limit=limit, offset=offset)
    resp = jsonify(runs)
    resp.headers["X-Total-Count"] = store.count_runs(task_id=task_id)
    return resp


@app.route("/api/runs/<int:rid>")
def api_run(rid):
    run = store.get_run(rid)
    if not run:
        return jsonify({"ok": False, "error": "运行记录不存在"}), 404
    return jsonify(run)


@app.route("/api/runs/latest/<int:tid>")
def api_run_latest(tid):
    runs = store.list_runs(task_id=tid, limit=1)
    if not runs:
        return jsonify({"ok": False})
    return jsonify(runs[0])


# ---------------- 数据源 CRUD ----------------
@app.route("/api/datasources", methods=["GET", "POST"])
def api_datasources():
    if request.method == "GET":
        out = store.list_datasources()
        for d in out:
            d["perm"] = _ds_perm(d)
        return jsonify(out)
    data = request.get_json(force=True)
    user = _current_user()  # 记录创建人
    if user:
        data["created_by"] = user.get("open_id")
        data["created_by_name"] = user.get("name")
    try:
        did = store.save_datasource(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"id": did, "ok": True})


@app.route("/api/datasources/<int:did>", methods=["PUT", "DELETE"])
def api_datasource(did):
    ds = store.get_datasource(did)
    if not ds:
        return jsonify({"ok": False, "error": "数据源不存在"}), 404
    if request.method == "DELETE":
        r = _deny(_ds_perm(ds)["del"], "只能删除自己创建的数据源")
        if r:
            return r
        store.delete_datasource(did)
        return jsonify({"ok": True})
    r = _deny(_ds_perm(ds)["edit"], "只能编辑自己创建的数据源（高级用户/管理员可编辑全部）")
    if r:
        return r
    data = request.get_json(force=True)
    data["id"] = did
    store.save_datasource(data)
    return jsonify({"ok": True})


@app.route("/api/datasources/<int:did>/test", methods=["POST"])
def api_datasource_test(did):
    ds = store.get_datasource(did)
    if not ds:
        return jsonify({"ok": False, "error": "数据源不存在"}), 404
    probe = {"oracle": "SELECT 1 FROM DUAL", "sqlite": "SELECT 1", "mysql": "SELECT 1"}
    class _ProbeLog:
        def info(self, m): pass
        def warn(self, m): pass
        def error(self, m): pass
    try:
        results = engine.run_sql(ds, [probe.get(ds["db_type"], "SELECT 1")], _ProbeLog())
        return jsonify({"ok": True, "result": str(results[0][0])})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 200


# ---------------- 设置 / 飞书测试 ----------------
@app.route("/api/settings", methods=["GET", "PUT"])
def api_settings():
    if request.method == "GET":
        return jsonify(store.get_settings())
    data = request.get_json(force=True)
    store.save_settings(data)
    return jsonify({"ok": True})


@app.route("/api/feishu/test", methods=["POST"])
def api_feishu_test():
    webhook = (request.get_json(force=True) or {}).get("webhook", "")
    if not webhook:
        webhook = store.get_settings().get("feishu_webhook", "")
    ok, msg = notifier.send_feishu(
        webhook,
        f"这是一条来自数据调度平台的连通性测试消息\n发送时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        title="数据调度平台-Webhook 测试",
    )
    return jsonify({"ok": ok, "message": msg})


# ---------------- 初始化与种子数据 ----------------
def seed():
    import json
    import sqlite3 as sq
    from openpyxl import Workbook

    # 演示 SQLite 库
    if not os.path.exists(config.DEMO_DB_PATH):
        conn = sq.connect(config.DEMO_DB_PATH)
        conn.executescript(
            """
            CREATE TABLE flight_sales(
                biz_date TEXT, route TEXT, bookings INTEGER, revenue REAL, load_factor REAL
            );
            """
        )
        rows = [
            ("2026-10-15", "深圳-北京", 412, 386500.0, 0.92),
            ("2026-10-15", "深圳-上海", 388, 302400.0, 0.88),
            ("2026-10-15", "深圳-成都", 296, 214800.0, 0.85),
            ("2026-10-15", "深圳-杭州", 244, 171200.0, 0.81),
            ("2026-10-15", "深圳-西安", 198, 138600.0, 0.79),
            ("2026-10-16", "深圳-北京", 421, 399700.0, 0.94),
            ("2026-10-16", "深圳-上海", 376, 296100.0, 0.87),
            ("2026-10-16", "深圳-成都", 301, 221000.0, 0.86),
            ("2026-10-16", "深圳-杭州", 238, 168900.0, 0.80),
            ("2026-10-16", "深圳-西安", 205, 143500.0, 0.80),
        ]
        conn.executemany("INSERT INTO flight_sales VALUES(?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

    # 演示 Excel 模板（带公式，用于验证重算）
    demo_tpl = os.path.join(config.TEMPLATE_DIR, "演示_销售日报模板.xlsx")
    if not os.path.exists(demo_tpl):
        wb = Workbook()
        ws = wb.active
        ws.title = "销售明细"
        ws.append(["业务日期", "航线", "订座人数", "销售收入", "客座率"])
        ws2 = wb.create_sheet("汇总")
        ws2["A1"] = "指标"
        ws2["B1"] = "数值"
        ws2["A2"] = "总订座人数"
        ws2["B2"] = "=SUM(销售明细!C2:C100)"
        ws2["A3"] = "总收入"
        ws2["B3"] = "=SUM(销售明细!D2:D100)"
        ws2["A4"] = "平均客座率"
        ws2["B4"] = "=AVERAGE(销售明细!E2:E100)"
        wb.save(demo_tpl)

    if store.list_datasources():
        return

    # 数据源
    ds_jcdb = store.save_datasource({
        "name": "JCDB（经营监控库）", "db_type": "oracle",
        "config": {"user": "a06478", "password": "******", "host": "填写Oracle主机",
                   "port": 1521, "service": "JCDB", "thick": True},
        "remark": "旧脚本中的预算/完成情况库",
    })
    ds_yx = store.save_datasource({
        "name": "YXDBNEW（营销新库）", "db_type": "oracle",
        "config": {"user": "khsy_sj", "password": "******", "host": "填写Oracle主机",
                   "port": 1521, "service": "YXDBNEW", "thick": True},
        "remark": "国深山竞比 / 预售数据",
    })
    ds_demo = store.save_datasource({
        "name": "本地演示库（SQLite）", "db_type": "sqlite",
        "config": {"path": config.DEMO_DB_PATH},
        "remark": "平台内置，供试运行演示",
    })

    base = r"\\10.21.101.11\经营监控室\报表\日常报表（周一至周五）\日报-日监控"

    def stub(code_file, table):
        return (f"-- 请将「代码」目录下 {code_file} 的完整 SQL 粘贴到此处\n"
                f"-- 支持分号分隔的多段 SQL；平台按下方“取结果段”选择数据集\n"
                f"SELECT * FROM {table} WHERE 1=0;")

    # —— 旧脚本完整迁移任务（默认停用，配好数据源与网络盘后启用）——
    migrated = {
        "name": "日常监控日报（旧脚本迁移）",
        "grp": "经营监控 / 日报",
        "owner": "管理员",
        "cron": "0 8 * * 1-5",
        "timeout_min": 60,
        "retry_times": 2,
        "enabled": False,
        "config": {
            "wait_interval": 600, "wait_retry": 10, "wait_notify_each": True,
            "wait_files": [
                {"name": "excel1 初版模板", "path": os.path.join(base, "日常监控_初版模板1.xlsx")},
                {"name": "excel2 国深山对比模板", "path": os.path.join(base, "看板数据2_国深山对比模板.xlsx")},
                {"name": "excel3 预售模板", "path": os.path.join(base, "看板数据3_预售数据模板.xlsx")},
                {"name": "excel4 KY日数据", "path": os.path.join(base, "KY日数据（TO深航）{{t1_6bit}}.xlsx")},
                {"name": "excel5 收益日报", "path": os.path.join(base, "收益日报-{{yyyyMMdd}}.xlsx")},
                {"name": "excel6 滚动预测",
                 "path": rf"\\10.21.23.113\rm\总部控制中心\%每日监控日报\{{{{mmdd}}}}\滚动预测未来7天{{{{mmdd}}}}.xlsx"},
                {"name": "excel7 预算目标模板", "path": os.path.join(base, "看板数据1_预算目标模板.xlsx")},
                {"name": "excel8 财务日利润", "path": os.path.join(base, "财务日利润更新{{mmdd}}.xlsx")},
            ],
            "excel_reads": [
                {"alias": "KY月累计 A1:N22", "path": os.path.join(base, "KY日数据（TO深航）{{t1_6bit}}.xlsx"),
                 "sheet": "ky数据", "start": "A1", "end": "N22", "var": "data_kh_month"},
                {"alias": "KY分天 A2:Q150", "path": os.path.join(base, "KY日数据（TO深航）{{t1_6bit}}.xlsx"),
                 "sheet": "分天经营数据", "start": "A2", "end": "Q150", "var": "data_kh_day"},
                {"alias": "深航利润预测", "path": rf"\\10.21.23.113\rm\总部控制中心\%每日监控日报\{{{{mmdd}}}}\滚动预测未来7天{{{{mmdd}}}}.xlsx",
                 "sheet": "简易模型", "start": "A32", "end": "L45", "var": "data_profit"},
                {"alias": "昆航后期预测", "path": os.path.join(base, "收益日报-{{yyyyMMdd}}.xlsx"),
                 "sheet": "后期经营预测", "start": "A13", "end": "N28", "var": "data_later"},
                {"alias": "财务日利润", "path": os.path.join(base, "财务日利润更新{{mmdd}}.xlsx"),
                 "sheet": "日利润", "start": "A4", "end": "E47", "var": "data_finance"},
            ],
            "sql_steps": [
                {"name": "代码1 月累计预算完成情况", "datasource_id": ds_jcdb, "segment": 0,
                 "sql": stub("1月累计预算完成情况JCDB.sql", "v_month_budget"), "var": "data_body_1"},
                {"name": "代码2 分天预算完成情况", "datasource_id": ds_jcdb, "segment": 0,
                 "sql": stub("2分天预算完成情况JCDB.sql", "v_day_budget"), "var": "data_body_2"},
                {"name": "代码5 同期分天预算", "datasource_id": ds_jcdb, "segment": 0,
                 "sql": stub("5同期分天预算完成情况JCDB.sql", "v_day_budget_t1"), "var": "data_body_21"},
                {"name": "代码3 国深山竞比-段1", "datasource_id": ds_yx, "segment": 0,
                 "sql": stub("3国深山竞比YXDBNEW.sql", "v_compare_a") + "\n-- 第二段结果将贴入 J2，请在同一步骤内保留两段 SQL",
                 "var": "data_3_1"},
                {"name": "代码3 国深山竞比-段2", "datasource_id": ds_yx, "segment": 1,
                 "sql": stub("3国深山竞比YXDBNEW.sql", "v_compare_b"), "var": "data_3_2"},
                {"name": "代码4 深航国内预售", "datasource_id": ds_yx, "segment": 0,
                 "sql": stub("4深航国内_预售YXDBNEW.sql", "v_presale_zh"), "var": "data_4"},
                {"name": "代码6 昆航国内预售", "datasource_id": ds_yx, "segment": 0,
                 "sql": stub("6昆航_国内预售YXDBNEW.sql", "v_presale_kh"), "var": "data_6"},
            ],
            "templates": [
                {"name": "初版汇总表", "path": os.path.join(base, "日常监控_初版模板1.xlsx"),
                 "output": "日常监控_初版{{mmdd}}.xlsx", "recalc": True,
                 "writes": [
                     {"sheet": "深航_月累计经营数据", "cell": "B2", "var": "data_body_1"},
                     {"sheet": "深航_分天经营数据", "cell": "A2", "var": "data_body_2"},
                     {"sheet": "深航_同期分天经营数据", "cell": "A2", "var": "data_body_21"},
                     {"sheet": "昆航_月累计经营数据", "cell": "Z1", "var": "data_kh_month"},
                     {"sheet": "昆航_分天经营数据", "cell": "C2", "var": "data_kh_day"},
                     {"sheet": "深航_分天利润预测", "cell": "A2", "var": "data_profit"},
                     {"sheet": "昆航_分天利润预测", "cell": "B2", "var": "data_later"},
                     {"sheet": "深航_日利润", "cell": "A2", "var": "data_finance"},
                 ]},
                {"name": "看板1 预算目标", "path": os.path.join(base, "看板数据1_预算目标模板.xlsx"),
                 "output": "看板数据1_预算目标{{mmdd}}.xlsx", "recalc": True,
                 "reads_before": [
                     {"from_output": True, "file": "日常监控_初版{{mmdd}}.xlsx",
                      "sheet": "深航_预算目标", "start": "A1", "end": "K29", "var": "bud_zh"},
                     {"from_output": True, "file": "日常监控_初版{{mmdd}}.xlsx",
                      "sheet": "昆航_预算目标", "start": "A1", "end": "K29", "var": "bud_kh"},
                     {"from_output": True, "file": "日常监控_初版{{mmdd}}.xlsx",
                      "sheet": "深昆_预算目标", "start": "A1", "end": "K29", "var": "bud_zk"},
                     {"from_output": True, "file": "日常监控_初版{{mmdd}}.xlsx",
                      "sheet": "深航_分天趋势", "start": "C1", "end": "D32", "var": "tr_zh_cd"},
                     {"from_output": True, "file": "日常监控_初版{{mmdd}}.xlsx",
                      "sheet": "深航_分天趋势", "start": "R1", "end": "AF32", "var": "tr_zh_rab"},
                     {"from_output": True, "file": "日常监控_初版{{mmdd}}.xlsx",
                      "sheet": "昆航_分天趋势", "start": "C1", "end": "D32", "var": "tr_kh_cd"},
                     {"from_output": True, "file": "日常监控_初版{{mmdd}}.xlsx",
                      "sheet": "昆航_分天趋势", "start": "R1", "end": "AF32", "var": "tr_kh_rab"},
                     {"from_output": True, "file": "日常监控_初版{{mmdd}}.xlsx",
                      "sheet": "深昆_分天趋势", "start": "C1", "end": "D32", "var": "tr_zk_cd"},
                     {"from_output": True, "file": "日常监控_初版{{mmdd}}.xlsx",
                      "sheet": "深昆_分天趋势", "start": "R1", "end": "AF32", "var": "tr_zk_rab"},
                 ],
                 "writes": [
                     {"sheet": "深航_预算目标", "cell": "A1", "var": "bud_zh"},
                     {"sheet": "昆航_预算目标", "cell": "A1", "var": "bud_kh"},
                     {"sheet": "深昆_预算目标", "cell": "A1", "var": "bud_zk"},
                     {"sheet": "深航_分天趋势", "cell": "A1", "merge": ["tr_zh_cd", "tr_zh_rab"]},
                     {"sheet": "昆航_分天趋势", "cell": "A1", "merge": ["tr_kh_cd", "tr_kh_rab"]},
                     {"sheet": "深昆_分天趋势", "cell": "A1", "merge": ["tr_zk_cd", "tr_zk_rab"]},
                 ]},
                {"name": "看板2 国深山对比", "path": os.path.join(base, "看板数据2_国深山对比模板.xlsx"),
                 "output": "看板数据2_国深山对比{{mmdd}}.xlsx", "recalc": True,
                 "writes": [
                     {"sheet": "数据源", "cell": "B2", "var": "data_3_1"},
                     {"sheet": "数据源", "cell": "J2", "var": "data_3_2"},
                 ]},
                {"name": "看板3 预售数据", "path": os.path.join(base, "看板数据3_预售数据模板.xlsx"),
                 "output": "看板数据3_预售数据{{mmdd}}.xlsx", "recalc": True,
                 "writes": [
                     {"sheet": "深航_预售", "cell": "A3", "var": "data_4"},
                     {"sheet": "昆航_预售", "cell": "A3", "var": "data_6"},
                 ]},
            ],
            "output_dir": os.path.join(base, "{{mmdd}}"),
            "python_enabled": False, "python_code": "",
            "notify": {"on_success": True, "on_failure": True, "webhook": "",
                       "template": "【{{任务名}}】执行{{状态}}\n执行时间：{{执行时间}}　耗时：{{耗时}}\n结果文件：\n{{文件路径}}"},
        },
    }
    store.save_task(migrated)

    # —— 可直接试运行成功的演示任务 ——
    store.save_task({
        "name": "演示：航线销售查询并回填Excel",
        "grp": "平台演示",
        "owner": "管理员",
        "cron": "0 9 * * *",
        "timeout_min": 15,
        "retry_times": 0,
        "enabled": False,
        "config": {
            "wait_files": [],
            "excel_reads": [],
            "sql_steps": [
                {"name": "近两日航线销售", "datasource_id": ds_demo, "segment": 0,
                 "sql": "SELECT biz_date, route, bookings, revenue, load_factor FROM flight_sales ORDER BY biz_date, route;",
                 "var": "df_sales"},
            ],
            "templates": [
                {"name": "销售日报", "path": demo_tpl,
                 "output": "航线销售日报_{{yyyyMMdd}}.xlsx", "recalc": True,
                 "writes": [{"sheet": "销售明细", "cell": "A2", "var": "df_sales"}]},
            ],
            "output_dir": os.path.join(config.OUTPUT_DIR, "demo_excel"),
            "python_enabled": False, "python_code": "",
            "notify": {"on_success": False, "on_failure": True, "webhook": ""},
        },
    })

    store.save_task({
        "name": "演示：SQL取数+Python导出CSV",
        "grp": "平台演示",
        "owner": "管理员",
        "cron": "30 9 * * *",
        "timeout_min": 15,
        "retry_times": 0,
        "enabled": False,
        "config": {
            "wait_files": [],
            "excel_reads": [],
            "sql_steps": [
                {"name": "当日销售汇总", "datasource_id": ds_demo, "segment": 0,
                 "sql": "SELECT biz_date, COUNT(*) AS cnt, SUM(bookings) AS bk, ROUND(SUM(revenue),2) AS rev "
                        "FROM flight_sales GROUP BY biz_date ORDER BY biz_date;",
                 "var": "df_summary"},
            ],
            "templates": [],
            "output_dir": os.path.join(config.OUTPUT_DIR, "demo_python"),
            "python_enabled": True,
            "python_code": (
                "import csv, os\n"
                "df = to_df('df_summary')\n"
                "out = os.path.join(OUTPUT_DIR, '销售汇总_' + tokens['yyyyMMdd'] + '.csv')\n"
                "df.to_csv(out, index=False, encoding='utf-8-sig')\n"
                "print('[OK] 导出', len(df), '行 ->', out)\n"
            ),
            "notify": {"on_success": False, "on_failure": True, "webhook": ""},
        },
    })


if __name__ == "__main__":
    store.init_db()
    seed()
    scheduler.start()
    _log.info(f"数据调度平台已启动: http://{config.WEB_HOST}:{config.WEB_PORT}")
    print(f"数据调度平台已启动: http://{config.WEB_HOST}:{config.WEB_PORT}")
    try:
        app.run(host=config.WEB_HOST, port=config.WEB_PORT, threaded=True)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        _log.info("Web 服务停止，正在清理调度器与心跳线程...")
        scheduler.shutdown()
        _log.info("数据调度平台已退出")
