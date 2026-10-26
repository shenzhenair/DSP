# -*- coding: utf-8 -*-
"""
任务执行引擎
————————————
执行顺序（与旧版《日常监控化脚本》一致并泛化）：
  1. 前置源文件检查（缺失则按间隔/次数重试，可飞书通知）
  2. Excel 数据源按区域读取 -> 结果变量
  3. SQL 步骤按顺序执行（支持分号切分多段、DDL/DML 自动提交）-> 结果变量
  4. 打开 Excel 模板，把变量粘贴到指定 sheet/单元格，另存到输出目录
  5. 调用本机 Excel（win32com）全量重算公式（可关闭）
  6. 可选 Python 自定义脚本（接收全部结果变量）
  7. 按配置推送飞书通知
"""
import datetime
import gc
import io
import os
import re
import time
import traceback
import contextlib

import store
import notifier
from logutil import get_logger

_log = get_logger("engine")

DDL_RE = re.compile(r"^(CREATE|DROP|DELETE|INSERT|GRANT|UPDATE|TRUNCATE|ALTER|MERGE)", re.I)


# ============================================================
# 日志
# ============================================================
class RunLogger:
    """任务运行日志：同时写入运行记录（页面展示）与服务端日志文件"""

    def __init__(self, run_id):
        self.run_id = run_id

    def _w(self, level, msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        try:
            store.append_run_log(self.run_id, line)
        except Exception:
            pass
        print(line)
        fn = {"INFO": _log.info, "WARN": _log.warning, "ERROR": _log.error}.get(level, _log.info)
        fn(f"run#{self.run_id} {msg}")

    def info(self, msg):
        self._w("INFO", msg)

    def warn(self, msg):
        self._w("WARN", msg)

    def error(self, msg):
        self._w("ERROR", msg)


# ============================================================
# 日期变量与模板渲染
# ============================================================
def date_tokens(now=None):
    t = now or datetime.datetime.now()
    t1 = t - datetime.timedelta(days=1)
    return {
        "yyyy": t.strftime("%Y"),
        "MM": t.strftime("%m"),
        "dd": t.strftime("%d"),
        "HH": t.strftime("%H"),
        "mm": t.strftime("%M"),
        "ss": t.strftime("%S"),
        "yyyyMMdd": t.strftime("%Y%m%d"),
        "mmdd": t.strftime("%m%d"),
        "t1_yyyyMMdd": t1.strftime("%Y%m%d"),
        "t1_6bit": t1.strftime("%y%m%d"),
        "t1_mmdd": t1.strftime("%m%d"),
        "biz_date": t.strftime("%Y-%m-%d"),
        "biz_date_t1": t1.strftime("%Y-%m-%d"),
    }


_TPL_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}|\$\{\s*([a-zA-Z0-9_]+)\s*\}")


def render(text, tokens):
    """支持 {{yyyyMMdd}} / ${biz_date} 两种占位符"""
    if text is None:
        return ""

    def _sub(m):
        key = m.group(1) or m.group(2)
        return str(tokens.get(key, m.group(0)))

    return _TPL_RE.sub(_sub, str(text))


# ============================================================
# SQL 处理（移植旧脚本 load_sql_file 的清洗逻辑）
# ============================================================
def split_sql(content):
    if not content:
        return []
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    # 清洗 BOM 残留、零宽字符
    content = content.replace("﻿", "").replace("​", "").replace("‌", "")
    out = []
    for seg in content.split(";"):
        s = seg.strip()
        if s and not s.isspace():
            out.append(s)
    return out


def _normalize_rows(rows):
    """把 Decimal/datetime 等转为可写 Excel/可序列化的值"""
    out = []
    for row in rows:
        nr = []
        for v in row:
            if isinstance(v, (datetime.datetime, datetime.date)):
                nr.append(v)
            else:
                nr.append(v)
        out.append(nr)
    return out


# ============================================================
# 数据库执行
# ============================================================
def _run_oracle(ds, statements, logger):
    import oracledb

    cfg = ds["config"]
    dsn = cfg.get("dsn") or f"{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 1521)}/{cfg.get('service', 'ORCL')}"
    # Thick 模式（兼容老版本 Oracle），进程内只初始化一次；数据源未配置时取系统设置默认值
    settings = store.get_settings()
    use_thick = str(cfg.get("thick", False)).lower() in ("1", "true", "yes") or \
        str(settings.get("oracle_thick", "false")).lower() == "true"
    if use_thick:
        client_dir = cfg.get("client_dir") or settings.get("oracle_client_dir") or None
        try:
            oracledb.init_oracle_client(lib_dir=client_dir)
            logger.info(f"Oracle Thick 客户端已初始化 ({client_dir or '默认路径'})")
        except Exception as e:
            if "DPI-1072" in str(e) or "already been initialized" in str(e):
                pass
            else:
                logger.warn(f"Thick 初始化失败，改用 Thin 模式: {e}")

    user, pwd = cfg.get("user", ""), cfg.get("password", "")
    conn = oracledb.connect(user=user, password=pwd, dsn=dsn)
    results = []
    try:
        cur = conn.cursor()
        for i, sql in enumerate(statements, 1):
            is_ddl = bool(DDL_RE.match(sql.upper().strip()))
            cur.execute(sql)
            if is_ddl:
                conn.commit()
                results.append(([], []))
                logger.info(f"  第{i}段：DDL/DML 已执行并提交")
            else:
                rows = _normalize_rows(cur.fetchall())
                header = [c[0] for c in (cur.description or [])]
                results.append((rows, header))
                logger.info(f"  第{i}段：返回 {len(rows)} 行")
        return results
    finally:
        conn.close()


def _run_sqlite(ds, statements, logger):
    import sqlite3

    path = ds["config"].get("path", ":memory:")
    conn = sqlite3.connect(path, timeout=30)
    results = []
    try:
        cur = conn.cursor()
        for i, sql in enumerate(statements, 1):
            is_ddl = bool(DDL_RE.match(sql.upper().strip()))
            cur.execute(sql)
            if is_ddl:
                conn.commit()
                results.append(([], []))
                logger.info(f"  第{i}段：DDL/DML 已执行并提交")
            else:
                rows = _normalize_rows(cur.fetchall())
                header = [c[0] for c in (cur.description or [])]
                results.append((rows, header))
                logger.info(f"  第{i}段：返回 {len(rows)} 行")
        return results
    finally:
        conn.close()


def _run_mysql(ds, statements, logger):
    import pymysql

    cfg = ds["config"]
    conn = pymysql.connect(
        host=cfg.get("host", "127.0.0.1"), port=int(cfg.get("port", 3306)),
        user=cfg.get("user", ""), password=cfg.get("password", ""),
        database=cfg.get("service", ""), charset="utf8mb4",
    )
    results = []
    try:
        cur = conn.cursor()
        for i, sql in enumerate(statements, 1):
            is_ddl = bool(DDL_RE.match(sql.upper().strip()))
            cur.execute(sql)
            if is_ddl:
                conn.commit()
                results.append(([], []))
                logger.info(f"  第{i}段：DDL/DML 已执行并提交")
            else:
                rows = _normalize_rows(cur.fetchall())
                header = [c[0] for c in (cur.description or [])]
                results.append((rows, header))
                logger.info(f"  第{i}段：返回 {len(rows)} 行")
        return results
    finally:
        conn.close()


def run_sql(ds, statements, logger):
    runner = {"oracle": _run_oracle, "sqlite": _run_sqlite, "mysql": _run_mysql}.get(ds["db_type"])
    if not runner:
        raise ValueError(f"不支持的数据源类型: {ds['db_type']}")
    return runner(ds, statements, logger)


# ============================================================
# Excel 处理（移植旧脚本 openpyxl + win32com 逻辑）
# ============================================================
def cell_to_row_col(cell_addr):
    from openpyxl.utils import column_index_from_string
    col = "".join(c for c in cell_addr if c.isalpha())
    row = "".join(c for c in cell_addr if c.isdigit())
    return int(row), column_index_from_string(col.upper())


def read_excel_range(path, sheet, start_cell, end_cell, logger):
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        ws = wb[sheet]
        r1, c1 = cell_to_row_col(start_cell)
        r2, c2 = cell_to_row_col(end_cell)
        data = []
        for r in range(r1, r2 + 1):
            data.append([ws.cell(row=r, column=c).value for c in range(c1, c2 + 1)])
        return data
    finally:
        wb.close()


def paste_data(ws, start_cell, data):
    r0, c0 = cell_to_row_col(start_cell)
    for i, row in enumerate(data):
        for j, val in enumerate(row):
            ws.cell(row=r0 + i, column=c0 + j, value=val)


def recalculate_excel(path, logger):
    """
    安全版公式重算（移植旧脚本）：
    DispatchEx 独立实例 / 打开前设手动计算 / UpdateLinks=0 /
    CalculateFullRebuild / 先 Save 再 Close / 只回收自己的实例。
    """
    if not os.path.exists(path):
        logger.warn(f"文件不存在，跳过重算: {path}")
        return
    try:
        import win32com.client as win32
    except ImportError:
        logger.warn("未安装 pywin32 或非 Windows 环境，跳过公式重算")
        return

    abs_path = os.path.abspath(path)
    app = wb_com = None
    try:
        logger.info(f"启动 Excel 重算公式: {os.path.basename(abs_path)}")
        app = win32.DispatchEx("Excel.Application")
        app.Visible = False
        app.DisplayAlerts = False
        try:
            app.Calculation = -4135  # xlCalculationManual
        except Exception:
            pass
        wb_com = app.Workbooks.Open(abs_path, UpdateLinks=0, ReadOnly=False)
        try:
            app.CalculateFullRebuild()
        except Exception:
            for sht in wb_com.Worksheets:
                sht.Calculate()
        wb_com.Save()
        wb_com.Close(SaveChanges=False)
        wb_com = None
        app.Quit()
        app = None
        logger.info("公式重算完成")
    except Exception as e:
        logger.warn(f"公式重算失败（不影响数据写入）: {e}")
    finally:
        try:
            if wb_com is not None:
                wb_com.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        gc.collect()


# ============================================================
# 前置文件等待（移植旧脚本 wait_for_all_files）
# ============================================================
def wait_for_files(files, interval_sec, max_retry, tokens, logger, webhook="", notify_each=False):
    """files: [{'name': 'excel4', 'path': '...'}]；初始1次 + max_retry 次重试"""
    rendered = [(f.get("name", f"file{i}"), render(f.get("path", ""), tokens)) for i, f in enumerate(files)]

    def missing():
        return [(n, p) for n, p in rendered if not os.path.exists(p)]

    total = max_retry + 1
    for rnd in range(1, total + 1):
        miss = missing()
        if not miss:
            logger.info(f"[文件检查·第{rnd}次] 全部 {len(rendered)} 个源文件存在，检查通过")
            return True
        logger.warn(f"[文件检查·第{rnd}次] 缺失 {len(miss)} 个: " + "；".join(n for n, _ in miss))
        for n, p in miss:
            logger.warn(f"    缺失 {n}: {p}")
        if notify_each and webhook:
            lines = [f"【数据源文件检查 · 第{rnd}/{total}次】", f"任务时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}", "",
                     "缺失文件："] + [f"- {n}: {p}" for n, p in miss]
            if rnd < total:
                lines += ["", f"等待 {interval_sec} 秒后重试"]
            notifier.send_feishu(webhook, "\n".join(lines), title="数据调度-数据源检查通知")
        if rnd < total:
            time.sleep(interval_sec)
    logger.error("已达最大重试次数，仍有文件缺失")
    return False


# ============================================================
# Python 自定义步骤
# ============================================================
def run_python_step(code, variables, tokens, output_dir, logger):
    logger.info("执行 Python 自定义脚本 ...")
    buf = io.StringIO()
    env = {
        "vars": variables,           # {变量名: {"header": [...], "rows": [[...]]}}
        "tokens": tokens,
        "OUTPUT_DIR": output_dir,
        "OUTPUT_PATH": output_dir,
        "os": os,
        "datetime": datetime,
    }

    def to_df(name):
        import pandas as pd
        v = variables[name]
        return pd.DataFrame(v["rows"], columns=v["header"] if v["header"] else None)

    env["to_df"] = to_df
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<task_python_step>", "exec"), env, {})
        text = buf.getvalue().strip()
        if text:
            for ln in text.splitlines():
                logger.info(f"[PY] {ln}")
        logger.info("Python 脚本执行完成")
        return True
    except Exception:
        text = buf.getvalue().strip()
        for ln in text.splitlines():
            logger.info(f"[PY] {ln}")
        logger.error("Python 脚本执行失败:\n" + traceback.format_exc())
        return False


# ============================================================
# 主执行流程
# ============================================================
def execute_task(task, trigger, run_id):
    cfg = task.get("config", {}) or {}
    logger = RunLogger(run_id)
    tokens = date_tokens()
    settings = store.get_settings()
    webhook = cfg.get("notify", {}).get("webhook") or settings.get("feishu_webhook", "")
    output_dir = render(cfg.get("output_dir", ""), tokens) or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "outputs", str(task["id"]))
    os.makedirs(output_dir, exist_ok=True)
    variables = {}
    outputs = []
    t0 = time.time()
    name = task["name"]

    logger.info("=" * 60)
    logger.info(f"任务「{name}」开始执行（触发方式: {trigger}）")
    logger.info(f"业务日期: {tokens['biz_date']}  输出目录: {output_dir}")

    try:
        # 1) 前置文件检查
        wait_files = cfg.get("wait_files", [])
        if wait_files:
            logger.info(f"[1/6] 前置文件检查，共 {len(wait_files)} 个依赖")
            ok = wait_for_files(
                wait_files,
                int(cfg.get("wait_interval", 600)),
                int(cfg.get("wait_retry", 10)),
                tokens, logger, webhook,
                notify_each=bool(cfg.get("wait_notify_each", False)),
            )
            if not ok:
                raise RuntimeError("前置源文件不齐，任务终止")
        else:
            logger.info("[1/6] 无前置文件依赖，跳过")

        # 2) Excel 数据源读取
        reads = cfg.get("excel_reads", [])
        logger.info(f"[2/6] Excel 数据源读取，共 {len(reads)} 个")
        for i, rd in enumerate(reads, 1):
            path = render(rd.get("path", ""), tokens)
            var = rd.get("var") or f"df_excel_{i}"
            logger.info(f"  读取 {rd.get('alias', path)} -> {var} ({rd.get('sheet')}!{rd.get('start')}:{rd.get('end')})")
            data = read_excel_range(path, rd["sheet"], rd["start"], rd["end"], logger)
            variables[var] = {"header": [], "rows": data, "source": path}
            logger.info(f"    读取 {len(data)} 行")

        # 3) SQL 步骤
        sql_steps = cfg.get("sql_steps", [])
        logger.info(f"[3/6] SQL 执行步骤，共 {len(sql_steps)} 个")
        for i, st in enumerate(sql_steps, 1):
            ds_id = st.get("datasource_id")
            ds = store.get_datasource(ds_id) if ds_id else None
            if not ds:
                raise RuntimeError(f"SQL 步骤「{st.get('name', i)}」未选择有效数据源")
            if st.get("source", "inline") == "file":
                sql_path = render(st.get("sql_path", ""), tokens)
                if not os.path.exists(sql_path):
                    raise RuntimeError(f"步骤{i} SQL 文件不存在: {sql_path}")
                with open(sql_path, "r", encoding="utf-8-sig") as f:
                    sql_text = f.read()
                logger.info(f"  步骤{i}「{st.get('name', '')}」引用 SQL 文件 {sql_path}")
            else:
                sql_text = st.get("sql", "")
            sql_text = render(sql_text, tokens)
            statements = split_sql(sql_text)
            if not statements:
                logger.warn(f"  步骤{i}「{st.get('name', '')}」SQL 为空，跳过")
                continue
            var = st.get("var") or f"df_sql_{i}"
            seg = int(st.get("segment", 0))
            logger.info(f"  步骤{i}「{st.get('name', '')}」-> 数据源 {ds['name']}({ds['db_type']})，共 {len(statements)} 段，取第 {seg + 1} 段结果 -> {var}")
            results = run_sql(ds, statements, logger)
            if seg >= len(results):
                raise RuntimeError(f"步骤{i} 指定的结果段 {seg + 1} 超出实际段数 {len(results)}")
            rows, header = results[seg]
            variables[var] = {"header": header, "rows": rows, "source": ds["name"]}
            logger.info(f"    {var} 获得 {len(rows)} 行 x {len(header)} 列")

        # 4/5) 模板写入 + 保存 + 重算
        templates = cfg.get("templates", [])
        logger.info(f"[4/6] Excel 模板写入，共 {len(templates)} 个模板")
        from openpyxl import load_workbook

        for ti, tpl in enumerate(templates, 1):
            tpl_path = render(tpl.get("path", ""), tokens)
            out_name = render(tpl.get("output", f"输出{ti}_{tokens['mmdd']}.xlsx"), tokens)
            out_path = os.path.join(output_dir, out_name)
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            logger.info(f"  模板{ti}: {os.path.basename(tpl_path)} -> {out_name}")

            # 读取此前已生成的产物（对应旧脚本步骤4：看板1 从初版文件取数）
            for rb in tpl.get("reads_before", []):
                src_name = render(rb.get("file", rb.get("path", "")), tokens)
                src_path = os.path.join(output_dir, src_name) if rb.get("from_output", True) else render(rb.get("path", ""), tokens)
                rvar = rb.get("var")
                logger.info(f"    依赖读取 {os.path.basename(src_path)} [{rb.get('sheet')}!{rb.get('start')}:{rb.get('end')}] -> {rvar}")
                if not os.path.exists(src_path):
                    raise RuntimeError(f"依赖产物不存在: {src_path}（请检查模板顺序）")
                variables[rvar] = {
                    "header": [],
                    "rows": read_excel_range(src_path, rb["sheet"], rb["start"], rb["end"], logger),
                    "source": src_path,
                }

            wb = load_workbook(tpl_path)
            try:
                for wr in tpl.get("writes", []):
                    ws = wb[wr["sheet"]]
                    if wr.get("merge"):
                        # 多变量横向拼接（对应旧脚本 data_cd[i] + data_rab[i]）
                        parts = [variables[v]["rows"] for v in wr["merge"]]
                        width = sum(len(p[0]) if p and p[0] else 0 for p in parts)
                        n = max((len(p) for p in parts), default=0)
                        merged = []
                        for i in range(n):
                            row = []
                            for p in parts:
                                row += p[i] if i < len(p) else [None] * (len(p[0]) if p and p[0] else 0)
                            merged.append(row)
                        paste_data(ws, wr.get("cell", "A1"), merged)
                        logger.info(f"    写入 {wr['sheet']}!{wr.get('cell')} <- 合并{wr['merge']} ({n} 行 x {width} 列)")
                        continue
                    var = wr.get("var", "")
                    if var not in variables:
                        raise RuntimeError(f"模板{ti} 写入变量「{var}」不存在，请检查 SQL/读取步骤配置")
                    paste_data(ws, wr.get("cell", "A1"), variables[var]["rows"])
                    logger.info(f"    写入 {wr['sheet']}!{wr.get('cell')} <- {var} ({len(variables[var]['rows'])} 行)")
                wb.save(out_path)
            finally:
                wb.close()
            outputs.append(out_path)
            if tpl.get("recalc", True):
                logger.info("[5/6] 公式重算")
                recalculate_excel(out_path, logger)
        if not templates:
            logger.info("[4/6] 无 Excel 模板配置，跳过")
            logger.info("[5/6] 无重算任务，跳过")

        # 6) Python 自定义步骤
        if cfg.get("python_enabled") and cfg.get("python_code", "").strip():
            logger.info("[6/6] Python 自定义处理")
            if not run_python_step(cfg["python_code"], variables, tokens, output_dir, logger):
                raise RuntimeError("Python 自定义脚本执行失败")
        else:
            logger.info("[6/6] 未启用 Python 自定义步骤")

        dur = time.time() - t0
        logger.info(f"任务「{name}」执行成功，耗时 {dur:.1f} 秒；产出 {len(outputs)} 个文件")
        _notify(cfg, settings, webhook, True, name, tokens, dur, outputs, "", logger)
        return True, outputs, ""

    except Exception as e:
        dur = time.time() - t0
        err = f"{type(e).__name__}: {e}"
        logger.error(f"任务「{name}」执行失败（{dur:.1f}秒）: {err}")
        logger.error(traceback.format_exc(limit=6))
        _notify(cfg, settings, webhook, False, name, tokens, dur, outputs, err, logger)
        return False, outputs, err


def _notify(cfg, settings, webhook, success, name, tokens, dur, outputs, error, logger):
    ncfg = cfg.get("notify", {}) or {}
    on_success = ncfg.get("on_success", str(settings.get("notify_on_success", "false")).lower() == "true")
    on_failure = ncfg.get("on_failure", str(settings.get("notify_on_failure", "true")).lower() == "true")
    if success and not on_success:
        return
    if (not success) and not on_failure:
        return
    if str(settings.get("feishu_enabled", "false")).lower() != "true":
        logger.info("飞书通知总开关关闭，跳过通知")
        return

    status = "成功" if success else "失败"
    if ncfg.get("template"):
        content = notifier.render_template(ncfg["template"], {
            "任务名": name, "状态": status, "执行时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "耗时": f"{dur:.1f} 秒", "文件路径": "\n".join(outputs) or "无", "错误": error,
        })
    else:
        lines = [f"【{name}】执行{status}",
                 f"执行时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}　耗时：{dur:.1f} 秒"]
        if success:
            lines += ["产出文件："] + [f"- {p}" for p in outputs]
        else:
            lines += [f"失败原因：{error}"]
        content = "\n".join(lines)
    ok, msg = notifier.send_feishu(webhook, content, title=f"数据调度-{name}-{status}通知")
    logger.info(f"飞书通知: {msg}" if ok else f"飞书通知未送达: {msg}")
