# -*- coding: utf-8 -*-
"""
服务端日志统一配置
————————————
- 控制台输出 + logs/server.log 按天轮转（保留 14 天），UTF-8 编码
- 各模块通过 get_logger(name) 获取独立命名 logger
"""
import logging
import os
import threading

import config

_configured = False
_lock = threading.Lock()

_FMT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging():
    """初始化根日志：控制台 + 按天轮转文件。可在进程内安全重复调用。"""
    global _configured
    with _lock:
        if _configured:
            return
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.handlers.clear()

        fmt = logging.Formatter(_FMT, _DATEFMT)

        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

        try:
            from logging.handlers import TimedRotatingFileHandler
            os.makedirs(config.LOG_DIR, exist_ok=True)
            file_handler = TimedRotatingFileHandler(
                os.path.join(config.LOG_DIR, "server.log"),
                when="midnight", backupCount=config.LOG_RETENTION_DAYS, encoding="utf-8",
            )
            file_handler.suffix = "%Y%m%d.log"
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
        except Exception as e:  # 文件日志失败不应阻断服务启动
            root.warning(f"文件日志初始化失败，仅使用控制台日志: {e}")

        # 降低第三方库噪音
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        logging.getLogger("apscheduler").setLevel(logging.WARNING)

        _configured = True
        logging.getLogger("server").info(
            f"日志系统初始化完成（文件: {os.path.join(config.LOG_DIR, 'server.log')}，按天轮转，保留 {config.LOG_RETENTION_DAYS} 天）"
        )


def get_logger(name):
    return logging.getLogger(name)
