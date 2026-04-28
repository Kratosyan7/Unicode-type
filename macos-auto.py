"""
Unicode Auto Typer (macOS) — сценарный движок автоматизации.

Из JSON-сценария программа последовательно:
- кликает по координатам экрана,
- печатает Unicode-текст,
- нажимает функциональные клавиши,
- делает паузы.

Координаты можно записать через калибратор (наведи курсор и подожди таймер).

Формат сценария (один JSON-файл):
{
  "fields": {
    "имя_поля": [x, y]
  },
  "steps": [
    {"action": "click", "field": "имя_поля"},
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
    CGEventGetLocation,
    CGEventKeyboardSetUnicodeString,
    CGEventPost,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseUp,
    kCGEventMouseMoved,
    kCGHIDEventTap,
    kCGMouseButtonLeft,
)


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


class AutoApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Unicode Auto Typer")
        self.root.geometry("760x640")
        self.root.minsize(640, 520)

        self.engine = MacosAutomation()
        self.is_running = False
        self.stop_event = threading.Event()

        self.scenario_path_var = tk.StringVar(value="")
        self.start_delay_var = tk.StringVar(value="3.0")
        self.step_delay_var = tk.StringVar(value="0.05")
        self.char_delay_var = tk.StringVar(value="0.0")
        self.status_var = tk.StringVar(value="Готово")

        self.scenario = {"fields": {}, "steps": []}

        self.load_settings()
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

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
        ttk.Label(topbar, text="macOS edition · сценарии автоматизации", style="FieldLabel.TLabel").pack(anchor="w")

        scenario_frame = ttk.LabelFrame(main, text="Сценарий", padding=8)
        scenario_frame.pack(fill="x", pady=(0, 8))

        path_row = ttk.Frame(scenario_frame)
        path_row.pack(fill="x")
        ttk.Label(path_row, text="Файл:", style="FieldLabel.TLabel").pack(side="left")
        ttk.Entry(path_row, textvariable=self.scenario_path_var).pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(path_row, text="Открыть", command=self.open_scenario).pack(side="left")
        ttk.Button(path_row, text="Сохранить", command=self.save_scenario).pack(side="left", padx=(6, 0))
        ttk.Button(path_row, text="Новый", command=self.new_scenario).pack(side="left", padx=(6, 0))

        fields_frame = ttk.LabelFrame(main, text="Поля (координаты)", padding=8)
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

        steps_frame = ttk.LabelFrame(main, text="Шаги (JSON)", padding=8)
        steps_frame.pack(fill="both", expand=True, pady=(0, 8))
        self.steps_text = tk.Text(
            steps_frame,
            wrap="none",
            height=10,
            font=("SF Mono", 11),
            undo=True,
            relief="flat",
            background="#FAFBFD",
        )
        self.steps_text.pack(fill="both", expand=True)

        params = ttk.LabelFrame(main, text="Параметры", padding=8)
        params.pack(fill="x", pady=(0, 8))
        ttk.Label(params, text="Старт через, сек", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(params, textvariable=self.start_delay_var, width=8).grid(row=1, column=0, padx=(0, 16), sticky="w")
        ttk.Label(params, text="Между шагами, сек", style="FieldLabel.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Entry(params, textvariable=self.step_delay_var, width=8).grid(row=1, column=1, padx=(0, 16), sticky="w")
        ttk.Label(params, text="Между символами, сек", style="FieldLabel.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Entry(params, textvariable=self.char_delay_var, width=8).grid(row=1, column=2, padx=(0, 16), sticky="w")

        actions = ttk.Frame(main)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="Старт", command=self.start_run)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="Стоп", command=self.stop_run, state="disabled")
        self.stop_button.pack(side="left", padx=(6, 0))
        ttk.Label(actions, textvariable=self.status_var, style="FieldLabel.TLabel").pack(side="right")

        self.refresh_fields_table()
        self.refresh_steps_text()

    def set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

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
            self.set_status(f"Загружено: {Path(path).name} — внимание: 0 шагов")
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
        delay = 3
        win = tk.Toplevel(self.root)
        win.title("Калибровка")
        win.geometry("420x150")
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
        ttk.Label(win, textvariable=coord_var, font=("SF Mono", 10)).pack(pady=(0, 8))

        live_running = {"value": True}

        def update_live():
            if not live_running["value"]:
                return
            try:
                x, y = self.engine.get_mouse_location()
                coord_var.set(f"курсор: x={int(x)}, y={int(y)}")
            except Exception:
                coord_var.set("курсор: —")
            win.after(60, update_live)

        def tick(remaining):
            if remaining <= 0:
                live_running["value"] = False
                try:
                    x, y = self.engine.get_mouse_location()
                except Exception as exc:
                    win.destroy()
                    messagebox.showerror("Ошибка", f"Не удалось получить позицию: {exc}")
                    return
                self.scenario.setdefault("fields", {})[name] = [round(x), round(y)]
                self.refresh_fields_table()
                win.destroy()
                self.set_status(f"Поле «{name}»: {int(x)}, {int(y)}")
                return
            label.configure(
                text=(
                    f"Поле «{name}»\n\n"
                    f"Переведи курсор в нужное место.\n"
                    f"Запишу через {remaining} сек..."
                )
            )
            win.after(1000, lambda: tick(remaining - 1))

        update_live()
        tick(delay)

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

    def parse_float(self, value, name, default=0.0):
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
                if step_delay > 0:
                    if self.stop_event.wait(step_delay):
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
            text = step.get("text", "")
            self.engine.type_text(text, char_delay=char_delay, stop_event=self.stop_event)
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
        self.root.destroy()


def main():
    root = tk.Tk()
    AutoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
