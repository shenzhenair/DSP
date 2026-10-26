# -*- coding: utf-8 -*-
"""全局路径与运行参数配置（示例文件）
使用方法：复制本文件为 config.py，然后填入实际配置值。
也可通过环境变量注入敏感配置，避免硬编码。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")
LOG_DIR = os.path.join(BASE_DIR, "logs")

DB_PATH = os.path.join(DATA_DIR, "scheduler.db")
DEMO_DB_PATH = os.path.join(DATA_DIR, "demo.db")
OUTPUT_DIR = os.path.join(DATA_DIR, "outputs")
TEMPLATE_DIR = os.path.join(DATA_DIR, "templates")

for _d in (DATA_DIR, LOG_DIR, OUTPUT_DIR, TEMPLATE_DIR):
    os.makedirs(_d, exist_ok=True)

# Web 服务
# 局域网部署：将 SCHED_HOST 设为 0.0.0.0（或直接改下行默认值），同事即可通过 http://<本机IP>:8600 访问
WEB_HOST = os.environ.get("SCHED_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("SCHED_PORT", "8600"))

# 执行引擎
WORKER_THREADS = 4          # 并发执行任务数
LOG_MAX_CHARS = 200000      # 单条运行记录日志最大保留字符
LOG_RETENTION_DAYS = 14     # server.log 轮转保留天数

# 心跳（避免服务中断：心跳线程定期自检；前端定期探测 /api/heartbeat）
HEARTBEAT_INTERVAL = 30     # 服务端心跳自检间隔（秒）
HEARTBEAT_LOG_EVERY = 10    # 每 N 次心跳写一条 INFO 日志（其余为 DEBUG）
FRONTEND_HEARTBEAT_INTERVAL = 15  # 前端探测 /api/heartbeat 间隔（秒）

# 飞书扫码登录（OAuth2）
# 1) 在飞书开放平台创建「企业自建应用」，开通"通过 code 获取用户信息"相关权限并发布可用范围
# 2) 在应用的「安全设置 → 重定向 URL」中登记回跳地址（局域网部署时登记 http://<本机局域网IP>:8600/api/auth/feishu/callback，
#    若会通过多种地址访问则逐条登记；默认留空 = 按浏览器实际访问地址自动生成）
# 3) 将 App ID / App Secret 填入下方（或用同名环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET 注入）
# 两项任一为空 = 登录功能未启用，页面不做登录拦截
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_REDIRECT_URI = os.environ.get("FEISHU_REDIRECT_URI", "")  # 留空 = 自动按访问地址生成
SESSION_TTL_HOURS = 24      # 登录会话有效期（小时）
SESSION_COOKIE = "dsp_session"

# 默认管理员：登录时用户名匹配者自动获得 admin 角色（权限管理页可再调整其他用户）
ADMIN_NAMES = {"admin"}
