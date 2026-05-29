#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信读书导出工具 - 轻量桌面 GUI
"""
from pathlib import Path
import ctypes
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from weread_scraper import load_cookie_values, load_cookies, login_with_qr, scrape_book


APP_DIR = Path(__file__).resolve().parent


class WereadGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("微信读书导出")
        self._configure_dpi()
        self._configure_window()

        self.messages = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()

        self.url_var = tk.StringVar()
        self.cookie_var = tk.StringVar(value=str(APP_DIR / "cookies.txt"))
        self.output_var = tk.StringVar(value=str(APP_DIR / "output"))
        self.show_browser_var = tk.BooleanVar(value=False)
        self.cookie_status_var = tk.StringVar()

        self.configure(bg="#f6f7f9")
        self._setup_style()
        self._build_ui()
        self._bind_responsive_events()
        self._refresh_cookie_status()
        self.after(120, self._poll_messages)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_dpi(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        try:
            scaling = self.winfo_fpixels("1i") / 72.0
            if 0.9 <= scaling <= 2.5:
                self.tk.call("tk", "scaling", scaling)
        except tk.TclError:
            pass

    def _configure_window(self):
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(1200, max(920, int(screen_w * 0.82)))
        height = min(820, max(640, int(screen_h * 0.82)))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(1100, max(860, int(screen_w * 0.55))), min(700, max(600, int(screen_h * 0.55))))

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", font=("Microsoft YaHei UI", 10), background="#f6f7f9")
        style.configure("Shell.TFrame", background="#f6f7f9")
        style.configure("Panel.TFrame", background="#ffffff", relief="flat")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"), foreground="#1f2937", background="#f6f7f9")
        style.configure("Subtle.TLabel", foreground="#667085", background="#f6f7f9")
        style.configure("Panel.TLabel", foreground="#344054", background="#ffffff")
        style.configure("Status.TLabel", foreground="#0f766e", background="#ffffff")
        style.configure("TEntry", padding=8)
        style.configure("Primary.TButton", padding=(18, 10), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Secondary.TButton", padding=(14, 9))
        style.configure("Danger.TButton", padding=(14, 9))
        style.configure("TCheckbutton", background="#ffffff")

    def _build_ui(self):
        shell = ttk.Frame(self, style="Shell.TFrame", padding=24)
        shell.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(shell, style="Shell.TFrame")
        header.pack(fill=tk.X)
        ttk.Label(header, text="微信读书导出", style="Title.TLabel").pack(anchor=tk.W)
        self.subtitle_label = ttk.Label(
            header,
            text="扫码登录、输入阅读页链接，然后导出为 Markdown。",
            style="Subtle.TLabel",
        )
        self.subtitle_label.pack(anchor=tk.W, pady=(6, 0))

        panel = ttk.Frame(shell, style="Panel.TFrame", padding=18)
        panel.pack(fill=tk.X, pady=(22, 14))
        panel.columnconfigure(1, weight=1)

        ttk.Label(panel, text="阅读页 URL", style="Panel.TLabel").grid(row=0, column=0, sticky=tk.W, padx=(0, 12), pady=8)
        ttk.Entry(panel, textvariable=self.url_var).grid(row=0, column=1, sticky=tk.EW, pady=8)

        ttk.Label(panel, text="Cookie 文件", style="Panel.TLabel").grid(row=1, column=0, sticky=tk.W, padx=(0, 12), pady=8)
        ttk.Entry(panel, textvariable=self.cookie_var).grid(row=1, column=1, sticky=tk.EW, pady=8)
        ttk.Button(panel, text="选择", style="Secondary.TButton", command=self._choose_cookie).grid(row=1, column=2, padx=(10, 0), pady=8)

        ttk.Label(panel, text="输出目录", style="Panel.TLabel").grid(row=2, column=0, sticky=tk.W, padx=(0, 12), pady=8)
        ttk.Entry(panel, textvariable=self.output_var).grid(row=2, column=1, sticky=tk.EW, pady=8)
        ttk.Button(panel, text="选择", style="Secondary.TButton", command=self._choose_output).grid(row=2, column=2, padx=(10, 0), pady=8)

        options = ttk.Frame(panel, style="Panel.TFrame")
        options.grid(row=3, column=1, columnspan=2, sticky=tk.W, pady=(8, 2))
        ttk.Checkbutton(options, text="显示采集浏览器", variable=self.show_browser_var).pack(side=tk.LEFT)

        ttk.Label(panel, textvariable=self.cookie_status_var, style="Status.TLabel").grid(row=4, column=1, columnspan=2, sticky=tk.W, pady=(8, 0))

        actions = ttk.Frame(shell, style="Shell.TFrame")
        actions.pack(fill=tk.X, pady=(0, 14))
        self.login_button = ttk.Button(actions, text="扫码登录", style="Secondary.TButton", command=self._start_login)
        self.login_button.pack(side=tk.LEFT)
        self.export_button = ttk.Button(actions, text="开始导出", style="Primary.TButton", command=self._start_export)
        self.export_button.pack(side=tk.LEFT, padx=(10, 0))
        self.stop_button = ttk.Button(actions, text="停止", style="Danger.TButton", command=self._stop_work, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(10, 0))

        log_panel = ttk.Frame(shell, style="Panel.TFrame", padding=12)
        log_panel.pack(fill=tk.BOTH, expand=True)
        log_panel.rowconfigure(0, weight=1)
        log_panel.columnconfigure(0, weight=1)
        self.log = tk.Text(
            log_panel,
            wrap=tk.WORD,
            borderwidth=0,
            highlightthickness=0,
            bg="#101828",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            padx=14,
            pady=12,
            font=("Consolas", 10),
        )
        self.log.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar = ttk.Scrollbar(log_panel, command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky=tk.NS)
        self.log.configure(yscrollcommand=scrollbar.set)
        self._append_log("准备就绪。先扫码登录，或直接使用已有 cookies.txt。")

    def _bind_responsive_events(self):
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        if event.widget is self:
            wrap = max(520, event.width - 96)
            self.subtitle_label.configure(wraplength=wrap)

    def _choose_cookie(self):
        path = filedialog.askopenfilename(
            title="选择 Cookie 文件",
            initialdir=str(APP_DIR),
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.cookie_var.set(path)
            self._refresh_cookie_status()

    def _choose_output(self):
        path = filedialog.askdirectory(title="选择输出目录", initialdir=str(APP_DIR))
        if path:
            self.output_var.set(path)

    def _refresh_cookie_status(self):
        cookie_path = self.cookie_var.get().strip()
        try:
            values = load_cookie_values(cookie_path)
            self.cookie_status_var.set(f"已检测到登录信息：wr_vid={values.get('wr_vid', '')}")
        except Exception:
            self.cookie_status_var.set("未检测到有效登录信息，请扫码登录。")

    def _append_log(self, message):
        self.log.insert(tk.END, f"{message}\n")
        self.log.see(tk.END)

    def _enqueue_log(self, message):
        self.messages.put(("log", message))

    def _set_busy(self, busy):
        state = tk.DISABLED if busy else tk.NORMAL
        self.login_button.configure(state=state)
        self.export_button.configure(state=state)
        self.stop_button.configure(state=tk.NORMAL if busy else tk.DISABLED)

    def _run_worker(self, target):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("任务进行中", "当前已有任务在运行。")
            return

        self.stop_event.clear()
        self._set_busy(True)

        def wrapper():
            try:
                target()
            except Exception as exc:
                self._enqueue_log(f"[错误] {exc}")
            finally:
                self.messages.put(("refresh", None))
                self.messages.put(("busy", False))

        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def _start_login(self):
        cookie_path = self.cookie_var.get().strip()
        if not cookie_path:
            messagebox.showwarning("缺少 Cookie 文件", "请先填写 Cookie 文件路径。")
            return

        def work():
            login_with_qr(cookie_path=cookie_path, progress=self._enqueue_log, stop_event=self.stop_event)

        self._run_worker(work)

    def _start_export(self):
        url = self.url_var.get().strip()
        cookie_path = self.cookie_var.get().strip()
        output_dir = self.output_var.get().strip()

        if not url:
            messagebox.showwarning("缺少 URL", "请粘贴微信读书阅读页 URL。")
            return
        if "weread.qq.com" not in url:
            messagebox.showwarning("URL 不正确", "请使用微信读书 weread.qq.com 的阅读页链接。")
            return

        def work():
            cookies = load_cookies(cookie_path)
            result = scrape_book(
                url,
                cookies,
                output_dir,
                headless=not self.show_browser_var.get(),
                progress=self._enqueue_log,
                stop_event=self.stop_event,
            )
            if result:
                self._enqueue_log(f"文件已生成：{result}")

        self._run_worker(work)

    def _stop_work(self):
        self.stop_event.set()
        self._append_log("正在请求停止，当前步骤结束后会退出。")

    def _poll_messages(self):
        while True:
            try:
                kind, payload = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._append_log(payload)
            elif kind == "busy":
                self._set_busy(payload)
            elif kind == "refresh":
                self._refresh_cookie_status()
        self.after(120, self._poll_messages)

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
        self.destroy()


def main():
    app = WereadGui()
    app.mainloop()


if __name__ == "__main__":
    main()
