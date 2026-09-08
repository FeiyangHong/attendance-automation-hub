# Attendance Automation Hub

一套可独立运行的 Windows + Android 飞书考勤工具，包含桌面控制面板、本地计划任务、节假日日历、每日自定义计划、历史记录、诊断，以及适配电脑和安卓浏览器的私网 Web 控制页。

它是 `feishu_dryrun` 的完整后继仓库。切换前旧仓库继续工作；新仓库默认禁止连接手机和真实打卡，也不会安装或修改任何系统任务。

## 快速安装

在项目目录运行：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

从旧仓库导入日历、计划和历史（只读旧仓库，且导入后安全锁仍关闭）：

```powershell
.\scripts\migration\migrate_from_legacy.ps1 `
    -LegacyPath E:\PhoneRemote\feishu_dryrun -IncludeLogs
```

仅追加归档旧日志并导入历史时间，不改变现有配置：

```powershell
.\scripts\migration\migrate_from_legacy.ps1 `
    -LegacyPath E:\PhoneRemote\feishu_dryrun -LogsOnly
```

配置网页登录密码和手机，但暂不允许真实打卡：

```powershell
.\scripts\setup\configure_remote.ps1 `
    -DeviceUdid 你的ADB序列号 -TailscaleUser 你的登录邮箱 -EnableDevice
```

本机试运行 Web：

```powershell
.\hub.ps1 web
```

浏览器打开 `http://127.0.0.1:8765`。确认无误后参照
[远程控制安装与使用](docs/REMOTE_CONTROL.md) 配置 Tailscale；最终切换步骤见
[迁移简明指南](docs/MIGRATION.md)。

## 仓库结构

```text
src/attendance_hub/   Python应用、桌面界面和Web资源
scripts/
  runtime/            上班、下班和Web运行器
  setup/              首次配置、任务和 Tailscale 安装
  operations/         诊断、日计划同步、scrcpy 和临时任务
  migration/          迁移、切换与回滚
  release/            发布包构建
config/               示例配置及本机运行配置
docs/                 安装与迁移文档
tests/                自动测试
logs/                 按月日志及旧仓库日志归档
data/                 历史、任务和审计数据库
artifacts/            失败截图与XML
dist/                 发布压缩包
```

根目录只保留项目说明、依赖和两个公共入口。

## 安全边界

- 服务只监听 `127.0.0.1`，不向局域网或公网直接开放。
- Tailscale Serve 提供私网 HTTPS；可再限定允许的 Tailscale 登录邮箱。
- 密码使用 scrypt 加盐哈希，登录会话为 HttpOnly/SameSite Cookie，所有写操作校验 CSRF。
- 真实打卡需网页二次确认，后台串行排队，并由跨入口全局设备锁防止并发。
- 重复请求有幂等保护；任务可取消、会超时释放，并保留审计与独立任务日志。
- 23:55 后的下班计划被禁止，避免跨日误记。
- 安全测试不会主动点击，但飞书“极速打卡”可能在应用被打开时自行产生真实记录。

## 常用入口

- 桌面面板：双击 `Attendance Hub.vbs`，或运行 `.\hub.ps1 desktop`
- 环境诊断：`.\hub.ps1 diagnose`
- Web 服务：`.\hub.ps1 web`
- 立即上班：`.\hub.ps1 clock-in`
- 立即下班：`.\hub.ps1 clock-out`
- 测试：`.\.venv\Scripts\python.exe -m pytest`
- 紧急回滚：`.\scripts\migration\rollback_to_legacy.ps1 -ConfirmRollback ROLLBACK`

运行数据、密码配置、截图、日志、发布包和 `.venv` 均被 Git 排除。
