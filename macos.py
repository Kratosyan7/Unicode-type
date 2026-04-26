import json
import subprocess
import time
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventKeyboardSetUnicodeString,
    CGEventPost,
    kCGHIDEventTap,
)

ENTER_KEYCODE = 36
TAB_KEYCODE = 48
PASTE_KEYCODE = 9
COMMAND_MASKS = (0x0004, 0x0008, 0x0010, 0x0080)
SELECT_ALL_KEYCODE = 0  # macOS virtual keycode для клавиши «A»
COPY_KEYCODE = 8
CUT_KEYCODE = 7
UNDO_KEYCODE = 6
UNDO_GROUP_DELAY_MS = 900
CONFIG_PATH = Path(__file__).with_name("macos_settings.json")
DESIGN_LABEL_TO_NAME = {
    "Utility": "utility",
    "Studio": "studio",
    "Native": "native",
}
DESIGN_NAME_TO_LABEL = {value: key for key, value in DESIGN_LABEL_TO_NAME.items()}


class TextTyperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Unicode Text Typer")
        self.root.geometry("640x460")
        self.root.minsize(360, 220)

        self.is_typing = False
        self.stop_event = threading.Event()
        self.focus_poll_job = None
        self.focus_probe_inflight = False
        self.app_process_name = None
        self.undo_group_job = None
        self.last_edit_time = 0.0
        self.design_theme_collapsed = False
        self.design_theme_collapse_width = 720

        self.delay_var = tk.StringVar(value="0.1")
        self.start_after_var = tk.StringVar(value="3")
        self.focus_delay_var = tk.StringVar(value="0.5")
        self.status_var = tk.StringVar(value="Готово")
        self.stats_var = tk.StringVar(value="0 символов • 0.0 сек")
        self.focus_var = tk.StringVar(value="Фокус: окно приложения")
        self.theme_var = tk.StringVar(value="Светлая")
        self.design_var = tk.StringVar(value="Native")
        self.compact_var = tk.BooleanVar(value=False)
        self.theme_name = "light"
        self.design_name = "native"
        self.theme_tokens = {}

        self.load_settings()
        self.build_ui()
        for var in (self.delay_var, self.start_after_var, self.focus_delay_var):
            var.trace_add("write", self.update_text_stats)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Configure>", self._on_root_resize, add="+")
        self.root.after(300, self.capture_app_identity)
        self.start_focus_polling()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        self.main_frame = main

        self.configure_styles()
        self.configure_menu()

        self.topbar_frame = ttk.Frame(main, padding=(2, 0, 2, 4), style="Topbar.TFrame")
        self.topbar_frame.pack(fill="x", pady=(0, 6))

        topbar_brand = ttk.Frame(self.topbar_frame, style="Topbar.TFrame")
        topbar_brand.pack(side="left", fill="x", expand=True)
        self.brand_label = ttk.Label(topbar_brand, text="Unicode Type", style="TopbarTitle.TLabel")
        self.brand_label.pack(anchor="w")
        self.platform_label = ttk.Label(topbar_brand, text="macOS edition", style="TopbarMeta.TLabel")
        self.platform_label.pack(anchor="w", pady=(2, 0))

        topbar_controls = ttk.Frame(self.topbar_frame, style="Topbar.TFrame")
        topbar_controls.pack(side="right")
        self.compact_check = ttk.Checkbutton(
            topbar_controls,
            text="Компактный режим",
            variable=self.compact_var,
            command=self.toggle_compact_mode,
        )
        self.compact_check.grid(row=0, column=0, sticky="e")

        self.hero_frame = ttk.Frame(main, padding=(14, 10, 14, 10), style="Hero.TFrame")
        self.hero_frame.pack(fill="x", pady=(0, 8))

        self.hero_badge_label = ttk.Label(self.hero_frame, text="NATIVE", style="HeroEyebrow.TLabel")
        self.hero_badge_label.pack(anchor="w")
        self.title_label = ttk.Label(self.hero_frame, text="Unicode Text Typer", style="HeroTitle.TLabel")
        self.title_label.pack(anchor="w", pady=(8, 0))
        self.subtitle_label = ttk.Label(self.hero_frame, style="HeroSubtitle.TLabel")
        self.subtitle_label.pack(anchor="w", pady=(6, 0))
        self.hero_flow_label = ttk.Label(self.hero_frame, style="HeroFlow.TLabel")
        self.hero_flow_label.pack(anchor="w", pady=(12, 0))

        settings = ttk.LabelFrame(main, text="Параметры", padding=8)
        settings.pack(fill="x", pady=(0, 8))
        self.settings_frame = settings

        ttk.Label(settings, text="Задержка, сек", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        self.delay_entry = ttk.Entry(settings, textvariable=self.delay_var, width=10)
        self.delay_entry.grid(row=1, column=0, padx=(0, 16), sticky="w")

        ttk.Label(settings, text="Старт через, сек", style="FieldLabel.TLabel").grid(row=0, column=1, sticky="w")
        self.start_after_entry = ttk.Entry(settings, textvariable=self.start_after_var, width=10)
        self.start_after_entry.grid(row=1, column=1, padx=(0, 16), sticky="w")

        ttk.Label(settings, text="После фокуса, сек", style="FieldLabel.TLabel").grid(row=0, column=2, sticky="w")
        self.focus_delay_entry = ttk.Entry(settings, textvariable=self.focus_delay_var, width=10)
        self.focus_delay_entry.grid(row=1, column=2, padx=(0, 16), sticky="w")

        self.design_label = ttk.Label(settings, text="Дизайн", style="FieldLabel.TLabel")
        self.design_label.grid(row=0, column=3, sticky="w")
        self.design_combo = ttk.Combobox(
            settings,
            textvariable=self.design_var,
            values=tuple(DESIGN_LABEL_TO_NAME.keys()),
            state="readonly",
            width=10,
        )
        self.design_combo.grid(row=1, column=3, padx=(0, 16), sticky="w")
        self.design_combo.bind("<<ComboboxSelected>>", self.change_design)

        self.theme_label = ttk.Label(settings, text="Тема", style="FieldLabel.TLabel")
        self.theme_label.grid(row=0, column=4, sticky="w")
        self.theme_combo = ttk.Combobox(
            settings,
            textvariable=self.theme_var,
            values=("Светлая", "Тёмная"),
            state="readonly",
            width=12,
        )
        self.theme_combo.grid(row=1, column=4, padx=(0, 16), sticky="w")
        self.theme_combo.bind("<<ComboboxSelected>>", self.change_theme)

        self.settings_hint_label = ttk.Label(settings, style="PanelHint.TLabel")
        self.settings_hint_label.grid(row=2, column=0, columnspan=5, sticky="w", pady=(10, 0))

        self.info_frame = ttk.Frame(main, style="Info.TFrame", padding=(10, 6))
        self.info_frame.pack(fill="x", pady=(0, 8))
        self.info_label = ttk.Label(
            self.info_frame,
            style="Info.TLabel",
        )
        self.info_label.pack(anchor="w")

        text_frame = ttk.LabelFrame(main, text="Текст", padding=6)
        text_frame.pack(fill="both", expand=True)
        self.text_frame = text_frame

        self.text_widget = tk.Text(
            text_frame,
            wrap="word",
            height=4,
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=6,
            font=("SF Pro Text", 12),
            background="#FBFCFE",
            undo=True,
            autoseparators=False,
            maxundo=-1,
        )
        self.text_widget.pack(side="left", fill="both", expand=True)
        self.text_widget.edit_reset()
        self.text_widget.bind("<KeyPress>", self.handle_text_keypress, add="+")
        self.text_widget.bind("<Command-z>", self.handle_undo)
        self.text_widget.bind("<Command-Z>", self.handle_undo)
        self.text_widget.bind("<Command-Cyrillic_ya>", self.handle_undo)
        self.text_widget.bind("<Command-Cyrillic_YA>", self.handle_undo)
        self.text_widget.bind("<Command-v>", self.handle_paste)
        self.text_widget.bind("<Command-V>", self.handle_paste)
        self.text_widget.bind("<Command-Cyrillic_em>", self.handle_paste)
        self.text_widget.bind("<Command-Cyrillic_EM>", self.handle_paste)
        self.text_widget.bind("<Command-a>", self.handle_select_all)
        self.text_widget.bind("<Command-A>", self.handle_select_all)
        self.text_widget.bind("<Command-Cyrillic_ef>", self.handle_select_all)
        self.text_widget.bind("<Command-Cyrillic_EF>", self.handle_select_all)
        self.text_widget.bind("<Command-c>", self.handle_copy)
        self.text_widget.bind("<Command-C>", self.handle_copy)
        self.text_widget.bind("<Command-Cyrillic_es>", self.handle_copy)
        self.text_widget.bind("<Command-Cyrillic_ES>", self.handle_copy)
        self.text_widget.bind("<Command-x>", self.handle_cut)
        self.text_widget.bind("<Command-X>", self.handle_cut)
        self.text_widget.bind("<Command-Cyrillic_che>", self.handle_cut)
        self.text_widget.bind("<Command-Cyrillic_CHE>", self.handle_cut)
        self.text_widget.bind("<<Paste>>", self.handle_paste)
        self.root.bind_all("<Command-KeyPress>", self.handle_command_shortcuts, add="+")
        self.root.bind_all("<KeyPress>", self.handle_global_keypress, add="+")
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
        self.paste_button.configure(width=8)
        self.clear_button = ttk.Button(actions, text="Очистить", command=self.clear_text, style="Secondary.TButton")
        self.clear_button.pack(side="left", padx=(6, 0))
        self.clear_button.configure(width=8)
        self.start_button = ttk.Button(actions, text="Старт", command=self.start_typing, style="Accent.TButton")
        self.start_button.pack(side="left", padx=(8, 0))
        self.start_button.configure(width=8)
        self.stop_button = ttk.Button(actions, text="Стоп", command=self.stop_typing, state="disabled", style="Secondary.TButton")
        self.stop_button.pack(side="left", padx=(6, 0))
        self.stop_button.configure(width=8)
        self.status_label = ttk.Label(actions, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(side="right")
        self.status_label.configure(width=14, anchor="e")

        self.stats_label.configure(width=12, anchor="w")
        self.focus_label.configure(width=16, anchor="e")
        self.info_label.configure(wraplength=320, justify="left")
        self.settings_hint_label.configure(wraplength=320, justify="left")

        self.apply_design_copy()
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

        style.configure("TEntry", padding=4)
        style.configure("TButton", padding=(8, 4), font=("SF Pro Text", 11))
        style.configure("TCombobox", padding=4)
        style.configure("TCheckbutton", font=("SF Pro Text", 10))
        self.style = style

    def configure_menu(self):
        menubar = tk.Menu(self.root)
        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Undo", accelerator="Cmd+Z", command=self.undo_text)
        edit_menu.add_separator()
        edit_menu.add_command(label="Select All", accelerator="Cmd+A", command=self.select_all_text)
        edit_menu.add_command(label="Copy", accelerator="Cmd+C", command=self.copy_selection)
        edit_menu.add_command(label="Cut", accelerator="Cmd+X", command=self.cut_selection)
        edit_menu.add_command(label="Paste", accelerator="Cmd+V", command=self.paste_text)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        self.root.configure(menu=menubar)

    def get_theme_tokens(self, theme_name):
        if self.design_name == "utility":
            if theme_name == "dark":
                return {
                    "root_bg": "#0C1720",
                    "panel_bg": "#0C1720",
                    "surface_bg": "#162532",
                    "text_bg": "#10202C",
                    "text_fg": "#F4FBFF",
                    "muted_fg": "#9DB3C2",
                    "entry_bg": "#10202C",
                    "entry_fg": "#F4FBFF",
                    "button_bg": "#22394B",
                    "button_fg": "#F4FBFF",
                    "insert_bg": "#F4FBFF",
                    "select_bg": "#2DD4BF",
                    "accent_fg": "#062B28",
                }
            return {
                "root_bg": "#EDF4F7",
                "panel_bg": "#EDF4F7",
                "surface_bg": "#DCE8EE",
                "text_bg": "#FFFFFF",
                "text_fg": "#10212B",
                "muted_fg": "#4A5F6B",
                "entry_bg": "#FFFFFF",
                "entry_fg": "#10212B",
                "button_bg": "#D0DEE6",
                "button_fg": "#10212B",
                "insert_bg": "#10212B",
                "select_bg": "#0F766E",
                "accent_fg": "#F8FFFE",
            }

        if self.design_name == "studio":
            if theme_name == "dark":
                return {
                    "root_bg": "#171413",
                    "panel_bg": "#171413",
                    "surface_bg": "#2B221D",
                    "text_bg": "#120F0E",
                    "text_fg": "#FAF5EF",
                    "muted_fg": "#D0BFAF",
                    "entry_bg": "#1B1512",
                    "entry_fg": "#FAF5EF",
                    "button_bg": "#4B3A31",
                    "button_fg": "#FAF5EF",
                    "insert_bg": "#FAF5EF",
                    "select_bg": "#F59E0B",
                    "accent_fg": "#241200",
                }
            return {
                "root_bg": "#F6F1E8",
                "panel_bg": "#F6F1E8",
                "surface_bg": "#E7D7BD",
                "text_bg": "#FFFDF8",
                "text_fg": "#2A2118",
                "muted_fg": "#6F5A45",
                "entry_bg": "#FFFDF8",
                "entry_fg": "#2A2118",
                "button_bg": "#D8C3A1",
                "button_fg": "#2A2118",
                "insert_bg": "#2A2118",
                "select_bg": "#B45309",
                "accent_fg": "#FFF7ED",
            }

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
                "accent_fg": "#F9FAFB",
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
            "accent_fg": "#111827",
        }

    def apply_theme(self):
        self.theme_tokens = self.get_theme_tokens(self.theme_name)
        colors = self.theme_tokens
        label_font = ("SF Pro Text", 11)
        hero_title_font = ("SF Pro Display", 16, "bold")
        hero_subtitle_font = ("SF Pro Text", 11)
        topbar_title_font = ("SF Pro Display", 13, "bold")
        section_title_font = ("SF Pro Text", 11, "bold")
        button_font = ("SF Pro Text", 11)

        self.style.configure("TFrame", background=colors["root_bg"])
        self.style.configure("TLabelframe", background=colors["panel_bg"], bordercolor=colors["surface_bg"])
        self.style.configure(
            "TLabelframe.Label",
            background=colors["panel_bg"],
            foreground=colors["text_fg"],
            font=section_title_font,
        )
        self.style.configure("TLabel", background=colors["root_bg"], foreground=colors["text_fg"], font=label_font)
        self.style.configure("Topbar.TFrame", background=colors["root_bg"])
        self.style.configure("TopbarTitle.TLabel", background=colors["root_bg"], foreground=colors["text_fg"], font=topbar_title_font)
        self.style.configure("TopbarMeta.TLabel", background=colors["root_bg"], foreground=colors["muted_fg"], font=("SF Pro Text", 11))
        self.style.configure("ControlLabel.TLabel", background=colors["root_bg"], foreground=colors["muted_fg"], font=("SF Pro Text", 10))
        self.style.configure("FieldLabel.TLabel", background=colors["panel_bg"], foreground=colors["muted_fg"], font=("SF Pro Text", 10))
        self.style.configure("Hero.TFrame", background=colors["surface_bg"])
        self.style.configure("HeroEyebrow.TLabel", background=colors["surface_bg"], foreground=colors["select_bg"], font=("SF Pro Text", 10, "bold"))
        self.style.configure("HeroTitle.TLabel", background=colors["surface_bg"], foreground=colors["text_fg"], font=hero_title_font)
        self.style.configure("HeroSubtitle.TLabel", background=colors["surface_bg"], foreground=colors["muted_fg"], font=hero_subtitle_font)
        self.style.configure("HeroFlow.TLabel", background=colors["surface_bg"], foreground=colors["text_fg"], font=("SF Pro Text", 11))
        self.style.configure("Info.TFrame", background=colors["surface_bg"])
        self.style.configure("Info.TLabel", background=colors["surface_bg"], foreground=colors["muted_fg"], font=("SF Pro Text", 11))
        self.style.configure("PanelHint.TLabel", background=colors["panel_bg"], foreground=colors["muted_fg"], font=("SF Pro Text", 10))
        self.style.configure("Status.TLabel", background=colors["root_bg"], foreground=colors["muted_fg"], font=("SF Pro Text", 11))
        self.style.configure("Meta.TLabel", background=colors["root_bg"], foreground=colors["muted_fg"], font=("SF Pro Text", 10))
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
        self.style.configure("TButton", background=colors["button_bg"], foreground=colors["button_fg"], font=button_font)
        self.style.configure("Secondary.TButton", background=colors["button_bg"], foreground=colors["button_fg"], font=("SF Pro Text", 11))
        self.style.configure("Accent.TButton", background=colors["select_bg"], foreground=colors["accent_fg"], font=button_font)
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
            font=("SF Pro Text", 12),
            spacing1=1,
            spacing3=2,
        )

    def apply_design_copy(self):
        copies = {
            "utility": {
                "platform": "macOS workspace",
                "badge": "UTILITY",
                "title": "Рабочая панель для быстрого ввода",
                "subtitle": "Текст в центре, параметры рядом, запуск без лишних промежуточных блоков.",
                "flow": "Cmd+V -> проверь задержки -> переведи фокус -> Старт",
                "settings_title": "Панель ввода",
                "settings_hint": "Минимум интерфейса: три числа и один основной action.",
                "info": "Utility-пресет прячет декоративные блоки и оставляет только то, что нужно перед запуском.",
                "text_title": "Текст для набора",
                "start_text": "Старт",
            },
            "studio": {
                "platform": "macOS flow mode",
                "badge": "STUDIO",
                "title": "Текст -> фокус -> печать",
                "subtitle": "Режим с более выразительной иерархией, крупным hero и визуальным сценарием запуска.",
                "flow": "1. Вставь черновик   2. Уйди в целевое окно   3. Дай приложению допечатать Unicode посимвольно",
                "settings_title": "Режиссура тайминга",
                "settings_hint": "Подстрой старт, паузу после фокуса и ритм печати под целевое приложение.",
                "info": "Studio подчёркивает сценарий: сначала подготовка, затем переключение фокуса, потом посимвольный ввод.",
                "text_title": "Черновик сцены",
                "start_text": "Старт",
            },
            "native": {
                "platform": "macOS edition",
                "badge": "NATIVE",
                "title": "Unicode Text Typer",
                "subtitle": "Печатай Unicode напрямую, без переключения раскладки. Вставляй текст из буфера и запускай набор в пару кликов.",
                "flow": "Cmd+V работает в редакторе. После старта приложение дождётся внешнего фокуса и начнёт печать.",
                "settings_title": "Параметры",
                "settings_hint": "Сбалансированная раскладка без сильного визуального давления.",
                "info": "Cmd+V и кнопка «Вставить» работают независимо от текущей раскладки клавиатуры. После «Старт» приложение ждёт переключения фокуса и только потом начинает печать.",
                "text_title": "Текст",
                "start_text": "Старт",
            },
        }
        copy = copies[self.design_name]
        self.platform_label.configure(text=copy["platform"])
        self.hero_badge_label.configure(text=copy["badge"])
        self.title_label.configure(text=copy["title"])
        self.subtitle_label.configure(text=copy["subtitle"])
        self.hero_flow_label.configure(text=copy["flow"])
        self.settings_frame.configure(text=copy["settings_title"])
        self.settings_hint_label.configure(text=copy["settings_hint"])
        self.info_label.configure(text=copy["info"])
        self.text_frame.configure(text=copy["text_title"])
        self.start_button.configure(text=copy["start_text"])

    def change_design(self, event=None):
        self.design_name = DESIGN_LABEL_TO_NAME.get(self.design_var.get(), "native")
        self.apply_design_copy()
        self.apply_theme()
        self.apply_layout_mode()
        self.save_settings()

    def change_theme(self, event=None):
        self.theme_name = "dark" if self.theme_var.get() == "Тёмная" else "light"
        self.apply_theme()
        self.save_settings()

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

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
        self.design_name = data.get("design", self.design_name)
        self.design_var.set(DESIGN_NAME_TO_LABEL.get(self.design_name, "Native"))
        self.compact_var.set(bool(data.get("compact", False)))

    def save_settings(self):
        data = {
            "delay": self.delay_var.get().strip(),
            "start_after": self.start_after_var.get().strip(),
            "focus_delay": self.focus_delay_var.get().strip(),
            "theme": self.theme_name,
            "design": self.design_name,
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

    def _on_root_resize(self, event):
        if event.widget is not self.root:
            return
        should_collapse = event.width < self.design_theme_collapse_width
        if should_collapse == self.design_theme_collapsed:
            return
        self.design_theme_collapsed = should_collapse
        if should_collapse:
            self.design_label.grid_remove()
            self.design_combo.grid_remove()
            self.theme_label.grid_remove()
            self.theme_combo.grid_remove()
        else:
            self.design_label.grid()
            self.design_combo.grid()
            self.theme_label.grid()
            self.theme_combo.grid()

    def apply_layout_mode(self):
        for widget in (
            self.topbar_frame,
            self.hero_frame,
            self.settings_frame,
            self.info_frame,
            self.text_frame,
            self.meta_frame,
            self.actions_frame,
        ):
            widget.pack_forget()

        dense = self.compact_var.get()
        gap = 4 if dense else 8
        self.main_frame.configure(padding=6 if dense else 8)
        self.topbar_frame.pack(fill="x", pady=(0, gap))
        if not dense:
            self.hero_frame.pack(fill="x", pady=(0, 8))
        self.settings_frame.pack(fill="x", pady=(0, gap))
        if not dense:
            self.info_frame.pack(fill="x", pady=(0, gap))
        self.text_frame.pack(fill="both", expand=True)
        self.meta_frame.pack(fill="x", pady=(gap, 0))
        self.actions_frame.pack(fill="x", pady=(gap, 0))

    def capture_app_identity(self):
        if self.app_process_name is None:
            self.app_process_name = self.get_frontmost_app_name()

    def get_frontmost_app_name(self):
        script = 'tell application "System Events" to get name of first application process whose frontmost is true'
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception:
            return ""
        return result.stdout.strip()

    def is_external_focus(self):
        current_name = self.get_frontmost_app_name()
        if not current_name:
            return False, "Фокус: не удалось определить"

        app_name = self.app_process_name or current_name
        if current_name == app_name:
            return False, "Фокус: окно приложения"

        return True, f"Фокус: {current_name}"

    def start_focus_polling(self):
        self.update_focus_status()

    def update_focus_status(self):
        if not self.focus_probe_inflight:
            self.focus_probe_inflight = True
            threading.Thread(target=self.refresh_focus_status_async, daemon=True).start()
        self.focus_poll_job = self.root.after(900, self.update_focus_status)

    def refresh_focus_status_async(self):
        _, label = self.is_external_focus()

        def apply_focus_status():
            self.focus_probe_inflight = False
            self.focus_var.set(label)

        try:
            self.root.after(0, apply_focus_status)
        except tk.TclError:
            self.focus_probe_inflight = False

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

    def wait_for_target_focus(self):
        self.set_status("Ожидание целевого фокуса...")
        while not self.stop_event.is_set():
            found, label = self.is_external_focus()
            self.root.after(0, self.focus_var.set, label)
            if found:
                return label.replace("Фокус: ", "", 1)
            if self.stop_event.wait(0.2):
                break
        return None

    def sleep_with_stop(self, seconds, status_text=None):
        if seconds <= 0:
            return not self.stop_event.is_set()
        if status_text:
            self.set_status(status_text)
        return not self.stop_event.wait(seconds)

    def clear_text(self):
        if self.is_typing:
            return
        self.push_undo_separator()
        self.text_widget.delete("1.0", "end")
        self.push_undo_separator()
        self.update_text_stats()
        self.set_status("Текст очищен")

    def select_all_text(self):
        self.text_widget.focus_set()
        self.text_widget.tag_remove("sel", "1.0", "end")
        self.text_widget.tag_add("sel", "1.0", "end-1c")
        self.text_widget.mark_set("insert", "end-1c")
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
        self.push_undo_separator()
        self.text_widget.delete("sel.first", "sel.last")
        self.push_undo_separator()
        self.set_status("Текст вырезан")

    def handle_paste(self, event=None):
        self.paste_text()
        return "break"

    def handle_undo(self, event=None):
        self.undo_text()
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

    def _shortcut_action_for_event(self, event):
        focused_widget = self.root.focus_get()
        if focused_widget is not self.text_widget:
            return None

        shortcuts = {
            SELECT_ALL_KEYCODE: self.select_all_text,
            COPY_KEYCODE: self.copy_selection,
            CUT_KEYCODE: self.cut_selection,
            PASTE_KEYCODE: self.paste_text,
        }
        return shortcuts.get(event.keycode)

    def handle_command_shortcuts(self, event):
        action = self._shortcut_action_for_event(event)
        if action is None:
            return None
        action()
        return "break"

    def handle_global_keypress(self, event):
        action = self._shortcut_action_for_event(event)
        if action is None:
            return None
        if any(event.state & mask for mask in COMMAND_MASKS):
            action()
            return "break"
        return None

    def handle_text_keypress(self, event):
        if self.is_typing:
            return None

        if any(event.state & mask for mask in COMMAND_MASKS):
            return None

        if not self.is_text_edit_event(event):
            return None

        now = time.monotonic()
        if now - self.last_edit_time >= UNDO_GROUP_DELAY_MS / 1000:
            self.push_undo_separator()

        self.last_edit_time = now

        if event.keysym in {"space", "Return", "KP_Enter", "Tab"}:
            self.root.after_idle(self.commit_undo_group)
        else:
            self.schedule_undo_group()
        return None

    def is_text_edit_event(self, event):
        if event.keysym in {"BackSpace", "Delete", "space", "Return", "KP_Enter", "Tab"}:
            return True
        return bool(event.char and event.char >= " ")

    def schedule_undo_group(self):
        self.cancel_undo_group_timer()
        self.undo_group_job = self.root.after(UNDO_GROUP_DELAY_MS, self.commit_undo_group)

    def cancel_undo_group_timer(self):
        if self.undo_group_job is not None:
            self.root.after_cancel(self.undo_group_job)
            self.undo_group_job = None

    def commit_undo_group(self):
        self.undo_group_job = None
        try:
            self.text_widget.edit_separator()
        except tk.TclError:
            pass

    def undo_text(self):
        if self.is_typing:
            return

        self.cancel_undo_group_timer()

        try:
            self.text_widget.edit_undo()
        except tk.TclError:
            self.set_status("Нечего отменять")
            return

        self.update_text_stats()
        self.text_widget.focus_set()
        self.set_status("Последнее изменение отменено")

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

        self.push_undo_separator()
        try:
            self.text_widget.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        self.text_widget.insert("insert", clipboard_text)
        self.push_undo_separator()
        self.update_text_stats()
        self.text_widget.focus_set()
        self.set_status("Текст вставлен из буфера")

    def push_undo_separator(self):
        self.cancel_undo_group_timer()
        self.commit_undo_group()

    def post_keycode(self, keycode):
        down = CGEventCreateKeyboardEvent(None, keycode, True)
        up = CGEventCreateKeyboardEvent(None, keycode, False)
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)

    def post_unicode_char(self, ch):
        down = CGEventCreateKeyboardEvent(None, 0, True)
        CGEventKeyboardSetUnicodeString(down, len(ch), ch)

        up = CGEventCreateKeyboardEvent(None, 0, False)
        CGEventKeyboardSetUnicodeString(up, len(ch), ch)

        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)

    def validate_float(self, value, name):
        try:
            x = float(value)
            if x < 0:
                raise ValueError
            return x
        except Exception:
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
        self.stop_event.clear()
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.save_settings()

        t = threading.Thread(
            target=self.run_action,
            args=(text, delay, start_after, focus_delay),
            daemon=True,
        )
        t.start()

    def stop_typing(self):
        if self.is_typing:
            self.stop_event.set()
            self.set_status("Запрошена остановка...")

    def type_text(self, text, delay):
        for ch in text:
            if self.stop_event.is_set():
                return False

            if ch == "\n":
                self.post_keycode(ENTER_KEYCODE)
            elif ch == "\t":
                self.post_keycode(TAB_KEYCODE)
            elif ch == "\r":
                continue
            else:
                self.post_unicode_char(ch)

            if self.stop_event.wait(delay):
                return False

        return True

    def run_action(self, text, delay, start_after, focus_delay):
        try:
            if not self.sleep_with_stop(start_after, f"Старт через {start_after:.1f} сек"):
                self.set_status("Остановлено")
                return

            if self.stop_event.is_set():
                self.set_status("Остановлено")
                return

            focus_name = self.wait_for_target_focus()
            if focus_name is None:
                self.set_status("Остановлено")
                return

            if not self.sleep_with_stop(focus_delay, f"Фокус найден: {focus_name}. Старт через {focus_delay:.1f} сек"):
                self.set_status("Остановлено")
                return

            self.set_status("Посимвольный ввод...")
            ok = self.type_text(text, delay)

            if ok:
                self.set_status("Готово")
            else:
                self.set_status("Остановлено")

        except Exception as e:
            self.set_status(f"Ошибка: {e}")
        finally:
            self.is_typing = False
            self.stop_event.clear()
            self.root.after(0, self.reset_buttons)

    def reset_buttons(self):
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")


def main():
    root = tk.Tk()
    TextTyperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
