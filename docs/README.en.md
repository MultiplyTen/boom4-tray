# BOOM 4 Tray

A small Windows tray controller for **two Ultimate Ears BOOM 4 speakers**. Start or stop PartyUp directly from your PC, adjust the main speaker's volume, and switch between mirrored audio and left/right stereo.

**v0.1.0-alpha · Windows x64 · Chinese UI**

[Download the latest portable release](https://github.com/MultiplyTen/boom4-tray/releases) · [中文说明](../README.md) · [Test record](TESTING.md)

This is an independent project, unaffiliated with Logitech or Ultimate Ears. PartyUp playback, main-left/guest-right, and swapped left/right have been confirmed by listening on **one pair of BOOM 4 speakers on Windows 11**. Other firmware, adapters and speaker combinations remain unverified.

## Download and first use

1. Download **`BOOM4-Tray-Windows-x64.zip`** from Releases. The portable build does not require Python.
2. Extract the **entire archive**. Keep `BOOM4-Tray.exe` next to its `_internal` folder, then run the EXE. No administrator rights, service installation or automatic startup entry is required. The alpha build is unsigned.
3. Turn on both speakers and pair **each speaker separately** in Windows Bluetooth settings. Only the main speaker needs an audio connection.
4. Close the mobile UE app or temporarily turn off phone Bluetooth so it releases the speaker control connection.
5. Click **刷新设备** (Refresh devices), choose **主音箱** (Main) and **副音箱** (Guest), then click **刷新状态** (Refresh status).
6. Click **开启 PartyUp** (Start PartyUp). In the Windows audio output menu, select the **same main speaker** and play music. Listen to confirm that both speakers play.

The app sends speaker control commands; Windows sends audio to the main speaker, which distributes PartyUp audio. The app does not duplicate Windows audio streams or switch your Windows output automatically.

## Controls

| Chinese label | Action |
| --- | --- |
| 开启 PartyUp / 结束 PartyUp | Start / stop the selected group. |
| 刷新状态 | Read main-speaker group state, battery and volume. |
| 应用音量 | Apply the volume slider after a successful status read. |
| 同步两台音量 | Send a group volume-sync command while PartyUp is on. |
| 声道（实验） | Experimental channel-mode tab. |
| 同播（完整声音） | Full audio on both speakers. |
| 左右立体声（主左／副右） | Main left, guest right. |
| 交换左右（主右／副左） | Main right, guest left. |
| 应用声道 | Apply the selected channel mode. |
| 播放左右测试音 / 停止测试音 | Play / stop the included channel test. |

Start PartyUp before applying a channel mode. Closing the window hides it in the tray; right-click the tray icon to reopen it or use shortcuts. **退出工具（保留播放）** exits the utility while preserving music playback. Group and channel changes may briefly interrupt audio.

## Verify stereo

Lower the volume first. Select the main speaker as Windows audio output and disable **Mono audio** in Windows accessibility audio settings.

Apply main-left/guest-right and play the built-in test: **one second of left-channel low tone, one second of silence, one second of right-channel higher tone, one second of silence; repeated once**. The test lasts eight seconds. Only the main speaker should play the first tone; only the guest should play the second. Apply swapped stereo and repeat: the two physical speakers should exchange roles.

The test is 48 kHz, 16-bit stereo PCM, with fades and a peak of about −18 dBFS. Final loudness still depends on your Windows and speaker volume. A successful command reply alone does not prove channel separation.

## Compatibility and troubleshooting

- **Verified:** one BOOM 4 pair on Windows 11, including PartyUp playback and both stereo orientations. See [the test record](TESTING.md).
- **Unverified:** Windows 10, other BOOM 4 firmware/adapters, MEGABOOM 4, mixed models and larger groups. The UI is intended for two speakers; a recognized name does not establish hardware support.
- If a speaker is missing, confirm both were paired separately and refresh devices. Custom names without `BOOM 4` or `MEGABOOM 4` may not be recognized by this version.
- If a connection times out, keep the speaker on, release the phone's Bluetooth connection, wait briefly, then refresh status. Avoid repeatedly issuing Start.
- If membership is unknown, refresh first. If needed, explicitly stop the group, wait for completion, then start it again.
- If a channel change is unconfirmed, some settings may already have taken effect. Refresh, apply full-audio mode to recover if needed, and listen again.

No firmware updates, factory reset, EQ, remote wake or audio/video latency correction are included. Synchronization error and crosstalk have not been measured.

## Privacy and source builds

No account, telemetry, cloud service or diagnostic upload is used. The selected speaker addresses stay in `%LOCALAPPDATA%\Boom4Tray\selection.json`. Do not attach that file, raw Bluetooth captures, private device names or addresses to public issues.

For source use, download the complete repository and run on Windows with Python 3.13 and Tkinter:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe boom_tray.py

# Software-only checks; no speaker commands or audio playback
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe boom_tray.py --smoke

# Build the portable folder and ZIP
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

Protocol helpers are adapted from the MIT-licensed [ue-partyup-pc](https://github.com/behemothnemoth-a11y/ue-partyup-pc), pinned to `4076ace2da564e8fcf45c4ca93a705495d4f2bf2`. See [third-party notices](../THIRD_PARTY_NOTICES.md) for dependencies and retained licenses.


## License

Original project code, icon and generated test tone are MIT-licensed. Third-party components retain their own terms; see [LICENSE](../LICENSE) and [third-party notices](../THIRD_PARTY_NOTICES.md).
