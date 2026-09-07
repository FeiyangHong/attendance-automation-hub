# 飞书自动化远程控制实施计划

版本：1.0  
日期：2026-09-07

## 1. 目标

建立一个可独立运行并最终完整取代 `feishu_dryrun` 的后继仓库；它保留桌面控制面板和全部本地自动化能力，并新增适配电脑与安卓手机浏览器的远程控制页面，用于：

- 查看电脑、手机、ADB、Appium和定时任务状态。
- 远程立即执行上班打卡、下班打卡或更新打卡。
- 设置和修改单日上下班计划、工作日及节假日。
- 查看实际打卡历史、执行日志、失败截图和 XML。
- 查看任务实时进度并停止尚未完成的任务。
- 一键启动远程手机画面，必要时人工操作手机。

完成和明确切换前，旧仓库及旧 Windows 任务不被修改；远程网络不可用时，后继仓库的每日自动打卡仍在本机独立运行。

## 2. 推荐架构

```text
远端电脑或安卓手机
        │ 浏览器 / RustDesk
        ▼
Tailscale 私有网络（HTTPS）
        │
        ▼
本机 Web 控制服务（127.0.0.1）
        │
        ├─ 状态与任务 API
        ├─ 单一任务队列与全局互斥锁
        ├─ 日期计划、历史、日志和诊断文件
        │
        ├─ run_random.ps1
        └─ run_clock_out.ps1
                │
                ▼
        Appium（仅本机）→ ADB → 安卓手机
```

技术选型：

- 后端：Python、FastAPI、Uvicorn。
- 页面：响应式 HTML/CSS/JavaScript，优先适配手机触控。
- 私有网络：Tailscale Serve，只允许受信任账户和设备访问。
- 数据：继续使用 `attendance_history.db`；远程任务和审计记录使用 SQLite。
- 手机画面：第一版使用 RustDesk远程电脑并操作scrcpy，不直接开发公网视频流。

官方参考：[Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve)、[Tailscale访问控制](https://tailscale.com/docs/features/access-control)、[FastAPI部署](https://fastapi.tiangolo.com/deployment/)、[RustDesk](https://rustdesk.com/docs)。

## 3. 必须遵守的安全边界

- 不向公网开放 Appium `4723`、ADB `5037` 或 Web 服务端口。
- Web 服务只监听 `127.0.0.1`，由 Tailscale 提供私有 HTTPS 入口。
- 仅允许经过授权的 Tailscale账户和设备访问。
- Web层仍需登录验证、会话超时、CSRF防护和操作审计。
- 不提供任意命令、任意路径读取或任意文件下载接口。
- 打卡按钮必须二次确认，并设置重复请求冷却时间。
- 密码和令牌通过环境变量或本机密钥文件提供，不写进代码、日志或迁移压缩包。
- Appium和ADB始终由本机执行，远端只能调用经过白名单限制的业务操作。

## 4. 功能范围

### 4.1 首页状态

- 远程服务运行状态。
- ADB设备在线和授权状态。
- Appium服务状态。
- 每日上班任务是否启用、下次系统触发时间。
- 下次实际打卡日期和本次计划时间。
- 今日工作日判定与原因。
- 当前正在执行的任务和最近一次结果。
- 最近上下班实际打卡记录。

### 4.2 远程操作

- 立即上班打卡。
- 立即下班/更新打卡。
- 安全测试，禁止主动点击打卡按钮。
- 停止当前任务。
- 启用、停用和重新安装每日任务。
- 刷新状态。

每次请求返回独立任务编号，页面按以下状态更新：

```text
已接收 → 排队中 → 正在连接 → 正在识别 → 成功 / 失败 / 已取消
```

### 4.3 日历与计划

- 月历显示执行日、休息日、调休日和人工覆盖状态。
- 设置临时执行或临时跳过。
- 设置某日精确上班与下班时间。
- 显示该日实际打卡历史。
- 联网同步国务院年度放假安排。
- 阻止或强提醒过晚的跨日风险计划。

### 4.4 日志与故障诊断

- 查看按月、按日保存的日志。
- 查看任务关键步骤，不直接暴露无限制文件浏览器。
- 查看失败截图和 XML。
- 下载指定任务的诊断包。
- 显示明确的失败分类：设备断开、Appium失败、导航失败、状态识别失败、点击失败、确认失败、超时或跨日终止。

### 4.5 手机画面

第一版流程：

1. Web页面点击“启动手机画面”。
2. 本机启动scrcpy。
3. 页面提示使用RustDesk连接打卡电脑。
4. 用户从另一台电脑或安卓手机人工操作scrcpy窗口。

浏览器内嵌实时手机画面作为后续可选功能，不纳入第一版。

## 5. 任务并发与防误操作设计

新增统一的设备任务队列，桌面界面、远程页面、每日任务和单日计划都遵守同一把全局锁。

- 同一时间只运行一个Appium流程。
- 同一操作的快速重复点击只生成一个任务。
- 定时任务执行期间，远程请求可选择排队或取消。
- 每日任务与单日精确任务不得在同一时刻分别启动。
- 上班页面显示已打卡或下班按钮时，记录已有时间并正常结束。
- 下班流程自动选择“下班打卡”或“更新打卡”，并处理二次确认弹窗。
- 任务进入下一自然日后，不再继续更新前一天打卡。
- 任务超时后终止对应进程，释放锁并记录失败原因。
- 每个任务携带唯一ID，保证重复提交不会造成重复打卡。

“单日精确上班计划”由每日任务统一读取和等待；一次性任务仅负责提前唤醒与异常补偿，并与每日任务共享调度锁，因此不会在同秒启动两个流程。

## 6. 计划新增文件

```text
remote_server.py                 FastAPI入口
remote_jobs.py                   队列、全局锁、进程和状态管理
remote_auth.py                   登录、会话和权限验证
remote_models.py                 API数据模型
remote_store.py                  任务与审计SQLite访问
web\templates\                  页面模板
web\static\                     CSS、JavaScript和图标
config\remote_config.example.json
install_remote_service.ps1       安装随系统启动的远程服务
uninstall_remote_service.ps1     停用远程服务
run_remote_service.ps1           启动及日志封装
REMOTE_CONTROL_README.md         安装和使用说明
```

现有文件尽量继续复用：

```text
04_feishu_flow.py
run_random.ps1
run_clock_out.ps1
sync_daily_plan.ps1
daily_plans.py
attendance_history.py
holiday_sync.py
feishu_control_panel.pyw
```

桌面控制面板不会被删除。后续可逐步让桌面界面和Web界面调用同一服务层，减少重复逻辑。

## 7. API初步设计

```text
POST   /api/login
POST   /api/logout
GET    /api/status
GET    /api/jobs
GET    /api/jobs/{job_id}
POST   /api/jobs/clock-in
POST   /api/jobs/clock-out
POST   /api/jobs/dry-run
POST   /api/jobs/{job_id}/cancel
GET    /api/calendar/{year}/{month}
PUT    /api/calendar/{date}
PUT    /api/plans/{date}
DELETE /api/plans/{date}
GET    /api/history/{year}/{month}
GET    /api/logs/{date}
GET    /api/artifacts/{job_id}
POST   /api/scrcpy/start
POST   /api/tasks/enable
POST   /api/tasks/disable
```

所有修改类接口都必须验证登录、CSRF令牌、参数范围和任务幂等键。

## 8. 分阶段实施

### 第一阶段：安全远程控制核心

- 建立FastAPI服务和响应式页面框架。
- 配置登录、会话和Tailscale访问。
- 显示设备、Appium和任务状态。
- 接入立即上班、立即下班/更新和安全测试。
- 建立任务编号、实时状态和基础日志。
- 安装服务随Windows启动。

完成标准：关闭桌面控制面板后，远端电脑和安卓手机仍可安全执行核心操作。

### 第二阶段：队列与稳定性

- 建立跨进程全局互斥锁。
- 统一远程、桌面和系统定时任务入口。
- 防止重复点击和同秒任务冲突。
- 增加取消、超时、跨日保护和失败分类。
- 修正精确上班计划与每日任务的并发问题。

完成标准：任何入口同时发起操作时，最多只有一个Appium流程运行。

### 第三阶段：管理功能

- 移植月历、工作日覆盖和单日计划编辑。
- 显示历史实际打卡时间。
- 提供日志、截图和XML查看。
- 增加诊断包下载和失败提示。

完成标准：日常配置与排查可以完全通过手机浏览器完成。

### 第四阶段：远程手机操作

- Web页面控制本机scrcpy启动和关闭。
- 接入RustDesk使用说明或快捷入口。
- 评估是否需要浏览器内嵌视频与控制。

完成标准：远端安卓手机能够进入电脑并人工操作手机画面。

## 9. 验收测试

- 桌面控制面板关闭时，远程页面仍可工作。
- Appium未启动时，任务能够自动启动并等待就绪。
- 手机断开、未授权或锁屏异常时，页面给出明确错误。
- 连续点击同一按钮不会重复打卡。
- 每日任务与远程请求同时发生时不会并发操作手机。
- 极速打卡已经完成时，程序正确读取现有记录并退出。
- “更新打卡”能够完成二次确认。
- 任务跨过零点时停止，不操作第二天页面。
- Tailscale断开后，Web页面无法访问，但本地每日任务仍可运行。
- 未登录、伪造请求和非法文件路径均被拒绝。
- Windows重启后，远程服务和原有每日任务能够恢复。
- 安卓手机浏览器上的主要按钮、月历和日志页面可正常使用。

## 10. 上线与回退

上线前备份：

```text
config\
data\attendance_history.db
Windows中的Feishu相关定时任务配置
```

上线顺序：

1. 在本机测试Web服务，不接入Tailscale。
2. 接入Tailscale，仅开放状态查询。
3. 开放安全测试。
4. 开放真实立即打卡。
5. 开放计划与任务管理。
6. 完成一周并行观察后，再确认稳定版本。

回退时停用远程服务即可；原桌面控制面板和本地定时任务继续保留，不依赖远程服务运行。

## 11. 不纳入第一版的内容

- 将Appium或ADB直接暴露到公网。
- 无需登录即可访问的公开控制页面。
- 任意PowerShell命令执行入口。
- 浏览器内完整复刻scrcpy低延迟视频控制。
- 多电脑同时控制多部手机或多飞书账号。

这些能力如以后确有需要，应单独设计权限、设备隔离和审计模型。
