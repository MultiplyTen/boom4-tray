# BOOM 4 Tray v0.1.0-alpha

首个公开实验版：在 Windows 托盘中直接控制两台 BOOM 4。

- 开启／结束 PartyUp，选择主、副音箱。
- 主音箱状态、电量、音量读取；音量调节与组队音量同步指令。
- 完整声音同播、主左／副右、交换左右三种模式。
- 内置八秒左右声道测试音，关闭窗口后可从托盘继续控制。
- 中文使用说明、英文快速指南、可重建源码与第三方许可。

**实测范围：**2026 年 10 月 31 日，一对 BOOM 4 在 Windows 11 上的 PartyUp 播放、左右分离和交换左右均通过用户试听。其他固件、适配器和型号未验证；没有测量同步误差或音视频延迟。

下载 `BOOM4-Tray-Windows-x64.zip`，完整解压后运行 `BOOM4-Tray.exe`，保留旁边的 `_internal` 文件夹。两台需分别与 Windows 配对，声音输出选择主音箱；首次测试请先降低音量。无需 Python、管理员权限或服务安装。Alpha 成品未签名。

完整使用步骤见仓库根目录 README；英文快速指南见 `docs/README.en.md`，验证记录见 `docs/TESTING.md`。

---

First public alpha of a Windows tray controller for two BOOM 4 speakers: direct PartyUp start/stop, main-speaker battery and volume, group volume sync, mirrored/stereo/swapped channels, and an included channel-isolation test.

**Tested scope:** one BOOM 4 pair on Windows 11, with PartyUp playback and both stereo orientations confirmed by listening. Other models, firmware and adapters remain unverified. This is an independent project, unaffiliated with Logitech or Ultimate Ears.

Download `BOOM4-Tray-Windows-x64.zip`, extract the whole archive, and run `BOOM4-Tray.exe` beside `_internal`. Pair both speakers separately, use the main speaker as Windows output, and start with low volume. No Python or administrator rights are needed; the alpha executable is unsigned.
