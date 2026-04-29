"""
Unicode Auto Typer (macOS) — два режима автоматизации в одном окне.

Вкладки:
  - «Автомат»: проигрывает JSON-сценарий (клики + печать Unicode).
    Калибровка координат: жмёшь «Калибровать поле», вводишь имя,
    наводишь курсор в нужное место и либо ждёшь 3 секунды, либо
    нажимаешь F8 — координата захватится мгновенно.
    Кнопка «Сгенерировать из data.json» собирает сценарий из
    откалиброванных полей + структурированных данных (см. kv1/data.json).

  - «Помощник»: загружает плоский список текстовых блоков и кладёт
    их в системный буфер обмена один за другим. Ты сам жмёшь Cmd+V в
    форме, Tab между полями, и F9 (или кнопку «Следующий») для
    перехода к следующему блоку. Координаты не нужны вообще.

Глобальные горячие клавиши (F8/F9) работают через CGEventTap. Им
требуется разрешение Accessibility — то же, что и обычной печати.
Если разрешения нет, горячие клавиши просто не активируются — все
действия по-прежнему доступны кнопками в окне.

Формат сценария (тот же, что был раньше):
  {
    "fields": {"имя": [x, y]},
    "steps": [
      {"action": "click", "field": "имя"},
      {"action": "click", "x": 100, "y": 200},
      {"action": "type", "text": "Привет"},
      {"action": "key", "name": "tab"},
      {"action": "sleep", "seconds": 0.5}
    ]
  }
"""

import json
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from Quartz import (
    CGEventCreate,
    CGEventCreateKeyboardEvent,
    CGEventCreateMouseEvent,
    CGEventGetIntegerValueField,
    CGEventGetLocation,
    CGEventKeyboardSetUnicodeString,
    CGEventMaskBit,
    CGEventPost,
    CGEventTapCreate,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    kCGEventKeyDown,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseUp,
    kCGEventMouseMoved,
    kCGEventTapOptionListenOnly,
    kCGHIDEventTap,
    kCGHeadInsertEventTap,
    kCGKeyboardEventKeycode,
    kCGMouseButtonLeft,
    kCGSessionEventTap,
)

try:
    from CoreFoundation import (
        CFRunLoopAddSource,
        CFRunLoopGetCurrent,
        CFRunLoopRun,
        CFRunLoopStop,
        kCFRunLoopDefaultMode,
    )

    _COREFOUNDATION_OK = True
except Exception:
    _COREFOUNDATION_OK = False


KEY_NAMES = {
    "tab": 48,
    "return": 36,
    "enter": 36,
    "esc": 53,
    "escape": 53,
    "space": 49,
    "backspace": 51,
    "delete": 117,
    "up": 126,
    "down": 125,
    "left": 123,
    "right": 124,
}

HOTKEY_F8 = 100
HOTKEY_F9 = 101

CONFIG_PATH = Path(__file__).with_name("macos_auto_settings.json")


class MacosAutomation:
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

    def type_text(self, text, char_delay=0.0, stop_event=None):
        for ch in text:
            if stop_event is not None and stop_event.is_set():
                return False
            if ch == "\n":
                self.post_keycode(KEY_NAMES["return"])
            elif ch == "\t":
                self.post_keycode(KEY_NAMES["tab"])
            elif ch == "\r":
                continue
            else:
                self.post_unicode_char(ch)
            if char_delay > 0:
                if stop_event is not None and stop_event.wait(char_delay):
                    return False
                if stop_event is None:
                    time.sleep(char_delay)
        return True

    def click(self, x, y):
        point = (float(x), float(y))
        move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, point, kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, move)
        down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, point, kCGMouseButtonLeft)
        up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, point, kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)

    def get_mouse_location(self):
        event = CGEventCreate(None)
        loc = CGEventGetLocation(event)
        return (loc.x, loc.y)

    def press_key(self, name):
        keycode = KEY_NAMES.get(name.lower())
        if keycode is None:
            raise ValueError(f"Неизвестная клавиша: {name}")
        self.post_keycode(keycode)


class HotkeyMonitor:
    """Глобальные горячие клавиши через CGEventTap.

    Требует Accessibility-разрешения. Если их нет, start() отдаёт
    False и колбэки просто не вызываются.
    """

    def __init__(self):
        self.callbacks = {}
        self._lock = threading.Lock()
        self._tap = None
        self._loop = None
        self._thread = None
        self._active = False

    def register(self, keycode, callback):
        with self._lock:
            self.callbacks[int(keycode)] = callback

    def start(self):
        if self._thread is not None or not _COREFOUNDATION_OK:
            return self._active
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Дать потоку шанс зарегистрировать tap
        time.sleep(0.1)
        return self._active

    def _run(self):
        mask = CGEventMaskBit(kCGEventKeyDown)

        def tap_callback(proxy, type_, event, refcon):
            try:
                kc = int(CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode))
                with self._lock:
                    handler = self.callbacks.get(kc)
                if handler is not None:
                    try:
                        handler()
                    except Exception:
                        pass
            except Exception:
                pass
            return event

        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
            mask,
            tap_callback,
            None,
        )
        if tap is None:
            self._active = False
            return
        self._tap = tap
        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        self._loop = CFRunLoopGetCurrent()
        CFRunLoopAddSource(self._loop, source, kCFRunLoopDefaultMode)
        CGEventTapEnable(tap, True)
        self._active = True
        try:
            CFRunLoopRun()
        except Exception:
            pass

    def stop(self):
        if self._loop is not None:
            try:
                CFRunLoopStop(self._loop)
            except Exception:
                pass


def extract_blocks_from_data(data):
    """data.json -> плоский список блоков для clipboard-помощника."""
    blocks = []
    for i, obj in enumerate(data.get("objects", []), start=1):
        blocks.append({"label": f"Объект {i}: имя", "text": obj.get("name", "")})
        blocks.append({"label": f"Объект {i}: класс", "text": obj.get("class", "")})
        blocks.append({"label": f"Объект {i}: описание", "text": obj.get("desc", "")})
    for i, fn in enumerate(data.get("functions", []), start=1):
        blocks.append({"label": f"Функция {i}: имя", "text": fn.get("name", "")})
        blocks.append({"label": f"Функция {i}: описание", "text": fn.get("desc", "")})
    for i, tool in enumerate(data.get("tools", []), start=1):
        blocks.append({"label": f"Инструмент {i}: описание", "text": tool.get("desc", "")})
    return blocks


def extract_blocks_from_steps(steps):
    """Из списка шагов оставляем только те, что вводят текст."""
    blocks = []
    counter = 0
    for step in steps:
        if isinstance(step, dict) and step.get("action") == "type":
            counter += 1
            text = step.get("text", "")
            label = f"Блок {counter}"
            if text:
                preview = text if len(text) <= 40 else text[:37] + "..."
                label = f"Блок {counter}: {preview}"
            blocks.append({"label": label, "text": text})
    return blocks


def generate_steps_from_data(profile, data):
    """Те же правила, что в kv1/make_scenario.py: Tab внутри строки,
    линейная экстраполяция между row1 и row2 для row3+.
    """

    def get(name):
        coords = profile.get(name)
        if not isinstance(coords, (list, tuple)) or len(coords) != 2:
            return None
        return float(coords[0]), float(coords[1])

    def row_coord(row1, row2, idx):
        if idx == 0:
            return row1
        if row2 is None:
            raise ValueError(
                "Нужно больше одной строки, но row2 не калибровано. "
                "Откалибруй вторую строку."
            )
        return (row1[0] + (row2[0] - row1[0]) * idx, row1[1] + (row2[1] - row1[1]) * idx)

    def click(point):
        return {"action": "click", "x": round(point[0]), "y": round(point[1])}

    def type_text(text):
        return {"action": "type", "text": text}

    def press(name):
        return {"action": "key", "name": name}

    def sleep_step(seconds):
        return {"action": "sleep", "seconds": seconds}

    steps = []

    objects = data.get("objects", []) or []
    if objects:
        row1 = get("obj_row1_name")
        if row1 is None:
            raise ValueError("Поле «obj_row1_name» не калибровано.")
        row2 = get("obj_row2_name")
        for i, obj in enumerate(objects):
            point = row_coord(row1, row2, i)
            steps.append(click(point))
            steps.append(sleep_step(0.05))
            steps.append(type_text(obj.get("name", "")))
            steps.append(press("tab"))
            steps.append(type_text(obj.get("class", "")))
            steps.append(press("tab"))
            steps.append(type_text(obj.get("desc", "")))

    functions = data.get("functions", []) or []
    if functions:
        row1 = get("func_row1_name")
        if row1 is None:
            raise ValueError("Поле «func_row1_name» не калибровано.")
        row2 = get("func_row2_name")
        for i, fn in enumerate(functions):
            point = row_coord(row1, row2, i)
            steps.append(click(point))
            steps.append(sleep_step(0.05))
            steps.append(type_text(fn.get("name", "")))
            steps.append(press("tab"))
            steps.append(type_text(fn.get("desc", "")))

    tools = data.get("tools", []) or []
    if tools:
        row1 = get("tool_row1_desc")
        if row1 is None:
            raise ValueError("Поле «tool_row1_desc» не калибровано.")
        row2 = get("tool_row2_desc")
        for i, tool in enumerate(tools):
            point = row_coord(row1, row2, i)
            steps.append(click(point))
            steps.append(sleep_step(0.05))
            steps.append(type_text(tool.get("desc", "")))

    return steps


class AutoApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Unicode Auto Typer")
        self.root.geometry("780x680")
        self.root.minsize(680, 540)

        self.engine = MacosAutomation()
        self.is_running = False
        self.stop_event = threading.Event()
        self.calibration_target = None

        self.scenario_path_var = tk.StringVar(value="")
        self.start_delay_var = tk.StringVar(value="3.0")
        self.step_delay_var = tk.StringVar(value="0.05")
        self.char_delay_var = tk.StringVar(value="0.0")
        self.status_var = tk.StringVar(value="Готово")
        self.hotkey_status_var = tk.StringVar(value="Горячие клавиши: запуск...")

        self.helper_blocks = []
        self.helper_index = 0
        self.helper_label_var = tk.StringVar(value="Загрузи блоки в режиме «Помощник».")
        self.helper_progress_var = tk.StringVar(value="0/0")
        self.helper_active = False

        self.scenario = {"fields": {}, "steps": []}

        self.hotkeys = HotkeyMonitor()
        self.hotkeys.register(HOTKEY_F8, self._on_f8)
        self.hotkeys.register(HOTKEY_F9, self._on_f9)

        self.load_settings()
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(50, self._init_hotkeys)

    # ---------- UI ----------

    def build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        style = ttk.Style()
        try:
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass
        style.configure("TButton", padding=(8, 4), font=("SF Pro Text", 11))
        style.configure("TLabel", font=("SF Pro Text", 11))
        style.configure("FieldLabel.TLabel", foreground="#555", font=("SF Pro Text", 10))

        topbar = ttk.Frame(main)
        topbar.pack(fill="x", pady=(0, 6))
        ttk.Label(topbar, text="Unicode Auto Typer", font=("SF Pro Display", 14, "bold")).pack(anchor="w")
        ttk.Label(
            topbar,
            text="macOS · F8 — записать координату · F9 — следующий блок",
            style="FieldLabel.TLabel",
        ).pack(anchor="w")

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True)

        auto_tab = ttk.Frame(notebook, padding=8)
        helper_tab = ttk.Frame(notebook, padding=8)
        notebook.add(auto_tab, text="Автомат")
        notebook.add(helper_tab, text="Помощник")

        self._build_auto_tab(auto_tab)
        self._build_helper_tab(helper_tab)

        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, textvariable=self.status_var, style="FieldLabel.TLabel").pack(side="left")
        ttk.Label(bottom, textvariable=self.hotkey_status_var, style="FieldLabel.TLabel").pack(side="right")

    def _build_auto_tab(self, parent):
        scenario_frame = ttk.LabelFrame(parent, text="Сценарий", padding=8)
        scenario_frame.pack(fill="x", pady=(0, 8))

        path_row = ttk.Frame(scenario_frame)
        path_row.pack(fill="x")
        ttk.Label(path_row, text="Файл:", style="FieldLabel.TLabel").pack(side="left")
        ttk.Entry(path_row, textvariable=self.scenario_path_var).pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(path_row, text="Открыть", command=self.open_scenario).pack(side="left")
        ttk.Button(path_row, text="Сохранить", command=self.save_scenario).pack(side="left", padx=(6, 0))
        ttk.Button(path_row, text="Новый", command=self.new_scenario).pack(side="left", padx=(6, 0))

        fields_frame = ttk.LabelFrame(parent, text="Поля (координаты)", padding=8)
        fields_frame.pack(fill="x", pady=(0, 8))

        tree_row = ttk.Frame(fields_frame)
        tree_row.pack(fill="x")
        columns = ("name", "x", "y")
        self.fields_tree = ttk.Treeview(tree_row, columns=columns, show="headings", height=5)
        self.fields_tree.heading("name", text="Имя")
        self.fields_tree.heading("x", text="X")
        self.fields_tree.heading("y", text="Y")
        self.fields_tree.column("name", width=240, anchor="w")
        self.fields_tree.column("x", width=80, anchor="e")
        self.fields_tree.column("y", width=80, anchor="e")
        self.fields_tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(tree_row, orient="vertical", command=self.fields_tree.yview)
        scroll.pack(side="right", fill="y")
        self.fields_tree.configure(yscrollcommand=scroll.set)

        tree_actions = ttk.Frame(fields_frame)
        tree_actions.pack(fill="x", pady=(6, 0))
        ttk.Button(tree_actions, text="Калибровать поле", command=self.calibrate_field).pack(side="left")
        ttk.Button(tree_actions, text="Перезаписать", command=self.recalibrate_field).pack(side="left", padx=(6, 0))
        ttk.Button(tree_actions, text="Удалить", command=self.delete_field).pack(side="left", padx=(6, 0))
        ttk.Button(tree_actions, text="Сгенерировать из data.json...", command=self.generate_from_data).pack(side="left", padx=(12, 0))

        steps_frame = ttk.LabelFrame(parent, text="Шаги (JSON)", padding=8)
        steps_frame.pack(fill="both", expand=True, pady=(0, 8))
        self.steps_text = tk.Text(
            steps_frame,
            wrap="none",
            height=8,
            font=("SF Mono", 11),
            undo=True,
            relief="flat",
            background="#FAFBFD",
        )
        self.steps_text.pack(fill="both", expand=True)

        params = ttk.LabelFrame(parent, text="Параметры", padding=8)
        params.pack(fill="x", pady=(0, 8))
        ttk.Label(params, text="Старт через, сек", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(params, textvariable=self.start_delay_var, width=8).grid(row=1, column=0, padx=(0, 16), sticky="w")
        ttk.Label(params, text="Между шагами, сек", style="FieldLabel.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Entry(params, textvariable=self.step_delay_var, width=8).grid(row=1, column=1, padx=(0, 16), sticky="w")
        ttk.Label(params, text="Между символами, сек", style="FieldLabel.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Entry(params, textvariable=self.char_delay_var, width=8).grid(row=1, column=2, padx=(0, 16), sticky="w")

        actions = ttk.Frame(parent)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="Старт", command=self.start_run)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Стоп", command=self.stop_run, state="disabled")
        self.stop_button.pack(side="left", padx=(6, 0))

        self.refresh_fields_table()
        self.refresh_steps_text()

    def _build_helper_tab(self, parent):
        info = ttk.Label(
            parent,
            text=(
                "1. Загрузи блоки текста (из data.json или текущего сценария).\n"
                "2. Нажми «Старт помощника» — первый блок попадёт в буфер обмена.\n"
                "3. В целевой форме делай Cmd+V → Tab → F9 (или кнопка «Следующий»)."
            ),
            style="FieldLabel.TLabel",
            justify="left",
        )
        info.pack(fill="x", pady=(0, 8))

        source_row = ttk.Frame(parent)
        source_row.pack(fill="x", pady=(0, 8))
        ttk.Button(source_row, text="Загрузить из data.json...", command=self.helper_load_data).pack(side="left")
        ttk.Button(source_row, text="Из текущего сценария", command=self.helper_load_from_scenario).pack(side="left", padx=(6, 0))

        list_frame = ttk.LabelFrame(parent, text="Блоки", padding=8)
        list_frame.pack(fill="both", expand=True, pady=(0, 8))

        list_row = ttk.Frame(list_frame)
        list_row.pack(fill="both", expand=True)

        self.helper_listbox = tk.Listbox(list_row, height=14, font=("SF Mono", 11), activestyle="dotbox")
        self.helper_listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_row, orient="vertical", command=self.helper_listbox.yview)
        scroll.pack(side="right", fill="y")
        self.helper_listbox.configure(yscrollcommand=scroll.set)
        self.helper_listbox.bind("<Double-Button-1>", self._helper_jump_to_selected)

        controls = ttk.Frame(parent)
        controls.pack(fill="x")
        self.helper_start_button = ttk.Button(controls, text="Старт помощника", command=self.helper_start)
        self.helper_start_button.pack(side="left")
        self.helper_next_button = ttk.Button(controls, text="Следующий (F9)", command=self.helper_next, state="disabled")
        self.helper_next_button.pack(side="left", padx=(6, 0))
        self.helper_stop_button = ttk.Button(controls, text="Стоп", command=self.helper_stop, state="disabled")
        self.helper_stop_button.pack(side="left", padx=(6, 0))

        ttk.Label(controls, textvariable=self.helper_progress_var, style="FieldLabel.TLabel").pack(side="right")
        ttk.Label(controls, textvariable=self.helper_label_var, style="FieldLabel.TLabel").pack(side="right", padx=(0, 12))

    # ---------- helpers ----------

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

    def _init_hotkeys(self):
        ok = self.hotkeys.start()
        if ok:
            self.hotkey_status_var.set("Горячие клавиши: F8 / F9 активны")
        else:
            self.hotkey_status_var.set(
                "Горячие клавиши недоступны (нужен Accessibility-доступ)"
            )

    def refresh_fields_table(self):
        self.fields_tree.delete(*self.fields_tree.get_children())
        for name, coords in self.scenario.get("fields", {}).items():
            x, y = coords
            self.fields_tree.insert("", "end", values=(name, int(x), int(y)))

    def refresh_steps_text(self):
        self.steps_text.delete("1.0", "end")
        steps = self.scenario.get("steps", [])
        self.steps_text.insert("1.0", json.dumps(steps, ensure_ascii=False, indent=2))

    def parse_steps_text(self):
        raw = self.steps_text.get("1.0", "end-1c").strip()
        if not raw:
            return []
        steps = json.loads(raw)
        if not isinstance(steps, list):
            raise ValueError("Шаги должны быть JSON-массивом")
        return steps

    # ---------- Auto tab ----------

    def new_scenario(self):
        if self.is_running:
            return
        self.scenario = {"fields": {}, "steps": []}
        self.scenario_path_var.set("")
        self.refresh_fields_table()
        self.refresh_steps_text()
        self.set_status("Новый сценарий")

    def open_scenario(self):
        if self.is_running:
            return
        path = filedialog.askopenfilename(
            title="Открыть сценарий",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл: {exc}")
            return
        if not isinstance(data, dict):
            messagebox.showerror("Ошибка", "Корень JSON должен быть объектом")
            return
        fields = data.get("fields", {}) or {}
        normalized_fields = {}
        for name, coords in fields.items():
            if isinstance(coords, (list, tuple)) and len(coords) == 2:
                normalized_fields[name] = [float(coords[0]), float(coords[1])]
        self.scenario = {
            "fields": normalized_fields,
            "steps": data.get("steps", []) or [],
        }
        self.scenario_path_var.set(str(Path(path).resolve()))
        self.refresh_fields_table()
        self.refresh_steps_text()
        n_steps = len(self.scenario.get("steps", []))
        if n_steps == 0:
            self.set_status(f"Загружено: {Path(path).name} — 0 шагов")
        else:
            self.set_status(f"Загружено: {Path(path).name} ({n_steps} шагов)")

    def save_scenario(self):
        try:
            steps = self.parse_steps_text()
        except Exception as exc:
            messagebox.showerror("Ошибка JSON", f"Шаги не парсятся как JSON: {exc}")
            return
        self.scenario["steps"] = steps
        path = self.scenario_path_var.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(
                title="Сохранить сценарий",
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
            )
            if not path:
                return
            path = str(Path(path).resolve())
            self.scenario_path_var.set(path)
        try:
            Path(path).write_text(
                json.dumps(self.scenario, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {exc}")
            return
        self.set_status(f"Сохранено: {Path(path).resolve()}")

    def calibrate_field(self):
        if self.is_running:
            return
        name = simpledialog.askstring("Калибровка", "Имя поля:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in self.scenario.get("fields", {}):
            if not messagebox.askyesno("Перезапись", f"Поле «{name}» уже есть. Перезаписать?"):
                return
        self._record_position_into(name)

    def recalibrate_field(self):
        if self.is_running:
            return
        sel = self.fields_tree.selection()
        if not sel:
            messagebox.showinfo("Выбор", "Сначала выдели поле в таблице.")
            return
        name = self.fields_tree.item(sel[0], "values")[0]
        self._record_position_into(name)

    def _record_position_into(self, name):
        self.calibration_target = name
        delay = 3
        win = tk.Toplevel(self.root)
        win.title("Калибровка")
        win.geometry("440x180")
        win.attributes("-topmost", True)
        win.transient(self.root)
        label = ttk.Label(
            win,
            text="",
            padding=20,
            font=("SF Pro Text", 13),
            justify="center",
            anchor="center",
        )
        label.pack(expand=True, fill="both")
        coord_var = tk.StringVar(value="курсор: —")
        ttk.Label(win, textvariable=coord_var, font=("SF Mono", 10)).pack()
        actions = ttk.Frame(win)
        actions.pack(pady=(4, 8))
        ttk.Button(actions, text="Записать сейчас", command=lambda: capture(immediate=True)).pack(side="left")
        ttk.Button(actions, text="Отмена", command=lambda: cancel()).pack(side="left", padx=(6, 0))

        state = {"running": True, "remaining": delay}

        def update_live():
            if not state["running"]:
                return
            try:
                x, y = self.engine.get_mouse_location()
                coord_var.set(f"курсор: x={int(x)}, y={int(y)}")
            except Exception:
                coord_var.set("курсор: —")
            win.after(60, update_live)

        def capture(immediate=False):
            if not state["running"]:
                return
            state["running"] = False
            try:
                x, y = self.engine.get_mouse_location()
            except Exception as exc:
                win.destroy()
                self.calibration_target = None
                messagebox.showerror("Ошибка", f"Не удалось получить позицию: {exc}")
                return
            self.scenario.setdefault("fields", {})[name] = [round(x), round(y)]
            self.refresh_fields_table()
            win.destroy()
            self.calibration_target = None
            self.set_status(f"Поле «{name}»: {int(x)}, {int(y)}")

        def cancel():
            state["running"] = False
            win.destroy()
            self.calibration_target = None
            self.set_status(f"Калибровка «{name}» отменена")

        def tick():
            if not state["running"]:
                return
            r = state["remaining"]
            if r <= 0:
                capture(immediate=False)
                return
            label.configure(
                text=(
                    f"Поле «{name}»\n\n"
                    f"Наведи курсор. Через {r} сек запишу автоматически.\n"
                    f"F8 — записать сейчас. Кнопка «Отмена» — отказаться."
                )
            )
            state["remaining"] -= 1
            win.after(1000, tick)

        win.protocol("WM_DELETE_WINDOW", cancel)
        update_live()
        tick()

    def delete_field(self):
        if self.is_running:
            return
        sel = self.fields_tree.selection()
        if not sel:
            return
        name = self.fields_tree.item(sel[0], "values")[0]
        self.scenario.get("fields", {}).pop(name, None)
        self.refresh_fields_table()
        self.set_status(f"Поле «{name}» удалено")

    def generate_from_data(self):
        path = filedialog.askopenfilename(
            title="Выбери data.json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл: {exc}")
            return
        try:
            steps = generate_steps_from_data(self.scenario.get("fields", {}), data)
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))
            return
        if not steps:
            messagebox.showinfo("Пусто", "В data.json нет блоков для сценария.")
            return
        self.scenario["steps"] = steps
        self.refresh_steps_text()
        self.set_status(f"Сгенерировано {len(steps)} шагов из {Path(path).name}")

    def parse_float(self, value, name):
        try:
            x = float(str(value).strip())
            if x < 0:
                raise ValueError
            return x
        except Exception:
            messagebox.showerror("Ошибка", f"{name} должно быть неотрицательным числом.")
            return None

    def start_run(self):
        if self.is_running:
            return
        try:
            steps = self.parse_steps_text()
        except Exception as exc:
            messagebox.showerror("Ошибка JSON", f"Шаги не парсятся: {exc}")
            return
        if not steps:
            messagebox.showinfo("Пусто", "Сначала добавь шаги.")
            return
        start_delay = self.parse_float(self.start_delay_var.get(), "Старт через")
        if start_delay is None:
            return
        step_delay = self.parse_float(self.step_delay_var.get(), "Между шагами")
        if step_delay is None:
            return
        char_delay = self.parse_float(self.char_delay_var.get(), "Между символами")
        if char_delay is None:
            return

        self.scenario["steps"] = steps
        self.is_running = True
        self.stop_event.clear()
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.save_settings()

        threading.Thread(
            target=self._run_worker,
            args=(steps, start_delay, step_delay, char_delay),
            daemon=True,
        ).start()

    def stop_run(self):
        if self.is_running:
            self.stop_event.set()
            self.set_status("Запрошена остановка...")

    def _run_worker(self, steps, start_delay, step_delay, char_delay):
        try:
            self.set_status(f"Старт через {start_delay:.1f} сек. Переключись в нужное окно.")
            if start_delay > 0 and self.stop_event.wait(start_delay):
                self.set_status("Остановлено")
                return
            for i, step in enumerate(steps, start=1):
                if self.stop_event.is_set():
                    self.set_status(f"Остановлено на шаге {i}")
                    return
                action = step.get("action") if isinstance(step, dict) else None
                self.set_status(f"Шаг {i}/{len(steps)}: {action or '?'}")
                self.execute_step(step, char_delay)
                if step_delay > 0 and self.stop_event.wait(step_delay):
                    self.set_status("Остановлено")
                    return
            self.set_status("Готово")
        except Exception as exc:
            self.set_status(f"Ошибка: {exc}")
        finally:
            self.is_running = False
            self.root.after(0, self._reset_buttons)

    def _reset_buttons(self):
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

    def execute_step(self, step, char_delay):
        if not isinstance(step, dict):
            raise ValueError(f"Шаг не объект: {step!r}")
        action = step.get("action")
        if action == "click":
            x, y = self.resolve_point(step)
            self.engine.click(x, y)
        elif action == "type":
            self.engine.type_text(step.get("text", ""), char_delay=char_delay, stop_event=self.stop_event)
        elif action == "key":
            self.engine.press_key(step.get("name", "tab"))
        elif action == "sleep":
            seconds = step.get("seconds")
            if seconds is None:
                seconds = float(step.get("ms", 0)) / 1000.0
            self.stop_event.wait(float(seconds))
        else:
            raise ValueError(f"Неизвестное действие: {action!r}")

    def resolve_point(self, step):
        if "field" in step:
            name = step["field"]
            fields = self.scenario.get("fields", {})
            if name not in fields:
                raise KeyError(f"Поле «{name}» не калибровано")
            x, y = fields[name]
            return float(x), float(y)
        return float(step["x"]), float(step["y"])

    # ---------- Helper tab ----------

    def helper_load_data(self):
        path = filedialog.askopenfilename(
            title="Выбери data.json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл: {exc}")
            return
        blocks = extract_blocks_from_data(data)
        if not blocks:
            messagebox.showinfo("Пусто", "В файле не нашлось блоков.")
            return
        self._set_helper_blocks(blocks)
        self.set_status(f"Помощник: загружено {len(blocks)} блоков из {Path(path).name}")

    def helper_load_from_scenario(self):
        try:
            steps = self.parse_steps_text()
        except Exception as exc:
            messagebox.showerror("Ошибка JSON", f"Шаги не парсятся: {exc}")
            return
        blocks = extract_blocks_from_steps(steps)
        if not blocks:
            messagebox.showinfo("Пусто", "В сценарии нет шагов type.")
            return
        self._set_helper_blocks(blocks)
        self.set_status(f"Помощник: {len(blocks)} блоков из текущего сценария")

    def _set_helper_blocks(self, blocks):
        self.helper_blocks = blocks
        self.helper_index = 0
        self.helper_active = False
        self.helper_listbox.delete(0, "end")
        for b in blocks:
            self.helper_listbox.insert("end", b["label"])
        self.helper_label_var.set(blocks[0]["label"] if blocks else "—")
        self.helper_progress_var.set(f"0/{len(blocks)}")
        self.helper_next_button.config(state="disabled")
        self.helper_stop_button.config(state="disabled")
        self._highlight_helper_index()

    def _highlight_helper_index(self):
        self.helper_listbox.selection_clear(0, "end")
        if 0 <= self.helper_index < len(self.helper_blocks):
            self.helper_listbox.selection_set(self.helper_index)
            self.helper_listbox.see(self.helper_index)

    def _helper_jump_to_selected(self, _event=None):
        if not self.helper_active:
            return
        sel = self.helper_listbox.curselection()
        if not sel:
            return
        self.helper_index = int(sel[0])
        self._copy_current_block()

    def _copy_to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def _copy_current_block(self):
        if not (0 <= self.helper_index < len(self.helper_blocks)):
            return
        block = self.helper_blocks[self.helper_index]
        self._copy_to_clipboard(block["text"])
        self.helper_label_var.set(block["label"])
        self.helper_progress_var.set(f"{self.helper_index + 1}/{len(self.helper_blocks)}")
        self._highlight_helper_index()
        self.set_status(f"В буфере: {block['label']}")

    def helper_start(self):
        if not self.helper_blocks:
            messagebox.showinfo("Пусто", "Сначала загрузи блоки.")
            return
        self.helper_active = True
        self.helper_index = 0
        self._copy_current_block()
        self.helper_next_button.config(state="normal")
        self.helper_stop_button.config(state="normal")

    def helper_next(self):
        if not self.helper_active:
            return
        if self.helper_index + 1 >= len(self.helper_blocks):
            self.set_status("Помощник: блоки закончились")
            self.helper_active = False
            self.helper_next_button.config(state="disabled")
            return
        self.helper_index += 1
        self._copy_current_block()

    def helper_stop(self):
        self.helper_active = False
        self.helper_next_button.config(state="disabled")
        self.helper_stop_button.config(state="disabled")
        self.set_status("Помощник остановлен")

    # ---------- Hotkey callbacks ----------

    def _on_f8(self):
        if self.calibration_target is None:
            return
        # маршалим в main thread
        self.root.after(0, self._capture_calibration_now)

    def _capture_calibration_now(self):
        name = self.calibration_target
        if name is None:
            return
        try:
            x, y = self.engine.get_mouse_location()
        except Exception:
            return
        self.scenario.setdefault("fields", {})[name] = [round(x), round(y)]
        self.refresh_fields_table()
        self.calibration_target = None
        self.set_status(f"Поле «{name}»: {int(x)}, {int(y)}")
        # Закрываем окно калибровки если оно открыто
        for w in self.root.winfo_children():
            if isinstance(w, tk.Toplevel) and w.title() == "Калибровка":
                w.destroy()
                break

    def _on_f9(self):
        if not self.helper_active:
            return
        self.root.after(0, self.helper_next)

    # ---------- Settings & lifecycle ----------

    def load_settings(self):
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        self.scenario_path_var.set(str(data.get("scenario_path", "")))
        self.start_delay_var.set(str(data.get("start_delay", self.start_delay_var.get())))
        self.step_delay_var.set(str(data.get("step_delay", self.step_delay_var.get())))
        self.char_delay_var.set(str(data.get("char_delay", self.char_delay_var.get())))
        scenario_path = data.get("scenario_path", "")
        if scenario_path and Path(scenario_path).is_file():
            try:
                content = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
                if isinstance(content, dict):
                    fields = content.get("fields", {}) or {}
                    normalized = {}
                    for name, coords in fields.items():
                        if isinstance(coords, (list, tuple)) and len(coords) == 2:
                            normalized[name] = [float(coords[0]), float(coords[1])]
                    self.scenario = {
                        "fields": normalized,
                        "steps": content.get("steps", []) or [],
                    }
            except Exception:
                pass

    def save_settings(self):
        data = {
            "scenario_path": self.scenario_path_var.get().strip(),
            "start_delay": self.start_delay_var.get().strip(),
            "step_delay": self.step_delay_var.get().strip(),
            "char_delay": self.char_delay_var.get().strip(),
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def on_close(self):
        self.save_settings()
        self.hotkeys.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    AutoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
