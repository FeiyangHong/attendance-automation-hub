# 远程控制安装与使用

## 网络结构

Web 服务仅监听电脑本机 `127.0.0.1:8765`。Tailscale Serve 将它以 tailnet 内的 HTTPS 地址提供给另一台电脑或安卓手机；Appium `4723` 和 ADB 不对网络开放。

## 一次性配置

1. 在打卡电脑、远程电脑/安卓手机安装 Tailscale，并登录同一网络。
2. 运行 `configure_remote.ps1` 设置网页登录密码、ADB 序列号和允许访问的 Tailscale 邮箱。
3. 管理员 PowerShell 运行 `setup_tailscale_serve.ps1`。它使用持久化的 `tailscale serve --bg`，不会启用公开的 Funnel。
4. 运行 `install_remote_service.ps1` 安装登录后自启任务。安装动作不会立即启动服务；可注销重登，或先手动运行 `run_remote_service.ps1`。

Tailscale 账户登录和新设备授权必须由你本人完成。官方文档：[Windows 安装](https://tailscale.com/docs/install/windows)、[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)。

## 页面按钮

- `立即上班打卡`：立即执行真实上班流程；若极速打卡已完成，识别现有记录后结束。
- `立即下班 / 更新`：识别“下班打卡”或“更新打卡”，更新时自动确认二次弹窗。
- `上班/下班安全测试`：识别和留存证据但不主动点击；极速打卡仍可能自行触发。
- `环境诊断`：检查本机依赖、ADB、Appium和项目文件。
- `启动手机画面`：在打卡电脑打开 scrcpy；再通过 RustDesk 连接电脑操作该窗口。
- `启用/暂停每日任务`：控制后继仓库的每日上班任务，不操作旧任务。
- `停止任务`：终止当前自动化进程树并释放队列。
- `同步国务院安排`：联网更新当前年份法定节假日和调休工作日。
- 月历日期：设置临时执行/跳过，以及当天精确上下班时间；单日计划优先于默认随机时间。
- 日志区：按日期读取日志，并查看最新失败截图/XML。

## 日常故障

- 页面打不开：先确认两端 Tailscale 在线，再检查“Attendance Automation Hub Web”任务。
- 手机离线：检查 USB/Wi-Fi ADB 授权；系统不会盲点。
- Appium 未启动：打卡运行器会自动启动并等待就绪。
- 远程网络断开：只影响网页访问，本地每日任务独立运行。
- 操作冲突：所有入口共享设备锁；请求排队或明确显示 busy，不会并发点击。
- 紧急恢复旧系统：运行 `rollback_to_legacy.ps1 -ConfirmRollback ROLLBACK`。

## 停用远程入口

```powershell
.\setup_tailscale_serve.ps1 -Disable
.\uninstall_remote_service.ps1
```

这不会删除考勤历史、计划或旧仓库。
