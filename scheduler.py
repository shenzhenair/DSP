# -*- coding: utf-8 -*-
"""调度层：APScheduler 定时触发 + 线程池执行（同一任务禁止并发）+ 心跳守护"""
import datetime
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import engine
import store
from logutil import get_logger

_log = get_logger("scheduler")
_heartbeat_log = get_logger("heartbeat")

_scheduler = None
_pool = ThreadPoolExecutor(max_workers=config.WORKER_THREADS)
_running = set()
_lock = threading.Lock()
_heartbeat_thread = None
_started_at = None
_hb_stop = threading.Event()


def _job_id(task_id):
    return f"task_{task_id}"


def _execute(task_id, trigger):
    task = store.get_task(task_id)
    if not task:
        _log.warning(f"任务#{task_id} 不存在，跳过执行")
        return
    with _lock:
        if task_id in _running:
            _log.warning(f"任务「{task['name']}」上一次执行未结束，本次触发({trigger})跳过")
            return
        _running.add(task_id)
    run_id = store.create_run(task_id, task["name"], trigger)
    _log.info(f"▶ 任务「{task['name']}」开始执行 (run#{run_id}, 触发: {trigger})")
    try:
        ok, outputs, err = engine.execute_task(task, trigger, run_id)
        dur = store.finish_run(run_id, "success" if ok else "failed", outputs, err)
        store.mark_task_result(task_id, "success" if ok else "failed", dur or 0)
        _log.info(f"✔ 任务「{task['name']}」执行{'成功' if ok else '失败'} (run#{run_id}，耗时 {dur or 0:.1f}s"
                  + (f"，错误: {err}" if err else "") + ")")
    except Exception as e:  # 引擎兜底之外的保险
        _log.error(f"任务「{task['name']}」调度器层异常 (run#{run_id}): {e}\n{traceback.format_exc(limit=5)}")
        store.append_run_log(run_id, f"[FATAL] 调度器异常: {e}")
        store.finish_run(run_id, "failed", [], str(e))
        store.mark_task_result(task_id, "failed", 0)
    finally:
        with _lock:
            _running.discard(task_id)


def run_now(task_id):
    """手动/API 立即触发，返回 run_id（先占位创建，实际 run_id 在线程内创建）"""
    task = store.get_task(task_id)
    if not task:
        return None
    with _lock:
        if task_id in _running:
            return -1
    _pool.submit(_execute, task_id, "manual")
    return 0


def is_running(task_id):
    return task_id in _running


def running_count():
    return len(_running)


def uptime_seconds():
    if not _started_at:
        return 0
    return round(time.time() - _started_at)


def sync_jobs():
    """根据 DB 中 enabled 的任务重建定时作业"""
    assert _scheduler is not None
    try:
        existing = {j.id for j in _scheduler.get_jobs()}
        wanted = set()
        for task in store.list_tasks():
            jid = _job_id(task["id"])
            if task.get("enabled", 1):
                wanted.add(jid)
                try:
                    trigger = CronTrigger.from_crontab(task["cron"])
                except Exception:
                    _log.warning(f"任务「{task['name']}」cron 表达式无效: {task['cron']}")
                    continue
                if jid in existing:
                    _scheduler.reschedule_job(jid, trigger=trigger)
                else:
                    _scheduler.add_job(
                        lambda tid=task["id"]: _pool.submit(_execute, tid, "auto"),
                        trigger=trigger, id=jid, max_instances=1, coalesce=True,
                        misfire_grace_time=600, replace_existing=True,
                    )
        for jid in existing - wanted:
            if jid.startswith("task_"):
                _scheduler.remove_job(jid)
    except Exception as e:
        # 同步失败不允许打断调度器 / Web 服务
        _log.error(f"定时作业同步失败（调度器保持运行）: {e}\n{traceback.format_exc(limit=4)}")


def _heartbeat_loop():
    """心跳守护线程：定期自检数据库与工作线程，写日志 + 落库，避免服务静默中断。"""
    counter = 0
    _heartbeat_log.info(f"心跳线程已启动（间隔 {config.HEARTBEAT_INTERVAL}s）")
    while not _hb_stop.is_set():
        try:
            counter += 1
            alive, note = True, ""
            try:
                store.get_heartbeat()  # 数据库连通性自检
            except Exception as e:
                alive, note = False, f"数据库自检失败: {e}"
                _heartbeat_log.error(f"心跳自检异常: {note}")

            store.save_heartbeat(alive, note)

            # 超时任务预警：running 状态超出 任务超时+5分钟 视为疑似卡死
            try:
                for r in store.overdue_runs():
                    _heartbeat_log.warning(
                        f"运行 #{r['id']}「{r['task_name']}」已执行超 "
                        f"{r['timeout_min']} 分钟仍未结束，疑似卡死，请检查")
            except Exception as e:
                _heartbeat_log.warning(f"超时任务巡检失败: {e}")

            msg = (f"心跳正常 | 运行中任务 {running_count()}/{config.WORKER_THREADS} | "
                   f"调度作业 {len(_scheduler.get_jobs()) if _scheduler else 0} 个")
            if counter % config.HEARTBEAT_LOG_EVERY == 0:
                _heartbeat_log.info(msg)
            else:
                _heartbeat_log.debug(msg)
        except Exception as e:
            # 心跳自身兜底：任何异常都不允许线程退出
            _heartbeat_log.error(f"心跳线程异常（已忽略，继续）: {e}\n{traceback.format_exc(limit=3)}")
        _hb_stop.wait(config.HEARTBEAT_INTERVAL)
    _heartbeat_log.info("心跳线程已停止")


def start():
    global _scheduler, _heartbeat_thread, _started_at
    if _scheduler is not None:
        return
    _started_at = time.time()
    store.save_settings({"server_started_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    store.reset_stale_running()
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.start()
    sync_jobs()
    _hb_stop.clear()
    _heartbeat_thread = threading.Thread(target=_heartbeat_loop, name="heartbeat", daemon=True)
    _heartbeat_thread.start()
    _log.info(f"调度器已启动（工作线程 {config.WORKER_THREADS}）")


def shutdown():
    global _scheduler, _heartbeat_thread
    _log.info("调度器正在停止...")
    _hb_stop.set()
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        _heartbeat_thread.join(timeout=3)
        _heartbeat_thread = None
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
