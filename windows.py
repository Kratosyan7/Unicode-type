import array
import ctypes
import json
import platform
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox, ttk

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0
VK_RETURN = 0x0D
VK_TAB = 0x09
ULONG_PTR = ctypes.c_size_t
CONFIG_PATH = Path(__file__).with_name("windows_settings.json")

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUTUNION),
    ]


class WindowsTyperError(RuntimeError):
    pass


class FocusError(WindowsTyperError):
    pass


class BlockedInputError(WindowsTyperError):
    pass


class WindowsTyper:
    def __init__(self):
        if platform.system() != "Windows":
            raise RuntimeError("Этот файл предназначен для запуска только в Windows.")

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.send_input = self.user32.SendInput
        self.send_input.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.send_input.restype = wintypes.UINT

        self.map_virtual_key = self.user32.MapVirtualKeyW
        self.map_virtual_key.argtypes = (wintypes.UINT, wintypes.UINT)
        self.map_virtual_key.restype = wintypes.UINT

        self.get_foreground_window = self.user32.GetForegroundWindow
        self.get_foreground_window.argtypes = ()
        self.get_foreground_window.restype = wintypes.HWND

        self.get_window_text = self.user32.GetWindowTextW
        self.get_window_text.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        self.get_window_text.restype = ctypes.c_int

    def get_foreground_window_title(self):
        handle = self.get_foreground_window()
        if not handle:
            return ""

        buffer = ctypes.create_unicode_buffer(512)
        self.get_window_text(handle, buffer, len(buffer))
        return buffer.value.strip()

    def ensure_target_is_ready(self, app_window_title):
        active_title = self.get_foreground_window_title()
        if not active_title:
            raise FocusError(
                "Windows не показывает активное окно. Сначала сфокусируйте обычное поле ввода."
            )

        if active_title == app_window_title:
            raise FocusError(
                "Сфокусируйте целевое поле в другом приложении до окончания таймера старта."
            )

        return active_title

    def _send_keyboard_input(self, keyboard_input):
        event = INPUT()
        event.type = INPUT_KEYBOARD
        event.ki = keyboard_input

        ctypes.set_last_error(0)
        sent = self.send_input(1, ctypes.byref(event), ctypes.sizeof(event))
        if sent == 1:
            return

        error_code = ctypes.get_last_error()
        if error_code:
            raise WindowsTyperError(ctypes.FormatError(error_code))

        raise BlockedInputError(
            "Windows отклонил ввод. Запустите скрипт с теми же правами, что и целевое приложение, "
            "и используйте обычное текстовое поле."
        )

    def _send_virtual_key(self, virtual_key):
        scan_code = self.map_virtual_key(virtual_key, MAPVK_VK_TO_VSC)
        if not scan_code:
            raise WindowsTyperError(f"Не удалось получить scan code для клавиши {virtual_key}.")

        self._send_keyboard_input(KEYBDINPUT(0, scan_code, KEYEVENTF_SCANCODE, 0, 0))
        self._send_keyboard_input(KEYBDINPUT(0, scan_code, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, 0))

    def _send_unicode_char(self, ch):
        for code_unit in array.array("H", ch.encode("utf-16le")):
            self._send_keyboard_input(KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE, 0, 0))
            self._send_keyboard_input(KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))

    def type_text(self, text, delay, stop_requested, on_progress=None):
        for index, ch in enumerate(text, start=1):
            if stop_requested():
                return False

            if ch == "\n":
                self._send_virtual_key(VK_RETURN)
            elif ch == "\t":
                self._send_virtual_key(VK_TAB)
            elif ch == "\r":
                continue
            else:
                self._send_unicode_char(ch)

            if on_progress is not None:
                on_progress(index, len(text))

            time.sleep(delay)

        return True


class TextTyperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Windows Text Typer")
        self.root.geometry("1040x620")
        self.root.minsize(980, 570)

        self.typer = WindowsTyper()
        self.is_typing = False
        self.stop_requested = False
        self.focus_poll_job = None

        self.delay_var = tk.StringVar(value="0.1")
        self.start_after_var = tk.StringVar(value="3")
        self.focus_delay_var = tk.StringVar(value="0.5")
        self.status_var = tk.StringVar(value="Готово")
        self.stats_var = tk.StringVar(value="0 символов • 0.0 сек")
        self.focus_var = tk.StringVar(value="Фокус: окно приложения")
        self.theme_var = tk.StringVar(value="Светлая")
        self.compact_var = tk.BooleanVar(value=False)
        self.theme_name = "light"
        self.theme_tokens = {}

        self.load_settings()
        self.build_ui()
        for var in (self.delay_var, self.start_after_var, self.focus_delay_var):
            var.trace_add("write", self.update_text_stats)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.start_focus_polling()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)
        self.main_frame = main

        self.configure_styles()
        self.configure_menu()

        hero = ttk.Frame(main, padding=(18, 18, 18, 14), style="Hero.TFrame")
        hero.pack(fill="x", pady=(0, 12))
        self.hero_frame = hero

        self.title_label = ttk.Label(hero, text="Windows Text Typer", style="HeroTitle.TLabel")
        self.title_label.pack(anchor="w")
        self.subtitle_label = ttk.Label(
            hero,
            text=(
                "Печатай Unicode напрямую в обычные поля Windows. "
                "Вставляй текст из буфера и запускай набор без лишних действий."
            ),
            style="HeroSubtitle.TLabel",
        )
        self.subtitle_label.pack(anchor="w", pady=(6, 0))

        settings = ttk.LabelFrame(main, text="Параметры", padding=12)
        settings.pack(fill="x", pady=(0, 12))
        self.settings_frame = settings

        ttk.Label(settings, text="Задержка").grid(row=0, column=0, sticky="w")
        self.delay_entry = ttk.Entry(settings, textvariable=self.delay_var, width=10)
        self.delay_entry.grid(row=1, column=0, padx=(0, 16), sticky="w")

        ttk.Label(settings, text="Старт через").grid(row=0, column=1, sticky="w")
        self.start_after_entry = ttk.Entry(settings, textvariable=self.start_after_var, width=10)
        self.start_after_entry.grid(row=1, column=1, padx=(0, 16), sticky="w")

        ttk.Label(settings, text="После фокуса").grid(row=0, column=2, sticky="w")
        self.focus_delay_entry = ttk.Entry(settings, textvariable=self.focus_delay_var, width=10)
        self.focus_delay_entry.grid(row=1, column=2, padx=(0, 16), sticky="w")

        ttk.Label(settings, text="Тема").grid(row=0, column=3, sticky="w")
        self.theme_combo = ttk.Combobox(
            settings,
            textvariable=self.theme_var,
            values=("Светлая", "Тёмная"),
            state="readonly",
            width=12,
        )
        self.theme_combo.grid(row=1, column=3, sticky="w")
        self.theme_combo.bind("<<ComboboxSelected>>", self.change_theme)

        self.compact_check = ttk.Checkbutton(
            settings,
            text="Компактный режим",
            variable=self.compact_var,
            command=self.toggle_compact_mode,
        )
        self.compact_check.grid(row=1, column=4, padx=(16, 0), sticky="w")

        info = ttk.Frame(main, style="Info.TFrame", padding=(14, 10))
        info.pack(fill="x", pady=(0, 12))
        self.info_frame = info
        self.info_label = ttk.Label(
            info,
            text=(
                "Ctrl+V и кнопка «Вставить» работают в поле редактора. "
                "После нажатия «Старт» переведи фокус в целевое окно до конца таймера."
            ),
            style="Info.TLabel",
        )
        self.info_label.pack(anchor="w")

        text_frame = ttk.LabelFrame(main, text="Текст", padding=8)
        text_frame.pack(fill="both", expand=True)
        self.text_frame = text_frame

        self.text_widget = tk.Text(
            text_frame,
            wrap="word",
            height=11,
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=10,
            font=("Segoe UI", 12),
            background="#FBFCFE",
        )
        self.text_widget.pack(side="left", fill="both", expand=True)
        self.text_widget.bind("<Control-v>", self.handle_paste)
        self.text_widget.bind("<Control-V>", self.handle_paste)
        self.text_widget.bind("<Control-Cyrillic_em>", self.handle_paste)
        self.text_widget.bind("<Control-Cyrillic_EM>", self.handle_paste)
        self.text_widget.bind("<Control-a>", self.handle_select_all)
        self.text_widget.bind("<Control-A>", self.handle_select_all)
        self.text_widget.bind("<Control-Cyrillic_ef>", self.handle_select_all)
        self.text_widget.bind("<Control-Cyrillic_EF>", self.handle_select_all)
        self.text_widget.bind("<Control-c>", self.handle_copy)
        self.text_widget.bind("<Control-C>", self.handle_copy)
        self.text_widget.bind("<Control-Cyrillic_es>", self.handle_copy)
        self.text_widget.bind("<Control-Cyrillic_ES>", self.handle_copy)
        self.text_widget.bind("<Control-x>", self.handle_cut)
        self.text_widget.bind("<Control-X>", self.handle_cut)
        self.text_widget.bind("<Control-Cyrillic_che>", self.handle_cut)
        self.text_widget.bind("<Control-Cyrillic_CHE>", self.handle_cut)
        self.text_widget.bind("<<Paste>>", self.handle_paste)
        self.text_widget.bind("<<Modified>>", self.on_text_modified)

        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=self.text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        self.text_widget.configure(yscrollcommand=scrollbar.set)

        meta = ttk.Frame(main)
        meta.pack(fill="x", pady=(10, 0))
        self.meta_frame = meta
        self.stats_label = ttk.Label(meta, textvariable=self.stats_var, style="Meta.TLabel")
        self.stats_label.pack(side="left")
        self.focus_label = ttk.Label(meta, textvariable=self.focus_var, style="Meta.TLabel")
        self.focus_label.pack(side="right")

        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(12, 0))
        self.actions_frame = actions

        self.paste_button = ttk.Button(actions, text="Вставить", command=self.paste_text, style="Secondary.TButton")
        self.paste_button.pack(side="left")
        self.clear_button = ttk.Button(actions, text="Очистить", command=self.clear_text, style="Secondary.TButton")
        self.clear_button.pack(side="left", padx=(8, 0))
        self.sample_button = ttk.Button(
            actions,
            text="Тестовый текст",
            command=self.insert_test_text,
            style="Secondary.TButton",
        )
        self.sample_button.pack(side="left", padx=(8, 0))
        self.start_button = ttk.Button(actions, text="Старт", command=self.start_typing, style="Accent.TButton")
        self.start_button.pack(side="left", padx=(20, 0))
        self.stop_button = ttk.Button(actions, text="Стоп", command=self.stop_typing, state="disabled", style="Secondary.TButton")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.status_label = ttk.Label(actions, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(side="right")

        self.apply_theme()
        self.apply_layout_mode()
        self.update_text_stats()

    def configure_styles(self):
        style = ttk.Style()
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            return

        style.configure("TEntry", padding=6)
        style.configure("TButton", padding=(12, 8), font=("Segoe UI", 11))
        style.configure("TCombobox", padding=6)
        style.configure("TCheckbutton", font=("Segoe UI", 10))
        self.style = style

    def configure_menu(self):
        menubar = tk.Menu(self.root)
        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Select All", accelerator="Ctrl+A", command=self.select_all_text)
        edit_menu.add_command(label="Copy", accelerator="Ctrl+C", command=self.copy_selection)
        edit_menu.add_command(label="Cut", accelerator="Ctrl+X", command=self.cut_selection)
        edit_menu.add_command(label="Paste", accelerator="Ctrl+V", command=self.paste_text)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        self.root.configure(menu=menubar)

    def get_theme_tokens(self, theme_name):
        if theme_name == "dark":
            return {
                "root_bg": "#111827",
                "panel_bg": "#111827",
                "surface_bg": "#1F2937",
                "text_bg": "#0F172A",
                "text_fg": "#F9FAFB",
                "muted_fg": "#CBD5E1",
                "entry_bg": "#0F172A",
                "entry_fg": "#F9FAFB",
                "button_bg": "#334155",
                "button_fg": "#F9FAFB",
                "insert_bg": "#F9FAFB",
                "select_bg": "#2563EB",
            }

        return {
            "root_bg": "#F3F6FB",
            "panel_bg": "#F3F6FB",
            "surface_bg": "#E8EEF8",
            "text_bg": "#FFFFFF",
            "text_fg": "#111111",
            "muted_fg": "#334155",
            "entry_bg": "#FFFFFF",
            "entry_fg": "#111111",
            "button_bg": "#D8E2F2",
            "button_fg": "#111827",
            "insert_bg": "#111111",
            "select_bg": "#BBD3FF",
        }

    def apply_theme(self):
        self.theme_tokens = self.get_theme_tokens(self.theme_name)
        colors = self.theme_tokens

        self.style.configure("TFrame", background=colors["root_bg"])
        self.style.configure("TLabelframe", background=colors["panel_bg"], bordercolor=colors["surface_bg"])
        self.style.configure(
            "TLabelframe.Label",
            background=colors["panel_bg"],
            foreground=colors["text_fg"],
            font=("Segoe UI Semibold", 11),
        )
        self.style.configure("TLabel", background=colors["root_bg"], foreground=colors["text_fg"], font=("Segoe UI", 11))
        self.style.configure("Hero.TFrame", background=colors["surface_bg"])
        self.style.configure("HeroTitle.TLabel", background=colors["surface_bg"], foreground=colors["text_fg"], font=("Segoe UI Semibold", 20))
        self.style.configure("HeroSubtitle.TLabel", background=colors["surface_bg"], foreground=colors["muted_fg"], font=("Segoe UI", 11))
        self.style.configure("Info.TFrame", background=colors["surface_bg"])
        self.style.configure("Info.TLabel", background=colors["surface_bg"], foreground=colors["muted_fg"], font=("Segoe UI", 10))
        self.style.configure("Status.TLabel", background=colors["root_bg"], foreground=colors["muted_fg"], font=("Segoe UI", 10))
        self.style.configure("Meta.TLabel", background=colors["root_bg"], foreground=colors["muted_fg"], font=("Segoe UI", 10))
        self.style.configure("TCheckbutton", background=colors["root_bg"], foreground=colors["text_fg"])
        self.style.map("TCheckbutton", background=[("active", colors["root_bg"])], foreground=[("active", colors["text_fg"])])
        self.style.configure(
            "TEntry",
            fieldbackground=colors["entry_bg"],
            foreground=colors["entry_fg"],
            insertcolor=colors["insert_bg"],
        )
        self.style.map("TEntry", fieldbackground=[("readonly", colors["entry_bg"])], foreground=[("readonly", colors["entry_fg"])])
        self.style.configure(
            "TCombobox",
            fieldbackground=colors["entry_bg"],
            background=colors["entry_bg"],
            foreground=colors["entry_fg"],
            arrowcolor=colors["entry_fg"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", colors["entry_bg"])],
            foreground=[("readonly", colors["entry_fg"])],
            selectbackground=[("readonly", colors["entry_bg"])],
            selectforeground=[("readonly", colors["entry_fg"])],
        )
        self.style.configure("TButton", background=colors["button_bg"], foreground=colors["button_fg"])
        self.style.configure("Secondary.TButton", background=colors["button_bg"], foreground=colors["button_fg"])
        self.style.configure("Accent.TButton", background=colors["select_bg"], foreground=colors["text_fg"])
        self.style.map(
            "TButton",
            background=[("active", colors["surface_bg"])],
            foreground=[("disabled", colors["muted_fg"])],
        )
        self.style.map(
            "Secondary.TButton",
            background=[("active", colors["surface_bg"])],
            foreground=[("disabled", colors["muted_fg"])],
        )
        self.style.map(
            "Accent.TButton",
            background=[("active", colors["button_bg"])],
            foreground=[("disabled", colors["muted_fg"])],
        )

        self.root.configure(background=colors["root_bg"])
        self.text_widget.configure(
            background=colors["text_bg"],
            foreground=colors["text_fg"],
            insertbackground=colors["insert_bg"],
            selectbackground=colors["select_bg"],
            selectforeground=colors["text_fg"],
        )

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def change_theme(self, event=None):
        self.theme_name = "dark" if self.theme_var.get() == "Тёмная" else "light"
        self.apply_theme()
        self.save_settings()

    def load_settings(self):
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return

        self.delay_var.set(str(data.get("delay", self.delay_var.get())))
        self.start_after_var.set(str(data.get("start_after", self.start_after_var.get())))
        self.focus_delay_var.set(str(data.get("focus_delay", self.focus_delay_var.get())))
        self.theme_name = data.get("theme", self.theme_name)
        self.theme_var.set("Тёмная" if self.theme_name == "dark" else "Светлая")
        self.compact_var.set(bool(data.get("compact", False)))

    def save_settings(self):
        data = {
            "delay": self.delay_var.get().strip(),
            "start_after": self.start_after_var.get().strip(),
            "focus_delay": self.focus_delay_var.get().strip(),
            "theme": self.theme_name,
            "compact": self.compact_var.get(),
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def on_close(self):
        self.save_settings()
        if self.focus_poll_job is not None:
            self.root.after_cancel(self.focus_poll_job)
        self.root.destroy()

    def toggle_compact_mode(self):
        self.apply_layout_mode()
        self.save_settings()

    def apply_layout_mode(self):
        for widget in (
            self.hero_frame,
            self.settings_frame,
            self.info_frame,
            self.text_frame,
            self.meta_frame,
            self.actions_frame,
        ):
            widget.pack_forget()

        if not self.compact_var.get():
            self.hero_frame.pack(fill="x", pady=(0, 12))
        self.settings_frame.pack(fill="x", pady=(0, 12))
        if not self.compact_var.get():
            self.info_frame.pack(fill="x", pady=(0, 12))
        self.text_frame.pack(fill="both", expand=True)
        self.meta_frame.pack(fill="x", pady=(10, 0))
        self.actions_frame.pack(fill="x", pady=(12, 0))

    def start_focus_polling(self):
        self.update_focus_status()

    def update_focus_status(self):
        title = self.typer.get_foreground_window_title()
        if title and title != self.root.title():
            self.focus_var.set(f"Фокус: {title[:40]}")
        else:
            self.focus_var.set("Фокус: окно приложения")
        self.focus_poll_job = self.root.after(700, self.update_focus_status)

    def on_text_modified(self, event=None):
        if self.text_widget.edit_modified():
            self.update_text_stats()
            self.text_widget.edit_modified(False)

    def update_text_stats(self, *_):
        text = self.text_widget.get("1.0", "end-1c")
        chars = len(text)
        delay = self.get_float_value(self.delay_var.get(), default=0.1)
        start_after = self.get_float_value(self.start_after_var.get(), default=0.0)
        focus_delay = self.get_float_value(self.focus_delay_var.get(), default=0.0)
        estimated = chars * delay + start_after + focus_delay
        self.stats_var.set(f"{chars} символов • ~{self.format_duration(estimated)}")

    def format_duration(self, seconds):
        if seconds < 60:
            return f"{seconds:.1f} сек"
        minutes = int(seconds // 60)
        rest = int(seconds % 60)
        return f"{minutes}м {rest:02d}с"

    def get_float_value(self, value, default=0.0):
        try:
            parsed = float(str(value).strip())
            if parsed < 0:
                return default
            return parsed
        except Exception:
            return default

    def clear_text(self):
        if self.is_typing:
            return
        self.text_widget.delete("1.0", "end")
        self.update_text_stats()
        self.set_status("Текст очищен")

    def select_all_text(self):
        self.text_widget.focus_set()
        self.text_widget.tag_add("sel", "1.0", "end-1c")
        self.text_widget.mark_set("insert", "1.0")
        self.text_widget.see("insert")
        self.set_status("Текст выделен")

    def copy_selection(self):
        try:
            selected_text = self.text_widget.get("sel.first", "sel.last")
        except tk.TclError:
            self.set_status("Нет выделенного текста")
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(selected_text)
        self.set_status("Текст скопирован")

    def cut_selection(self):
        if self.is_typing:
            return

        try:
            selected_text = self.text_widget.get("sel.first", "sel.last")
        except tk.TclError:
            self.set_status("Нет выделенного текста")
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(selected_text)
        self.text_widget.delete("sel.first", "sel.last")
        self.set_status("Текст вырезан")

    def handle_paste(self, event=None):
        self.paste_text()
        return "break"

    def handle_select_all(self, event=None):
        self.select_all_text()
        return "break"

    def handle_copy(self, event=None):
        self.copy_selection()
        return "break"

    def handle_cut(self, event=None):
        self.cut_selection()
        return "break"

    def paste_text(self):
        if self.is_typing:
            return

        try:
            clipboard_text = self.root.clipboard_get()
        except tk.TclError:
            self.set_status("Буфер обмена пуст")
            return

        if not clipboard_text:
            self.set_status("Буфер обмена пуст")
            return

        self.text_widget.insert("insert", clipboard_text)
        self.update_text_stats()
        self.text_widget.focus_set()
        self.set_status("Текст вставлен из буфера")

    def insert_test_text(self):
        if self.is_typing:
            return
        sample = (
            "Привет, это безопасный тестовый ввод.\n"
            "Он печатает символы по одному в обычное поле Windows.\n"
            "Проверь работу в Блокноте, затем в своем приложении.\n"
        )
        self.text_widget.delete("1.0", "end")
        self.text_widget.insert("1.0", sample)
        self.update_text_stats()
        self.set_status("Тестовый текст вставлен")

    def validate_float(self, value, name):
        try:
            parsed = float(value)
            if parsed < 0:
                raise ValueError
            return parsed
        except ValueError:
            messagebox.showerror("Ошибка", f"{name} должно быть неотрицательным числом.")
            return None

    def start_typing(self):
        if self.is_typing:
            return

        text = self.text_widget.get("1.0", "end-1c")
        if not text:
            messagebox.showwarning("Пустой текст", "Сначала введи текст.")
            return

        delay = self.validate_float(self.delay_var.get().strip(), "Задержка")
        if delay is None:
            return

        start_after = self.validate_float(self.start_after_var.get().strip(), "Время старта")
        if start_after is None:
            return

        focus_delay = self.validate_float(self.focus_delay_var.get().strip(), "Задержка после фокуса")
        if focus_delay is None:
            return

        self.is_typing = True
        self.stop_requested = False
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.save_settings()
        self.set_status(f"Старт через {start_after:.1f} сек. Переведи фокус в целевое поле.")

        threading.Thread(
            target=self.run_action,
            args=(text, delay, start_after, focus_delay),
            daemon=True,
        ).start()

    def stop_typing(self):
        if self.is_typing:
            self.stop_requested = True
            self.set_status("Запрошена остановка...")

    def wait_for_target_focus(self, app_window_title):
        self.set_status("Ожидание целевого фокуса...")
        while not self.stop_requested:
            active_title = self.typer.get_foreground_window_title()
            if active_title and active_title != app_window_title:
                return active_title
            time.sleep(0.2)
        return None

    def sleep_with_stop(self, seconds, status_text=None):
        if seconds <= 0:
            return not self.stop_requested
        if status_text:
            self.set_status(status_text)
        end_time = time.time() + seconds
        while time.time() < end_time:
            if self.stop_requested:
                return False
            time.sleep(0.05)
        return True

    def run_action(self, text, delay, start_after, focus_delay):
        app_window_title = self.root.title()
        try:
            if not self.sleep_with_stop(start_after, f"Старт через {start_after:.1f} сек"):
                self.set_status("Остановлено")
                return

            if self.stop_requested:
                self.set_status("Остановлено")
                return

            active_title = self.wait_for_target_focus(app_window_title)
            if active_title is None:
                self.set_status("Остановлено")
                return

            if not self.sleep_with_stop(
                focus_delay,
                f"Фокус найден: {active_title[:40] or 'без заголовка'}. Старт через {focus_delay:.1f} сек",
            ):
                self.set_status("Остановлено")
                return

            self.set_status(f"Печать в окне: {active_title[:40] or 'без заголовка'}")

            completed = self.typer.type_text(
                text,
                delay,
                stop_requested=lambda: self.stop_requested,
                on_progress=self.update_progress,
            )

            if completed:
                self.set_status("Готово")
            else:
                self.set_status("Остановлено")

        except FocusError as error:
            self.show_runtime_error("Нет целевого фокуса", str(error))
        except BlockedInputError as error:
            self.show_runtime_error(
                "Ввод заблокирован",
                str(error)
                + "\n\nЕсли целевое приложение запущено от администратора, запусти и этот скрипт от администратора.",
            )
        except WindowsTyperError as error:
            self.show_runtime_error("Ошибка Windows", str(error))
        except Exception as error:
            self.show_runtime_error("Неожиданная ошибка", str(error))
        finally:
            self.is_typing = False
            self.stop_requested = False
            self.root.after(0, self.reset_buttons)

    def update_progress(self, current, total):
        self.root.after(0, lambda: self.status_var.set(f"Печать: {current}/{total}"))

    def show_runtime_error(self, title, text):
        self.set_status(title)
        self.root.after(0, lambda: messagebox.showerror(title, text))

    def reset_buttons(self):
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")


def main():
    root = tk.Tk()
    TextTyperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
