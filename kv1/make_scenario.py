"""
Генератор сценария для КВ_1 (Дерево иерархии объектов) под форму ARM_Student.

Использование:
    python3 kv1/make_scenario.py kv1/profile.json kv1/data.json kv1/scenario.json

Где:
    kv1/profile.json   — JSON с координатами полей формы (см. ниже).
    kv1/data.json      — содержимое отчёта (имена объектов/функций/инструментов).
    kv1/scenario.json  — итоговый сценарий для macos-auto.py / windows-auto.py.

Какие поля нужно откалибровать в profile.json (раздел "fields"):
    obj_row1_name   — поле «Наименование» в первой строке таблицы «Используемые объекты»
    obj_row2_name   — то же самое во второй строке (нужно ТОЛЬКО если объектов > 1)

    func_row1_name  — поле «Наименование» в первой строке таблицы «Используемые функции»
    func_row2_name  — то же самое во второй строке (нужно ТОЛЬКО если функций > 1)

    tool_row1_desc  — поле «Описание» в первой строке таблицы «Другие инструменты»
    tool_row2_desc  — то же самое во второй строке (нужно ТОЛЬКО если инструментов > 1)

Перед запуском сценария нажми «+» в форме столько раз, сколько нужно строк
в каждой группе. Сценарий заходит в первое поле каждой строки, печатает
текст и переходит дальше клавишей Tab.

Если строк больше двух — координаты row3, row4 и так далее вычисляются
линейной экстраполяцией: (row_n) = row1 + (n - 1) * (row2 - row1).

Формат profile.json:
    {
      "fields": {
        "obj_row1_name":  [950, 514],
        "func_row1_name": [983, 632],
        "func_row2_name": [983, 668],
        "tool_row1_desc": [1300, 745],
        "tool_row2_desc": [1300, 781]
      }
    }
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


REQUIRED_PROFILE_FIELDS = [
    "obj_row1_name",
    "obj_row2_name",
    "func_row1_name",
    "func_row2_name",
    "tool_row1_desc",
    "tool_row2_desc",
]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_profile_template(path: Path) -> None:
    template = {
        "fields": {name: [0, 0] for name in REQUIRED_PROFILE_FIELDS},
        "steps": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")


def get_point(profile: dict, name: str) -> tuple[float, float] | None:
    fields = profile.get("fields", {})
    coords = fields.get(name)
    if not isinstance(coords, (list, tuple)) or len(coords) != 2:
        return None
    return float(coords[0]), float(coords[1])


def require_point(profile: dict, name: str) -> tuple[float, float]:
    pt = get_point(profile, name)
    if pt is None:
        raise SystemExit(f"В profile.fields отсутствует обязательное поле «{name}».")
    return pt


def row_coord(row1: tuple[float, float], row2: tuple[float, float] | None, index_zero_based: int) -> tuple[float, float]:
    if index_zero_based == 0:
        return row1
    if row2 is None:
        raise SystemExit(
            "Нужно больше одной строки, но row2 не откалибровано. "
            "Добавь координату второй строки в profile.fields."
        )
    dx = row2[0] - row1[0]
    dy = row2[1] - row1[1]
    return (row1[0] + dx * index_zero_based, row1[1] + dy * index_zero_based)


def click(point: tuple[float, float]) -> dict:
    return {"action": "click", "x": round(point[0]), "y": round(point[1])}


def type_text(text: str) -> dict:
    return {"action": "type", "text": text}


def press(name: str) -> dict:
    return {"action": "key", "name": name}


def sleep_step(seconds: float) -> dict:
    return {"action": "sleep", "seconds": seconds}


def build_objects_steps(profile: dict, objects: list[dict]) -> list[dict]:
    if not objects:
        return []
    row1 = require_point(profile, "obj_row1_name")
    row2 = get_point(profile, "obj_row2_name")
    steps: list[dict] = []
    for i, obj in enumerate(objects):
        point = row_coord(row1, row2, i)
        steps.append(click(point))
        steps.append(sleep_step(0.05))
        steps.append(type_text(obj["name"]))
        steps.append(press("tab"))
        steps.append(type_text(obj["class"]))
        steps.append(press("tab"))
        steps.append(type_text(obj["desc"]))
    return steps


def build_functions_steps(profile: dict, functions: list[dict]) -> list[dict]:
    if not functions:
        return []
    row1 = require_point(profile, "func_row1_name")
    row2 = get_point(profile, "func_row2_name")
    steps: list[dict] = []
    for i, fn in enumerate(functions):
        point = row_coord(row1, row2, i)
        steps.append(click(point))
        steps.append(sleep_step(0.05))
        steps.append(type_text(fn["name"]))
        steps.append(press("tab"))
        steps.append(type_text(fn["desc"]))
    return steps


def build_tools_steps(profile: dict, tools: list[dict]) -> list[dict]:
    if not tools:
        return []
    row1 = require_point(profile, "tool_row1_desc")
    row2 = get_point(profile, "tool_row2_desc")
    steps: list[dict] = []
    for i, tool in enumerate(tools):
        point = row_coord(row1, row2, i)
        steps.append(click(point))
        steps.append(sleep_step(0.05))
        steps.append(type_text(tool["desc"]))
    return steps


def build_scenario(profile: dict, data: dict) -> dict:
    steps: list[dict] = []
    steps.extend(build_objects_steps(profile, data.get("objects", [])))
    steps.extend(build_functions_steps(profile, data.get("functions", [])))
    steps.extend(build_tools_steps(profile, data.get("tools", [])))
    return {
        "fields": profile.get("fields", {}),
        "steps": steps,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 1
    profile_path = Path(argv[1])
    data_path = Path(argv[2])
    out_path = Path(argv[3])

    if not profile_path.exists():
        write_profile_template(profile_path)
        print(
            f"Профиль {profile_path} не найден. Создан пустой шаблон.\n"
            f"Открой его в macos-auto.py / windows-auto.py (кнопка «Открыть»),\n"
            f"откалибруй координаты пяти полей "
            f"(obj_row1_name, func_row1_name, func_row2_name, tool_row1_desc, tool_row2_desc),\n"
            f"нажми «Сохранить» и снова запусти эту команду."
        )
        return 1

    profile = load_json(profile_path)
    data = load_json(data_path)

    scenario = build_scenario(profile, data)
    out_path.write_text(json.dumps(scenario, ensure_ascii=False, indent=2), encoding="utf-8")
    n_steps = len(scenario["steps"])
    print(f"Сохранено {n_steps} шагов в {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
