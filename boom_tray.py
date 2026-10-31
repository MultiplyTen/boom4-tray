"""Small, local Chinese Windows tray controller for two BOOM 4 speakers."""
from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from app_version import VERSION

from boom_backend import (ROOT, Controller, Snapshot, data_directory,
                          CHANNEL_MODES, host_channel_label, discover_speakers,
                          friendly_error, load_selection, save_selection)

TITLE = 'BOOM 4 · 托盘控制'


class App:
    def __init__(self, root, smoke=False):
        self.root = root
        self.smoke = smoke
        self.controller = Controller()
        self.events = queue.Queue()
        self.busy = False
        self.closing = False
        self.tray = None
        self.tray_ready = False
        self.speakers = []
        self.selection = load_selection()
        self.loop = asyncio.new_event_loop()
        self.worker = threading.Thread(target=self.loop.run_forever, daemon=True, name='BOOM4-control')
        self.worker.start()
        self.status_text = tk.StringVar(value='选择两台音箱，点击刷新状态。')
        self.group_text = tk.StringVar(value='未读取')
        self.battery_text = tk.StringVar(value='—')
        self.volume_text = tk.StringVar(value='—')
        self.slider_text = tk.StringVar(value='音量：请先读取')
        self.volume_value = tk.DoubleVar(value=0)
        self.host_var = tk.StringVar()
        self.guest_var = tk.StringVar()
        self.channel_var = tk.StringVar(value=next(iter(CHANNEL_MODES)))
        self.channel_text = tk.StringVar(value='主音箱声道：未读取')
        self.last_volume = None
        self.volume_host = None
        self.build_ui()
        if smoke:
            self.root.after(350, self.quit)
        else:
            self.setup_tray()
            self.refresh_devices()
        self.root.after(75, self.drain_events)

    def build_ui(self):
        root = self.root
        root.title(TITLE)
        root.geometry('570x570')
        root.minsize(550, 550)
        root.configure(bg='#F4F6FC')
        try:
            root.iconbitmap(str(ROOT / 'assets' / 'boom4.ico'))
        except tk.TclError:
            pass
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#F4F6FC')
        style.configure('TLabel', background='#F4F6FC', foreground='#23304B')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 21, 'bold'))
        style.configure('Muted.TLabel', foreground='#69758B', font=('Microsoft YaHei UI', 9))
        style.configure('TButton', padding=(12, 9))
        style.configure('Primary.TButton', background='#6558DE', foreground='white')
        style.map('Primary.TButton', background=[('active', '#5143C3'), ('disabled', '#BAB5DF')])
        box = ttk.Frame(root, padding=25)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='BOOM 4', style='Title.TLabel').pack(anchor='w')
        ttk.Label(box, text=f'双音箱 · Windows 托盘控制 · v{VERSION}', style='Muted.TLabel').pack(anchor='w', pady=(2, 18))
        picker = ttk.Frame(box)
        picker.pack(fill='x')
        ttk.Label(picker, text='主音箱  ·  电脑播放到这一台').grid(row=0, column=0, sticky='w', pady=(0, 5))
        self.host_combo = ttk.Combobox(picker, textvariable=self.host_var, state='readonly', width=32)
        self.host_combo.grid(row=1, column=0, sticky='ew', padx=(0, 9))
        self.scan_button = ttk.Button(picker, text='刷新设备', command=self.refresh_devices)
        self.scan_button.grid(row=1, column=1)
        ttk.Label(picker, text='副音箱  ·  加入 PartyUp').grid(row=2, column=0, sticky='w', pady=(12, 5))
        self.guest_combo = ttk.Combobox(picker, textvariable=self.guest_var, state='readonly', width=32)
        self.guest_combo.grid(row=3, column=0, sticky='ew', padx=(0, 9))
        self.bluetooth_button = ttk.Button(picker, text='蓝牙设置', command=lambda: os.startfile('ms-settings:bluetooth'))
        self.bluetooth_button.grid(row=3, column=1)
        picker.columnconfigure(0, weight=1)
        self.host_combo.bind('<<ComboboxSelected>>', self.selection_changed)
        self.guest_combo.bind('<<ComboboxSelected>>', self.selection_changed)
        summary = ttk.Frame(box, padding=(0, 18, 0, 14))
        summary.pack(fill='x')
        for index, (caption, value) in enumerate((('PartyUp', self.group_text), ('主音箱电量', self.battery_text), ('主音箱音量', self.volume_text))):
            card = ttk.Frame(summary)
            card.grid(row=0, column=index, sticky='w')
            ttk.Label(card, text=caption, style='Muted.TLabel').pack(anchor='w')
            ttk.Label(card, textvariable=value, font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w', pady=(5, 0))
            summary.columnconfigure(index, weight=1)
        actions = ttk.Frame(box)
        actions.pack(fill='x')
        self.start_button = ttk.Button(actions, text='开启 PartyUp', style='Primary.TButton', command=lambda: self.group('start'))
        self.stop_button = ttk.Button(actions, text='结束 PartyUp', command=lambda: self.group('stop'))
        self.read_button = ttk.Button(actions, text='刷新状态', command=self.read_status)
        for index, button in enumerate((self.start_button, self.stop_button, self.read_button)):
            button.grid(row=0, column=index, sticky='ew', padx=(0, 7 if index < 2 else 0))
            actions.columnconfigure(index, weight=1)
        sound_tabs = ttk.Notebook(box)
        sound_tabs.pack(fill='x', pady=(16, 10))
        controls = ttk.Frame(sound_tabs, padding=10)
        sound_tabs.add(controls, text='音量')
        ttk.Label(controls, textvariable=self.slider_text, style='Muted.TLabel').grid(row=0, column=0, sticky='w', columnspan=2)
        self.slider = ttk.Scale(controls, from_=0, to=31, variable=self.volume_value, command=self.slider_changed)
        self.slider.grid(row=1, column=0, sticky='ew', padx=(0, 8), pady=6)
        self.apply_button = ttk.Button(controls, text='应用音量', command=self.apply_volume)
        self.apply_button.grid(row=1, column=1)
        self.sync_button = ttk.Button(controls, text='同步两台音量', command=self.sync_volume)
        self.sync_button.grid(row=2, column=0, sticky='w', pady=(4, 0))
        controls.columnconfigure(0, weight=1)
        channels = ttk.Frame(sound_tabs, padding=10)
        sound_tabs.add(channels, text='声道（实验）')
        self.channel_combo = ttk.Combobox(channels, textvariable=self.channel_var,
                                         values=list(CHANNEL_MODES), state='readonly', width=30)
        self.channel_combo.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.channel_button = ttk.Button(channels, text='应用声道', command=self.apply_channel)
        self.channel_button.grid(row=0, column=1)
        ttk.Label(channels, textvariable=self.channel_text, style='Muted.TLabel').grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(6, 2))
        ttk.Label(channels, text='先开启 PartyUp，再应用声道。切换可能短暂停顿。',
                  style='Muted.TLabel', wraplength=490).grid(row=2, column=0, columnspan=2, sticky='w')
        audio_buttons = ttk.Frame(channels)
        audio_buttons.grid(row=3, column=0, columnspan=2, sticky='w', pady=(8, 0))
        self.test_sound_button = ttk.Button(audio_buttons, text='播放左右测试音', command=self.play_test_sound)
        self.test_sound_button.pack(side='left', padx=(0, 8))
        self.stop_sound_button = ttk.Button(audio_buttons, text='停止测试音', command=self.stop_test_sound)
        self.stop_sound_button.pack(side='left')
        channels.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(box, mode='indeterminate')
        self.progress.pack(fill='x', pady=(5, 10))
        tk.Label(box, textvariable=self.status_text, wraplength=510, height=3,
                 anchor='nw', justify='left', bg='#F4F6FC', fg='#23304B',
                 font=('Microsoft YaHei UI', 10)).pack(anchor='w', fill='x')
        ttk.Label(box, text='首次使用：两台分别与 Windows 配对，并暂时关闭手机蓝牙。\nBOOM 4 兼容性验证中；关闭窗口后仍可从托盘打开。',
                  style='Muted.TLabel', wraplength=510).pack(side='bottom', anchor='w', pady=(12, 0))
        root.protocol('WM_DELETE_WINDOW', self.hide)
        self.update_controls()
        # Size from actual widget metrics: Windows text scaling can make the
        # required height substantially larger than the nominal geometry.
        root.update_idletasks()
        width = min(root.winfo_screenwidth() - 80, max(570, root.winfo_reqwidth() + 8))
        height = min(root.winfo_screenheight() - 100, max(650, root.winfo_reqheight() + 8))
        root.geometry(f'{width}x{height}')
        root.minsize(width, height)

    def setup_tray(self):
        try:
            import pystray
            from PIL import Image
            item = pystray.MenuItem
            def send(action):
                return lambda icon, selected: self.events.put(('command', action))
            self.tray = pystray.Icon('Boom4Tray', Image.open(ROOT / 'assets' / 'boom4.ico'), 'BOOM 4 · 双音箱控制',
                pystray.Menu(item('打开控制面板', send('show'), default=True),
                             item('开启 PartyUp', send('start')),
                             item('结束 PartyUp', send('stop')),
                             item('声道（实验）', pystray.Menu(*[
                                 item(label, send('channel:' + label)) for label in CHANNEL_MODES])),
                             pystray.Menu.SEPARATOR,
                             item('音量 +', send('up')), item('音量 −', send('down')),
                             item('刷新状态', send('status')),
                             pystray.Menu.SEPARATOR, item('退出工具（保留播放）', send('quit'))))
            def ready(icon):
                icon.visible = True
                self.events.put(('tray_ready', None))
            self.tray.run_detached(ready)
        except Exception as error:
            self.status_text.set('托盘暂不可用；控制窗口仍可使用。' + friendly_error(error))

    def selected(self, require_guest=False):
        mapping = {speaker.label: speaker for speaker in self.speakers}
        host = mapping.get(self.host_var.get())
        guest = mapping.get(self.guest_var.get())
        self.controller.validate(host, guest)
        if require_guest and guest is None:
            raise ValueError('请先在 Windows 分别配对两台 BOOM 4，再选择副音箱。')
        if host and guest:
            save_selection(host, guest)
        return host, guest

    def selection_changed(self, event=None):
        self.group_text.set('未读取')
        self.channel_text.set('主音箱声道：未读取')
        self.battery_text.set('—')
        self.volume_text.set('—')
        self.slider_text.set('音量：请先读取')
        self.last_volume = None
        self.volume_host = None
        self.status_text.set('音箱选择已改变，请刷新状态。')
        self.update_controls()

    def refresh_devices(self):
        if self.busy:
            return
        async def discover():
            return await asyncio.to_thread(discover_speakers)
        self.submit('devices', discover(), '正在读取 Windows 已配对的音箱…')

    def submit(self, kind, coroutine, message):
        if self.busy or self.closing:
            coroutine.close()
            if self.busy:
                self.status_text.set('正在处理上一条操作，请稍候。')
            return
        self.busy = True
        self.status_text.set(message)
        self.progress.start(12)
        self.update_controls()
        async def bounded():
            return await asyncio.wait_for(coroutine, timeout=70)
        self.future = asyncio.run_coroutine_threadsafe(bounded(), self.loop)
        def done(future):
            try:
                self.events.put(('result', (kind, future.result(), None)))
            except Exception as error:
                self.events.put(('result', (kind, None, error)))
        self.future.add_done_callback(done)

    def action(self, kind, operation, message, require_guest=False):
        try:
            if self.busy:
                return
            host, guest = self.selected(require_guest)
            self.submit(kind, operation(host, guest), message)
        except Exception as error:
            self.status_text.set(friendly_error(error))
            self.show()

    def read_status(self):
        self.action('status', lambda host, guest: self.controller.status(host, guest), '正在读取主音箱状态…')

    def group(self, action):
        def progress(message):
            # Keep transport/model internals out of the main product flow.
            self.events.put(('progress', '正在组队，请稍候…' if action == 'start' else '正在结束组队，请稍候…'))
        self.action(action, lambda host, guest: self.controller.group(action, host, guest, progress),
                    '正在开启 PartyUp…' if action == 'start' else '正在结束 PartyUp…', require_guest=True)

    def slider_changed(self, value):
        self.slider_text.set(f'待应用音量：{round(float(value) / 31 * 100)}%')

    def apply_volume(self):
        self.action('volume', lambda host, guest: self.controller.volume(host, level=round(self.volume_value.get())), '正在调整音量…')

    def nudge_volume(self, delta):
        self.action('volume', lambda host, guest: self.controller.volume(host, delta=delta), '正在调整音量…')

    def sync_volume(self):
        self.action('sync', lambda host, guest: self.controller.sync_volume(host), '正在同步组队音量…')

    def apply_channel(self, mode=None):
        mode = mode or self.channel_var.get()
        if mode not in CHANNEL_MODES:
            return
        self.channel_var.set(mode)
        self.action('channel', lambda host, guest: self.controller.channel_mode(host, guest, mode),
                    '正在切换声道，播放可能短暂停顿…', require_guest=True)

    def play_test_sound(self):
        if self.busy:
            return
        import winsound
        try:
            winsound.PlaySound(str(ROOT / 'assets' / 'channel-test.wav'),
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.status_text.set('测试音共 8 秒：先左侧低音，再右侧高音，重复两轮。请让 Windows 输出到主音箱，试听是否分别发声。')
        except (OSError, RuntimeError) as error:
            self.status_text.set('测试音未能播放：' + friendly_error(error))

    def stop_test_sound(self):
        import winsound
        winsound.PlaySound(None, 0)

    def update_controls(self):
        has_host = any(speaker.label == self.host_var.get() for speaker in self.speakers)
        has_guest = any(speaker.label == self.guest_var.get() for speaker in self.speakers)
        distinct = self.host_var.get() != self.guest_var.get()
        for button in (self.scan_button, self.bluetooth_button):
            button.configure(state='disabled' if self.busy else 'normal')
        self.host_combo.configure(state='disabled' if self.busy else 'readonly')
        self.guest_combo.configure(state='disabled' if self.busy else 'readonly')
        for button in (self.start_button, self.stop_button):
            button.configure(state='normal' if not self.busy and has_host and has_guest and distinct else 'disabled')
        self.read_button.configure(state='normal' if not self.busy and has_host else 'disabled')
        readable_volume = not self.busy and has_host and self.last_volume is not None
        self.slider.configure(state='normal' if readable_volume else 'disabled')
        self.apply_button.configure(state='normal' if readable_volume else 'disabled')
        self.sync_button.configure(state='normal' if not self.busy and has_host and self.group_text.get() == '已开启' else 'disabled')
        self.channel_combo.configure(state='disabled' if self.busy else 'readonly')
        self.channel_button.configure(state='normal' if not self.busy and has_host and has_guest and distinct else 'disabled')
        self.test_sound_button.configure(state='disabled' if self.busy else 'normal')

    def on_result(self, kind, value, error):
        self.busy = False
        self.progress.stop()
        if error:
            self.status_text.set('操作未确认：' + friendly_error(error))
            if kind in ('start', 'stop', 'status', 'channel'):
                self.group_text.set('未确认')
                self.channel_text.set('主音箱声道：未确认，请刷新状态')
            if kind in ('volume', 'status'):
                self.last_volume = None
                self.volume_text.set('未确认')
                self.slider_text.set('音量结果未确认，请刷新状态')
            if kind == 'status':
                self.battery_text.set('未知')
            self.show()
        elif kind == 'devices':
            self.speakers = value
            labels = [speaker.label for speaker in value]
            for combo in (self.host_combo, self.guest_combo):
                combo.configure(values=labels)
            host = next((s for s in value if s.address == self.selection.get('host')), value[0] if value else None)
            guest = next((s for s in value if s.address == self.selection.get('guest') and s != host), next((s for s in value if s != host), None))
            self.host_var.set(host.label if host else '')
            self.guest_var.set(guest.label if guest else '')
            self.selection_changed()
            self.status_text.set(f'找到 {len(value)} 台已配对音箱。' + ('请选择主音箱，然后刷新状态。' if len(value) >= 2 else '请点蓝牙设置，分别配对两台 BOOM 4，再刷新设备。'))
        elif kind == 'status':
            self.group_text.set('已开启' if value.host_on else '已关闭' if value.host_on is False else '未确认')
            self.channel_text.set(host_channel_label(value.audio_option, value.host_on))
            self.battery_text.set(f'{value.battery}%' if value.battery is not None else '未知')
            if value.volume is not None:
                self.set_volume_display(value.volume)
            else:
                self.last_volume = None
                self.volume_text.set('未知')
            member = '主音箱报告副音箱已加入。' if value.guest_reported else '副音箱成员报告尚未确认。' if value.host_on else '可从电脑开启双音箱组队。'
            self.status_text.set(member + (' 部分信息暂不可读。' if value.errors else ''))
        elif kind in ('start', 'stop'):
            self.group_text.set('已开启' if value.host_on else '已关闭' if value.host_on is False else '未确认')
            self.channel_text.set('主音箱声道：请刷新状态')
            accepted = bool(value.members) and all(member.state in ('join accepted', 'reported by main speaker', 'leave accepted') for member in value.members)
            if kind == 'start' and value.host_on and accepted:
                self.status_text.set('组队指令已接受。请将 Windows 声音输出选为主音箱，试听两台是否都发声。')
            elif kind == 'stop' and value.host_on is False and accepted:
                self.status_text.set('主音箱已关闭广播，移除副音箱指令已接受。')
            else:
                self.status_text.set('组队结果未完全确认。请刷新状态并试听；检查音箱开机、手机蓝牙是否关闭。')
        elif kind == 'volume':
            self.set_volume_display(value)
            self.status_text.set('主音箱音量已回读确认。')
        elif kind == 'sync':
            self.status_text.set('音量同步指令已接受，请试听两台。')
        elif kind == 'channel':
            self.group_text.set('已开启')
            self.channel_text.set(host_channel_label(value.audio_option, True) + ' · 副音箱待试听')
            self.status_text.set('主音箱配置已回读，副音箱声道指令已接受。请播放左右测试音，确认两台实际声道。')
        self.update_controls()

    def set_volume_display(self, value):
        self.last_volume = value
        self.volume_value.set(value)
        self.volume_text.set(f'{round(value / 31 * 100)}%')
        self.slider_text.set(f'当前音量：{round(value / 31 * 100)}%')

    def drain_events(self):
        if self.closing:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'tray_ready':
                    self.tray_ready = True
                elif kind == 'progress':
                    self.status_text.set(value)
                elif kind == 'result':
                    self.on_result(*value)
                elif kind == 'command':
                    if value.startswith('channel:'):
                        self.apply_channel(value.removeprefix('channel:'))
                        continue
                    {'show': self.show, 'start': lambda: self.group('start'), 'stop': lambda: self.group('stop'),
                     'status': self.read_status, 'up': lambda: self.nudge_volume(1),
                     'down': lambda: self.nudge_volume(-1), 'quit': self.quit}[value]()
                    if self.closing:
                        return
        except queue.Empty:
            pass
        self.root.after(75, self.drain_events)

    def show(self):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def hide(self):
        if self.tray_ready:
            self.root.withdraw()
        else:
            self.quit()

    def quit(self):
        if self.busy:
            self.status_text.set('当前操作尚未完成，请稍候再退出。')
            self.show()
            return
        self.closing = True
        if not self.smoke:
            self.stop_test_sound()
        if self.tray:
            self.tray.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.destroy()


def main():
    smoke = '--smoke' in sys.argv
    if smoke:
        # Exercise lazy native imports too, without starting a scan, a control
        # connection or a notification-area icon.
        import pystray._win32
        import bleak.backends.winrt.client
        import bleak.backends.winrt.scanner
        import bleak.backends.winrt.util
    if not smoke:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        mutex = kernel.CreateMutexW(None, False, 'Local\\Boom4Tray.Controller.v1')
        if not mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            user = ctypes.WinDLL('user32')
            user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user.FindWindowW.restype = wintypes.HWND
            user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user.SetForegroundWindow.argtypes = [wintypes.HWND]
            window = user.FindWindowW(None, TITLE)
            if window:
                user.ShowWindow(window, 9)
                user.SetForegroundWindow(window)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(mutex)
            return
    else:
        mutex = None
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        root = tk.Tk()
        App(root, smoke=smoke)
        root.mainloop()
    finally:
        if mutex:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(mutex)


if __name__ == '__main__':
    main()
