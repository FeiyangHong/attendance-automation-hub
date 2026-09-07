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
.\migrate_from_legacy.ps1 -LegacyPath E:\PhoneRemote\feishu_dryrun -IncludeLogs
```

配置网页登录密码和手机，但暂不允许真实打卡：

```powershell
.\configure_remote.ps1 -DeviceUdid 你的ADB序列号 -TailscaleUser 你的登录邮箱 -EnableDevice
```

本机试运行 Web：

```powershell
.\run_remote_service.ps1
```

浏览器打开 `http://127.0.0.1:8765`。确认无误后参照 [远程控制安装与使用](REMOTE_CONTROL_README.md) 配置 Tailscale；最终切换步骤见 [迁移简明指南](README_MIGRATION_QUICK.md)。

## 安全边界

- 服务只监听 `127.0.0.1`，不向局域网或公网直接开放。
- Tailscale Serve 提供私网 HTTPS；可再限定允许的 Tailscale 登录邮箱。
- 密码使用 scrypt 加盐哈希，登录会话为 HttpOnly/SameSite Cookie，所有写操作校验 CSRF。
- 真实打卡需网页二次确认，后台串行排队，并由跨入口全局设备锁防止并发。
- 重复请求有幂等保护；任务可取消、会超时释放，并保留审计与独立任务日志。
- 23:55 后的下班计划被禁止，避免跨日误记。
- 安全测试不会主动点击，但飞书“极速打卡”可能在应用被打开时自行产生真实记录。

## 常用入口

- 桌面面板：双击 `launch_control_panel.vbs`
- 环境诊断：`.\diagnose_environment.ps1`
- Web 服务：`.\run_remote_service.ps1`
- 测试：`.\.venv\Scripts\python.exe -m pytest`
- 紧急回滚：`.\rollback_to_legacy.ps1 -ConfirmRollback ROLLBACK`

运行数据、密码配置、截图、日志和 `.venv` 均被 Git/发布包排除。
