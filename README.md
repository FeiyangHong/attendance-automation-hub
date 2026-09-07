# Attendance Automation Hub

Windows + Android attendance automation with a desktop control panel, local scheduling, holiday calendar, history, diagnostics, and a secure remote Web interface.

This repository is the isolated successor to `feishu_dryrun`. It is intended to become a complete standalone replacement, not an add-on to the old installation.

## Development safety

The repository starts with both device access and real attendance actions disabled. Until `config/app_config.json` is created explicitly, its automation flow refuses to connect to a phone.

```json
{
  "device_udid": "your-adb-serial",
  "feishu_package": "com.ss.android.lark",
  "device_access_enabled": false,
  "real_actions_enabled": false
}
```

During development:

- The existing `E:\PhoneRemote\feishu_dryrun` installation remains production.
- No Windows task points to this repository.
- No live-phone test is run without an agreed safe test window.
- Final cutover requires an explicit migration and rollback check.

See [REMOTE_CONTROL_IMPLEMENTATION_PLAN.md](REMOTE_CONTROL_IMPLEMENTATION_PLAN.md) for the implementation and acceptance plan. Full setup documentation will be updated as the standalone replacement is completed.
