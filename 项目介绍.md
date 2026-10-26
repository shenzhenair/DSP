# DSP 数据调度平台 - 项目交接文档

## 一、项目概述

**项目名称**：DSP（Data Scheduler Platform）数据查询调度管理平台

**项目定位**：一个面向企业内部的数据报表自动化调度平台，用于替代传统的手工取数和 Excel 报表制作流程。平台支持定时执行 SQL 查询、读取 Excel 数据源、填充 Excel 模板、Python 自定义处理，并通过飞书通知执行结果。

**典型应用场景**：
- 经营监控日报自动生成（从 Oracle 数据库取数 + Excel 模板填充）
- 多数据源整合报表
- 定时数据导出与分发

---

## 二、业务功能描述

### 2.1 核心业务流程

每个任务按以下 6 个步骤顺序执行：

```
1. 前置文件检查 → 2. Excel 数据源读取 → 3. SQL 步骤执行
     → 4. Excel 模板写入 → 5. 公式重算 → 6. Python 自定义处理 → 飞书通知
```

| 步骤 | 说明 |
|------|------|
| 前置文件检查 | 检查依赖的源文件是否存在，缺失则按配置间隔重试，可配置每轮飞书提醒 |
| Excel 数据源读取 | 从指定 Excel 文件按区域（Sheet + 起止单元格）读取数据到变量 |
| SQL 执行步骤 | 按顺序执行多段 SQL，支持分号分隔，DDL/DML 自动提交，结果存入变量 |
| Excel 模板写入 | 打开 Excel 模板，将变量数据粘贴到指定 Sheet/单元格，另存为输出文件 |
| 公式重算 | 可选：调用本机 Excel（COM 组件）全量重算公式 |
| Python 自定义 | 可选：执行自定义 Python 脚本，可用 to_df('变量名') 转 DataFrame |

### 2.2 功能模块

#### 2.2.1 执行总览（首页）
- 今日执行总数、成功数、失败数、成功率 KPI 展示
- 任务列表（名称、分组、调度周期、上次执行、耗时、状态、近 7 次记录）
- 支持按状态 Tab 筛选（全部/运行中/成功/失败/已停用/未执行）
- 支持关键词搜索（任务名/负责人/分组）
- 支持手动运行、查看日志、启停任务

#### 2.2.2 任务控制台（任务编辑）
- **基础信息**：任务名称、分组、负责人、调度周期（工作日/每日/每周/每月/每季度/每小时/自定义 Cron）、超时时间、失败重试次数
- **前置文件检查**：配置多个依赖文件，支持日期占位符，可设置重试间隔、最大重试次数、每轮飞书提醒
- **SQL 执行步骤**：
  - 支持多步骤顺序执行
  - 支持内联 SQL 或引用服务器 SQL 文件
  - 支持从本地 .sql 文件导入
  - 支持多段 SQL（分号分隔），指定取第几段结果
  - 结果存入变量供后续使用
- **Excel 数据源读取**：从 Excel 文件按区域读取到变量
- **Excel 模板写入**：
  - 支持多个模板顺序生成
  - 支持"依赖读取"（从已生成的产物文件取数，实现多模板级联）
  - 支持单变量写入或多变量横向合并
  - 可选公式重算
- **Python 自定义处理**：可编写 Python 脚本处理数据
- **输出与通知**：配置输出目录、飞书 Webhook、成功/失败通知开关、消息模板

#### 2.2.3 运行监控
- 任务运行记录列表
- 实时日志查看
- 运行状态（运行中/成功/失败）
- 产出文件列表

#### 2.2.4 数据源管理
- 支持 Oracle（oracledb，支持 Thick 模式）
- 支持 SQLite（本地文件库）
- 支持 MySQL（pymysql）
- 数据源连接测试
- 创建人权限控制

#### 2.2.5 用户与权限管理
- 飞书扫码登录（OAuth2）
- 角色体系：admin（管理员）/ senior（高级用户）/ member（普通成员）/ pending（待审批）
- 默认管理员：配置文件中 ADMIN_NAMES 指定的用户自动获得 admin 角色
- 权限规则：
  - admin：全部权限
  - senior：可操作全部任务和数据源（编辑/运行/启停），但无用户管理权限
  - member：只能操作自己创建的任务和数据源
  - pending：无访问权限

#### 2.2.6 操作审计
- 自动记录所有写操作（新建/修改/删除任务、数据源、设置等）
- 记录操作人、操作时间、IP、操作结果
- 支持按操作人筛选
- 分页查询

#### 2.2.7 系统设置
- 飞书通知全局配置（Webhook、总开关、成功/失败默认通知）
- Oracle 客户端全局默认配置（Thick 模式、Instant Client 目录）
- 服务状态与心跳信息展示

---

## 三、技术架构

### 3.1 技术栈

| 层级 | 技术选型 |
|------|----------|
| 后端框架 | Flask 3.0+ |
| 任务调度 | APScheduler 3.10+（BackgroundScheduler + CronTrigger） |
| 数据库 | SQLite（元数据存储）+ Oracle / MySQL / SQLite（业务数据源） |
| Excel 处理 | openpyxl 3.1+（读写）+ pywin32（可选，公式重算） |
| 前端 | 原生 HTML + CSS + 原生 JavaScript（无构建工具） |
| 通知 | 飞书群机器人 Webhook |
| 登录认证 | 飞书 OAuth2 扫码登录 |
| 日志 | Python logging + TimedRotatingFileHandler（按天轮转） |

### 3.2 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        前端（static/）                       │
│  index.html  console.html  monitor.html  datasources.html  │
│  users.html  audit.html  settings.html  mytasks.html       │
│  js/common.js  js/*.js  css/app.css                        │
└────────────────────────────┬────────────────────────────────┘
                             │ REST API (/api/*)
┌────────────────────────────▼────────────────────────────────┐
│                      Flask Web 服务 (app.py)                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  认证中间件 / 请求日志 / 全局错误兜底 / 审计 Hook     │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────┐  ┌────────────┐  ┌──────────────────────┐  │
│  │ 任务 CRUD   │  │ 数据源 CRUD │  │ 用户/权限/审计/设置  │  │
│  └────────────┘  └────────────┘  └──────────────────────┘  │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                    调度层 (scheduler.py)                    │
│  APScheduler 定时触发 + ThreadPoolExecutor 线程池执行       │
│  同一任务禁止并发 + 心跳守护线程 + 超时任务巡检              │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                    执行引擎 (engine.py)                      │
│  前置文件检查 → Excel读取 → SQL执行 → 模板写入 → 重算 → Python
│  日期变量渲染 / 飞书通知                                      │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│                   存储层 (store.py)                          │
│  SQLite (scheduler.db)                                      │
│  表：datasources / tasks / runs / settings / sessions /     │
│       users / audit_logs                                    │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 核心数据模型

#### 任务表 (tasks)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| name | TEXT | 任务名称 |
| grp | TEXT | 任务分组 |
| owner | TEXT | 负责人 |
| cron | TEXT | Cron 表达式 |
| timeout_min | INTEGER | 超时时间（分钟） |
| retry_times | INTEGER | 失败重试次数 |
| enabled | INTEGER | 是否启用（1/0） |
| config | TEXT | JSON 配置（步骤、模板、通知等） |
| last_run_at | TEXT | 上次运行时间 |
| last_status | TEXT | 上次状态（success/failed） |
| last_duration | REAL | 上次耗时（秒） |
| created_by | TEXT | 创建人 open_id |
| created_by_name | TEXT | 创建人姓名 |

#### 运行记录表 (runs)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| task_id | INTEGER | 任务 ID（外键） |
| task_name | TEXT | 任务名称（冗余） |
| trigger | TEXT | 触发方式（auto/manual） |
| status | TEXT | 状态（running/success/failed） |
| start_at | TEXT | 开始时间 |
| end_at | TEXT | 结束时间 |
| duration_sec | REAL | 耗时（秒） |
| log | TEXT | 运行日志 |
| outputs | TEXT | JSON 数组，产出文件路径 |
| error | TEXT | 错误信息 |

#### 数据源表 (datasources)
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| name | TEXT | 数据源名称（唯一） |
| db_type | TEXT | 类型（oracle/sqlite/mysql） |
| config | TEXT | JSON 连接配置 |
| remark | TEXT | 备注 |
| created_by / created_by_name | TEXT | 创建人 |

#### 用户表 (users)
| 字段 | 类型 | 说明 |
|------|------|------|
| open_id | TEXT | 飞书 open_id（主键） |
| name | TEXT | 姓名 |
| role | TEXT | 角色（admin/senior/member/pending） |
| email / mobile / avatar_url | TEXT | 用户信息 |
| last_login_at / last_login_ip | TEXT | 登录信息 |
| login_count | INTEGER | 登录次数 |

---

## 四、目录结构清单

```
D:\DSP\
├── app.py                  # Flask Web 服务入口，REST API + 静态页面
├── config.py               # 全局配置（路径、端口、线程数、飞书登录等）
├── engine.py               # 任务执行引擎（6 步执行流程核心）
├── scheduler.py            # 调度层（APScheduler + 线程池 + 心跳守护）
├── store.py                # SQLite 存储层（所有数据持久化）
├── notifier.py             # 飞书通知发送
├── logutil.py              # 日志工具（按天轮转 + 多模块 logger）
├── requirements.txt        # Python 依赖
├── 1.bat                   # 启动脚本（打开 cmd）
│
├── data/                   # 数据目录
│   ├── scheduler.db        # 主数据库（SQLite）
│   ├── scheduler.db-shm    # SQLite WAL 模式共享内存文件
│   ├── scheduler.db-wal    # SQLite WAL 模式日志文件
│   ├── demo.db             # 演示用 SQLite 数据库
│   ├── templates/          # Excel 模板目录
│   │   └── 演示_销售日报模板.xlsx
│   └── outputs/            # 任务输出目录（运行时生成）
│
├── logs/                   # 日志目录
│   └── server.log          # 服务端日志（按天轮转，保留 14 天）
│
└── static/                 # 前端静态文件
    ├── css/
    │   └── app.css         # 全局样式
    ├── js/
    │   ├── common.js       # 公共函数（侧边栏、API 封装、工具函数）
    │   ├── audit.js        # 审计日志页逻辑
    │   ├── mytasks.js      # 我的任务页逻辑
    │   └── users.js        # 用户管理页逻辑
    ├── index.html          # 执行总览页
    ├── console.html        # 任务编辑页
    ├── monitor.html        # 运行监控页
    ├── datasources.html    # 数据源管理页
    ├── users.html          # 用户管理页
    ├── audit.html          # 操作审计页
    ├── settings.html       # 系统设置页
    └── mytasks.html        # 我的任务页
```

---

## 五、核心模块详解

### 5.1 app.py - Web 服务入口

**主要职责**：
- 启动 Flask Web 服务，提供 REST API 和静态页面
- 请求日志记录、全局异常兜底
- 心跳接口 /api/heartbeat
- 飞书 OAuth2 登录认证（登录守卫、回调、登出）
- 人为操作审计（after_request Hook 自动记录）
- 任务 / 数据源 / 运行记录 / 用户 / 设置 / 飞书测试 等 API

**关键 API 列表**：

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/heartbeat | GET | 心跳探测，返回服务状态 |
| /api/auth/me | GET | 当前登录用户信息 |
| /api/auth/logout | POST | 退出登录 |
| /api/auth/feishu/callback | GET | 飞书登录回调 |
| /api/overview | GET | 执行总览数据 |
| /api/tasks | GET/POST | 任务列表 / 新建任务 |
| /api/tasks/<id> | GET/PUT/DELETE | 任务详情 / 修改 / 删除 |
| /api/tasks/<id>/enabled | POST | 启停任务 |
| /api/tasks/<id>/run | POST | 手动运行任务 |
| /api/runs | GET | 运行记录列表 |
| /api/runs/<id> | GET | 运行记录详情 |
| /api/runs/latest/<tid> | GET | 任务最新运行记录 |
| /api/datasources | GET/POST | 数据源列表 / 新建 |
| /api/datasources/<id> | PUT/DELETE | 修改 / 删除数据源 |
| /api/datasources/<id>/test | POST | 测试数据源连接 |
| /api/users | GET | 用户列表 |
| /api/users/role | POST | 修改用户角色 |
| /api/audit | GET | 审计日志列表 |
| /api/settings | GET/PUT | 系统设置读取 / 保存 |
| /api/feishu/test | POST | 飞书通知测试 |

### 5.2 engine.py - 执行引擎

**核心函数**：execute_task(task, trigger, run_id)

执行 6 步流程：
1. **前置文件检查** wait_for_files() - 按间隔重试，可通知
2. **Excel 数据源读取** read_excel_range() - openpyxl 只读模式
3. **SQL 执行步骤** run_sql() - 按 db_type 分发到 Oracle/SQLite/MySQL 执行器
4. **模板写入** paste_data() - 打开模板、粘贴数据、另存
5. **公式重算** recalculate_excel() - win32com 调用 Excel（可选）
6. **Python 自定义** run_python_step() - exec 执行用户代码

**日期变量**（date_tokens()）：
| 变量 | 示例 | 说明 |
|------|------|------|
| {{yyyy}} | 2026 | 当年 |
| {{MM}} | 10 | 当月 |
| {{dd}} | 26 | 当日 |
| {{yyyyMMdd}} | 20261026 | 年月日 8 位 |
| {{mmdd}} | 1026 | 月日 4 位 |
| {{t1_yyyyMMdd}} | 20261025 | 前一天 8 位 |
| {{t1_6bit}} | 261025 | 前一天 6 位（年月日后两位） |
| {{biz_date}} | 2026-10-26 | 业务日期 |

支持 {{var}} 和 ${var} 两种占位符写法。

### 5.3 scheduler.py - 调度层

**核心组件**：
- BackgroundScheduler - APScheduler 后台调度器
- ThreadPoolExecutor - 工作线程池（默认 4 线程）
- _running 集合 - 运行中任务 ID，防止同一任务并发
- 心跳守护线程 - 每 30 秒自检数据库、巡检超时任务

**关键机制**：
- **同步作业** sync_jobs() - 根据数据库中 enabled 的任务重建定时作业
- **防并发** - 同一任务上一次未结束时，新触发自动跳过
- **超时巡检** - 运行超过「任务超时 + 5 分钟」视为疑似卡死，日志预警
- **服务重启** - 重置残留的 running 状态为 failed

### 5.4 store.py - 存储层

**数据库**：SQLite，WAL 模式，外键启用，busy_timeout=15s

**表结构**（详见 3.3 节）：
- datasources - 数据源
- tasks - 任务
- runs - 运行记录
- settings - 系统设置（key-value）
- sessions - 登录会话
- users - 用户
- audit_logs - 操作审计

**迁移机制**：启动时动态检查列是否存在，缺失则 ALTER TABLE 添加，保证老库平滑升级。

### 5.5 notifier.py - 飞书通知

**发送方式**：飞书群自定义机器人 Webhook，post 富文本消息

**模板变量**：
- {{任务名}} / {{状态}} / {{执行时间}} / {{耗时}} / {{文件路径}} / {{错误}}

### 5.6 config.py - 配置

**关键配置项**：
| 配置 | 默认值 | 说明 |
|------|--------|------|
| WEB_HOST | 0.0.0.0 | Web 服务监听地址 |
| WEB_PORT | 8600 | Web 服务端口 |
| WORKER_THREADS | 4 | 并发执行任务数 |
| LOG_MAX_CHARS | 200000 | 单条运行日志最大字符数 |
| LOG_RETENTION_DAYS | 14 | server.log 保留天数 |
| HEARTBEAT_INTERVAL | 30 | 服务端心跳间隔（秒） |
| FEISHU_APP_ID | cli_aa10... | 飞书应用 ID |
| FEISHU_APP_SECRET | ... | 飞书应用密钥 |
| SESSION_TTL_HOURS | 24 | 登录会话有效期 |
| ADMIN_NAMES | {"史炎平"} | 默认管理员姓名集合 |

支持通过环境变量覆盖（SCHED_HOST、SCHED_PORT、FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_REDIRECT_URI）。

---

## 六、部署与运行

### 6.1 环境要求

- **操作系统**：Windows（推荐，Excel 公式重算需要）/ Linux（无重算功能）
- **Python**：3.8+
- **可选依赖**：
  - Microsoft Excel（用于公式重算，需安装 pywin32）
  - Oracle Instant Client（用于 Thick 模式连接老版本 Oracle）

### 6.2 安装步骤

1. **安装 Python 依赖**：
   ```bash
   cd D:\DSP
   pip install -r requirements.txt
   ```

2. **可选：安装 pywin32（Excel 公式重算）**：
   ```bash
   pip install pywin32>=306
   ```

3. **可选：安装 pymysql（MySQL 数据源）**：
   ```bash
   pip install pymysql>=1.1
   ```

4. **配置飞书登录（可选）**：
   - 在飞书开放平台创建企业自建应用
   - 开通"通过 code 获取用户信息"权限
   - 在安全设置中配置重定向 URL
   - 修改 config.py 中的 FEISHU_APP_ID 和 FEISHU_APP_SECRET
   - 两项任一为空则登录功能不启用

5. **配置默认管理员**：
   - 修改 config.py 中的 ADMIN_NAMES 集合
   - 匹配姓名的用户登录时自动获得 admin 角色

### 6.3 启动方式

**方式一：直接运行**
```bash
cd D:\DSP
python app.py
```

**方式二：双击 1.bat**（打开 cmd 窗口）

服务启动后访问：http://localhost:8600

### 6.4 局域网部署

1. 确保 config.py 中 WEB_HOST = "0.0.0.0"
2. 关闭防火墙或放行 8600 端口
3. 同事通过 http://<本机IP>:8600 访问
4. 飞书登录回调地址需在飞书开放平台登记所有访问方式

### 6.5 生产部署建议

- 使用 Windows 服务包装（如 NSSM）将程序注册为系统服务
- 配置开机自启动
- 定期备份 data/scheduler.db 数据库文件
- 监控 logs/server.log 异常

---

## 七、常见问题与排障

### 7.1 Oracle 连接问题

**问题**：连接 Oracle 报错 "DPI-1047: Cannot locate a 64-bit Oracle Client library"

**解决**：
1. 安装 Oracle Instant Client（64 位）
2. 在系统设置中启用 Thick 模式并配置客户端目录
3. 或在数据源配置中单独启用 Thick 模式

### 7.2 Excel 公式重算不生效

**问题**：模板中的公式没有自动重算

**解决**：
1. 确认安装了 pywin32 且本机有 Excel
2. 确认模板配置中"保存后重算公式"已勾选
3. 检查 Excel 是否弹出安全提示导致卡住

### 7.3 任务执行卡住

**问题**：任务长时间处于运行中状态

**排查**：
1. 查看 logs/server.log 是否有异常
2. 查看任务运行日志是否卡在某一步
3. 心跳日志中是否有"疑似卡死"预警
4. 可能原因：数据库查询慢、网络盘读写慢、Excel COM 进程残留

**处理**：
1. 重启服务（残留的 running 状态会自动标记为 failed）
2. 检查是否有 Excel 进程残留，手动结束
3. 调大任务超时时间

### 7.4 飞书通知发送失败

**问题**：飞书通知没收到

**排查**：
1. 确认系统设置中飞书通知总开关已打开
2. 确认 Webhook 地址正确
3. 在系统设置页点击"发送测试消息"验证
4. 检查飞书机器人安全设置（关键词 / IP 白名单）

### 7.5 登录失败

**问题**：飞书扫码登录后报错

**排查**：
1. 确认 FEISHU_APP_ID 和 FEISHU_APP_SECRET 正确
2. 确认飞书应用已发布且可用范围包含当前用户
3. 确认重定向 URL 已在飞书开放平台登记
4. 查看 logs/server.log 中的详细错误

### 7.6 数据库锁定

**问题**：日志中出现 "database is locked"

**原因**：SQLite 并发写入冲突，正常情况下 busy_timeout=15s 会自动重试

**处理**：
1. 偶发出现无需处理
2. 频繁出现需检查是否有大量并发任务
3. 可适当增加 WORKER_THREADS 或调整任务调度时间

---

## 八、版本历史与已知事项

### 当前版本特点
- 完整的任务调度与执行引擎
- 飞书扫码登录与权限管理
- 操作审计日志
- 心跳守护与超时预警
- 支持 Oracle / SQLite / MySQL 三种数据源
- 内置演示数据与演示任务

### 已迁移的旧脚本功能
- 日常监控日报完整流程（前置文件等待 → SQL 取数 → 多模板级联生成 → 公式重算）
- 飞书通知机制
- 多数据源支持（JCDB、YXDBNEW）

### 后续可扩展方向
- 更多数据源类型（PostgreSQL、SQL Server 等）
- 任务依赖关系（DAG 编排）
- 文件输出后自动发送邮件 / 飞书文件
- 更丰富的报表展示与数据可视化
- 任务版本管理与回滚

---

## 九、联系人

- **系统默认管理员**：史炎平
- **技术支持**：（待补充）

---

*文档生成时间：2026-10-26*
