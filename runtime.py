# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import copy
import json
import math
import operator
import threading
import uuid
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Shared variables / controls
# ---------------------------------------------------------------------------


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [json_safe(x) for x in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    return repr(value)


class SharedState:
    def __init__(self, notify: Callable[[str], None]) -> None:
        self._lock = threading.RLock()
        self.variables: dict[str, Any] = {}
        self.controls: dict[str, dict[str, Any]] = {}
        self.notify = notify

    def variable_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.variables)

    def set_var(self, name: str, value: Any) -> None:
        name = str(name).strip()
        if not name or not name.isidentifier():
            raise ValueError(
                "Имя переменной должно быть допустимым идентификатором "
                "(например speed, x, матрица)"
            )
        with self._lock:
            self.variables[name] = copy.deepcopy(value)
        self.notify("variables")

    def get_var(self, name: str) -> Any:
        with self._lock:
            if name not in self.variables:
                raise NameError(f"Переменная {name!r} не существует")
            return copy.deepcopy(self.variables[name])

    def del_var(self, name: str) -> None:
        with self._lock:
            self.variables.pop(name, None)
        self.notify("variables")

    @staticmethod
    def _normalise_control_value(kind: str, value: Any) -> Any:
        if kind == "checkbox":
            return bool(value)
        if kind == "text":
            return str(value)
        if kind == "button":
            try:
                return int(value)
            except Exception:
                return 0
        if kind in ("slider", "number"):
            try:
                return float(value)
            except Exception:
                return 0.0
        return value

    def ensure_control(
        self,
        name: str,
        *,
        label: Optional[str] = None,
        kind: str = "slider",
        minimum: float = 0.0,
        maximum: float = 100.0,
        step: float = 1.0,
        default: Any = 0.0,
        bind_var: str = "",
    ) -> None:
        name = str(name).strip()
        if not name:
            raise ValueError("У элемента управления должно быть имя")
        if kind not in {"slider", "number", "checkbox", "text", "button"}:
            raise ValueError(f"Неизвестный тип элемента управления: {kind}")

        with self._lock:
            old = self.controls.get(name)
            if old and old.get("kind") == kind:
                value = old.get("value", default)
            else:
                value = default

            entry = {
                "name": name,
                "label": label or name,
                "kind": kind,
                "min": float(minimum),
                "max": float(maximum),
                "step": max(0.000001, float(step)),
                "value": self._normalise_control_value(kind, value),
                "bind_var": str(bind_var or "").strip(),
            }
            self.controls[name] = entry

            if entry["bind_var"]:
                bname = entry["bind_var"]
                if not bname.isidentifier():
                    raise ValueError(f"Некорректное имя привязанной переменной: {bname}")
                self.variables[bname] = copy.deepcopy(entry["value"])

        self.notify("controls")
        self.notify("variables")

    def controls_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self.controls)

    def get_control(self, name: str) -> Any:
        with self._lock:
            if name not in self.controls:
                raise KeyError(f"Элемент управления {name!r} не существует")
            return copy.deepcopy(self.controls[name]["value"])

    def set_control(self, name: str, value: Any) -> None:
        with self._lock:
            if name not in self.controls:
                raise KeyError(f"Элемент управления {name!r} не существует")
            entry = self.controls[name]
            new_value = self._normalise_control_value(entry["kind"], value)
            if entry["kind"] in ("slider", "number"):
                new_value = min(entry["max"], max(entry["min"], float(new_value)))
            changed = new_value != entry.get("value")
            entry["value"] = new_value
            bind_var = entry.get("bind_var", "")
            if bind_var:
                self.variables[bind_var] = copy.deepcopy(new_value)

        if changed:
            self.notify("control_values")
            if bind_var:
                self.notify("variables")

    def del_control(self, name: str) -> None:
        with self._lock:
            self.controls.pop(name, None)
        self.notify("controls")

    def snapshot_for_project(self) -> dict[str, Any]:
        with self._lock:
            return {
                "variables": json_safe(copy.deepcopy(self.variables)),
                "controls": json_safe(copy.deepcopy(self.controls)),
            }

    def restore_from_project(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.variables = copy.deepcopy(payload.get("variables", {}))
            self.controls = copy.deepcopy(payload.get("controls", {}))
        self.notify("variables")
        self.notify("controls")


# ---------------------------------------------------------------------------
# Safe expressions
# ---------------------------------------------------------------------------


class SafeEvaluator:
    BIN_OPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    UNARY_OPS = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
        ast.Not: operator.not_,
    }
    CMP_OPS = {
        ast.Eq: operator.eq,
        ast.NotEq: operator.ne,
        ast.Lt: operator.lt,
        ast.LtE: operator.le,
        ast.Gt: operator.gt,
        ast.GtE: operator.ge,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }

    def __init__(self, state: SharedState) -> None:
        self.state = state

    def _functions(self) -> dict[str, Callable[..., Any]]:
        return {
            "abs": abs,
            "min": min,
            "max": max,
            "sum": sum,
            "len": len,
            "round": round,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "sqrt": math.sqrt,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "floor": math.floor,
            "ceil": math.ceil,
            "range": lambda *a: list(range(*map(int, a))),
            "control": lambda name: self.state.get_control(str(name)),
            "var": lambda name: self.state.get_var(str(name)),
            "zeros": lambda rows, cols=1: [
                [0 for _ in range(int(cols))] for _ in range(int(rows))
            ],
            "ones": lambda rows, cols=1: [
                [1 for _ in range(int(cols))] for _ in range(int(rows))
            ],
        }

    def eval(self, expression: Any) -> Any:
        if not isinstance(expression, str):
            return copy.deepcopy(expression)
        expression = expression.strip()
        if not expression:
            return None
        tree = ast.parse(expression, mode="eval")
        names = self.state.variable_snapshot()
        return self._node(tree.body, names)

    def _node(self, node: ast.AST, names: dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in names:
                return copy.deepcopy(names[node.id])
            if node.id == "True":
                return True
            if node.id == "False":
                return False
            if node.id == "None":
                return None
            raise NameError(f"Неизвестное имя: {node.id}")

        if isinstance(node, ast.List):
            return [self._node(x, names) for x in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._node(x, names) for x in node.elts)
        if isinstance(node, ast.Dict):
            return {
                self._node(k, names): self._node(v, names)
                for k, v in zip(node.keys, node.values)
            }

        if isinstance(node, ast.BinOp) and type(node.op) in self.BIN_OPS:
            left = self._node(node.left, names)
            right = self._node(node.right, names)
            if isinstance(node.op, ast.Pow) and isinstance(right, (int, float)):
                if abs(right) > 1000:
                    raise ValueError("Слишком большая степень")
            return self.BIN_OPS[type(node.op)](left, right)

        if isinstance(node, ast.UnaryOp) and type(node.op) in self.UNARY_OPS:
            return self.UNARY_OPS[type(node.op)](self._node(node.operand, names))

        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result = True
                for part in node.values:
                    result = self._node(part, names)
                    if not result:
                        return result
                return result
            if isinstance(node.op, ast.Or):
                result = False
                for part in node.values:
                    result = self._node(part, names)
                    if result:
                        return result
                return result

        if isinstance(node, ast.Compare):
            left = self._node(node.left, names)
            for op_node, comp in zip(node.ops, node.comparators):
                right = self._node(comp, names)
                fn = self.CMP_OPS.get(type(op_node))
                if fn is None or not fn(left, right):
                    return False
                left = right
            return True

        if isinstance(node, ast.IfExp):
            cond = self._node(node.test, names)
            return self._node(node.body if cond else node.orelse, names)

        if isinstance(node, ast.Subscript):
            value = self._node(node.value, names)
            sl = node.slice
            if isinstance(sl, ast.Slice):
                lower = self._node(sl.lower, names) if sl.lower else None
                upper = self._node(sl.upper, names) if sl.upper else None
                step = self._node(sl.step, names) if sl.step else None
                return value[slice(lower, upper, step)]
            index = self._node(sl, names)
            return value[index]

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("Разрешены только безопасные встроенные функции")
            fn = self._functions().get(node.func.id)
            if fn is None:
                raise ValueError(f"Функция {node.func.id!r} запрещена")
            args = [self._node(x, names) for x in node.args]
            kwargs = {
                kw.arg: self._node(kw.value, names)
                for kw in node.keywords
                if kw.arg is not None
            }
            return fn(*args, **kwargs)

        raise ValueError(f"Конструкция выражения запрещена: {type(node).__name__}")


def nested_set(container: Any, index_spec: Any, value: Any) -> Any:
    result = copy.deepcopy(container)
    path = list(index_spec) if isinstance(index_spec, (list, tuple)) else [index_spec]
    if not path:
        return copy.deepcopy(value)
    cur = result
    for idx in path[:-1]:
        cur = cur[int(idx)]
    cur[int(path[-1])] = copy.deepcopy(value)
    return result


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

BLOCK_META = {
    "forward":        ("ВПЕРЁД", "#43A047"),
    "backward":       ("НАЗАД", "#388E3C"),
    "left":           ("ПОВОРОТ НАЛЕВО", "#1E88E5"),
    "right":          ("ПОВОРОТ НАПРАВО", "#1976D2"),
    "stop":           ("СТОП", "#E53935"),
    "wait":           ("ПАУЗА", "#FB8C00"),

    "repeat_start":   ("ПОВТОРИТЬ", "#FFB300"),
    "repeat_end":     ("КОНЕЦ ПОВТОРА", "#F57C00"),
    "parallel_start": ("ПАРАЛЛЕЛЬНО", "#5E35B1"),
    "parallel_branch":("НОВАЯ ВЕТКА", "#673AB7"),
    "parallel_end":   ("КОНЕЦ ПАРАЛЛЕЛИ", "#512DA8"),

    "var_set":        ("ЗАДАТЬ ПЕРЕМЕННУЮ", "#00897B"),
    "var_change":     ("ИЗМЕНИТЬ ПЕРЕМЕННУЮ", "#00796B"),
    "var_math":       ("АРИФМЕТИКА С ПЕРЕМЕННОЙ", "#00695C"),
    "array_set":      ("ЗАДАТЬ ЭЛЕМЕНТ МАССИВА", "#00838F"),
    "array_append":   ("ДОБАВИТЬ В МАССИВ", "#006064"),
    "array_insert":   ("ВСТАВИТЬ В МАССИВ", "#00695C"),
    "array_pop":      ("УДАЛИТЬ ИЗ МАССИВА", "#004D40"),
    "log_expr":       ("В ЖУРНАЛ", "#546E7A"),

    "control_define": ("ЭЛЕМЕНТ УПРАВЛЕНИЯ", "#7E57C2"),
    "control_read":   ("СЧИТАТЬ УПРАВЛЕНИЕ", "#6A1B9A"),
    "control_write":  ("ЗАДАТЬ УПРАВЛЕНИЕ", "#8E24AA"),

    "matrix":         ("МАТРИЦА 5×5", "#8E24AA"),
    "marquee":        ("БЕГУЩАЯ СТРОКА", "#AB47BC"),
    "clear_matrix":   ("ОЧИСТИТЬ МАТРИЦУ", "#7B1FA2"),
    "melody":         ("МЕЛОДИЯ", "#00ACC1"),

    "file_read":      ("ПРОЧИТАТЬ ФАЙЛ", "#455A64"),
    "file_write":     ("ЗАПИСАТЬ ФАЙЛ", "#37474F"),
    "file_list":      ("СПИСОК ФАЙЛОВ", "#263238"),

    "raw":            ("HEX-КОМАНДА", "#607D8B"),
}


def new_block(kind: str) -> dict[str, Any]:
    b: dict[str, Any] = {"id": str(uuid.uuid4()), "type": kind}
    if kind in ("forward", "backward", "left", "right"):
        b["duration_expr"] = "1.0"
    elif kind == "wait":
        b["duration_expr"] = "1.0"
    elif kind == "repeat_start":
        b["count_expr"] = "2"
    elif kind == "var_set":
        b.update(name="x", expr="0")
    elif kind == "var_change":
        b.update(name="x", expr="1")
    elif kind == "var_math":
        b.update(name="x", op="*", expr="2")
    elif kind == "array_set":
        b.update(name="arr", index_expr="0", value_expr="0")
    elif kind == "array_append":
        b.update(name="arr", value_expr="0")
    elif kind == "array_insert":
        b.update(name="arr", index_expr="0", value_expr="0")
    elif kind == "array_pop":
        b.update(name="arr", index_expr="-1", target="")
    elif kind == "log_expr":
        b["expr"] = "x"
    elif kind == "control_define":
        b.update(
            control_name="speed",
            label="Скорость",
            control_kind="slider",
            min_expr="0",
            max_expr="100",
            step_expr="1",
            default_expr="50",
            bind_var="speed",
        )
    elif kind == "control_read":
        b.update(control_name="speed", target="speed")
    elif kind == "control_write":
        b.update(control_name="speed", value_expr="50")
    elif kind == "matrix":
        b.update(source="manual", rows=[0, 0, 0, 0, 0], matrix_expr="screen")
    elif kind == "marquee":
        b.update(text_expr="'ПРИВЕТ'", frame_time_expr="0.15", duration_expr="0")
    elif kind == "melody":
        b["number_expr"] = "0"
    elif kind == "file_read":
        b.update(path_expr="'data.json'", target="data", mode="json")
    elif kind == "file_write":
        b.update(path_expr="'data.json'", value_expr="data", mode="json", append=False)
    elif kind == "file_list":
        b.update(path_expr="'.'", target="files")
    elif kind == "raw":
        b["hex"] = "00"
    return b


def block_title(block: dict[str, Any]) -> str:
    kind = block["type"]
    name = BLOCK_META[kind][0]
    if kind in ("forward", "backward", "left", "right", "wait"):
        return f"{name}   {block.get('duration_expr', '1.0')} с"
    if kind == "repeat_start":
        return f"{name}   {block.get('count_expr', '2')} раз"
    if kind == "var_set":
        return f"{name}   {block.get('name','x')} = {block.get('expr','0')}"
    if kind == "var_change":
        return f"{name}   {block.get('name','x')} += {block.get('expr','1')}"
    if kind == "var_math":
        return f"{name}   {block.get('name','x')} {block.get('op','*')}= {block.get('expr','2')}"
    if kind == "array_set":
        return (
            f"{name}   {block.get('name','arr')}[{block.get('index_expr','0')}]"
            f" = {block.get('value_expr','0')}"
        )
    if kind == "array_append":
        return f"{name}   {block.get('name','arr')} ← {block.get('value_expr','0')}"
    if kind == "array_insert":
        return f"{name}   {block.get('name','arr')}[{block.get('index_expr','0')}] ← {block.get('value_expr','0')}"
    if kind == "array_pop":
        target = block.get('target','')
        suffix = f" → {target}" if target else ""
        return f"{name}   {block.get('name','arr')}[{block.get('index_expr','-1')}]{suffix}"
    if kind == "log_expr":
        return f"{name}   {block.get('expr','')}"
    if kind == "control_define":
        return f"{name}   {block.get('control_name','control')}"
    if kind == "control_read":
        return f"{name}   {block.get('target','x')} ← {block.get('control_name','control')}"
    if kind == "control_write":
        return f"{name}   {block.get('control_name','control')} ← {block.get('value_expr','0')}"
    if kind == "matrix":
        if block.get("source") == "expr":
            return f"{name}   ← {block.get('matrix_expr','screen')}"
        rows = block.get("rows", [0]*5)
        return f"{name}   " + " ".join(f"{int(x)&31:05b}" for x in rows)
    if kind == "marquee":
        return f"{name}   {block.get('text_expr', repr('ПРИВЕТ'))}"
    if kind == "melody":
        return f"{name}   № {block.get('number_expr','0')}"
    if kind == "file_read":
        return f"{name}   {block.get('path_expr', repr('data.json'))} → {block.get('target','data')}"
    if kind == "file_write":
        return f"{name}   {block.get('value_expr','data')} → {block.get('path_expr', repr('data.json'))}"
    if kind == "file_list":
        return f"{name}   {block.get('path_expr', repr('.'))} → {block.get('target','files')}"
    if kind == "raw":
        return f"{name}   {block.get('hex','00')}"
    return name


# ---------------------------------------------------------------------------
# Linear block structure parser
# ---------------------------------------------------------------------------

CLOSING_TYPES = {"repeat_end", "parallel_branch", "parallel_end"}


def parse_sequence(
    blocks: list[dict[str, Any]],
    index: int = 0,
    stop_types: Optional[set[str]] = None,
):
    stop_types = stop_types or set()
    nodes: list[dict[str, Any]] = []

    while index < len(blocks):
        block = blocks[index]
        kind = block["type"]

        if kind in stop_types:
            return nodes, index, kind

        if kind == "repeat_start":
            children, end_index, token = parse_sequence(blocks, index + 1, {"repeat_end"})
            if token != "repeat_end":
                raise ValueError("Для «ПОВТОРИТЬ» не найден «КОНЕЦ ПОВТОРА»")
            nodes.append({
                "node": "repeat",
                "start": block,
                "end": blocks[end_index],
                "children": children,
            })
            index = end_index + 1
            continue

        if kind == "parallel_start":
            branches: list[list[dict[str, Any]]] = []
            markers: list[dict[str, Any]] = []
            pos = index + 1
            while True:
                children, end_index, token = parse_sequence(
                    blocks, pos, {"parallel_branch", "parallel_end"}
                )
                branches.append(children)
                if token == "parallel_branch":
                    markers.append(blocks[end_index])
                    pos = end_index + 1
                    continue
                if token == "parallel_end":
                    nodes.append({
                        "node": "parallel",
                        "start": block,
                        "end": blocks[end_index],
                        "branches": branches,
                        "markers": markers,
                    })
                    index = end_index + 1
                    break
                raise ValueError("Для «ПАРАЛЛЕЛЬНО» не найден «КОНЕЦ ПАРАЛЛЕЛИ»")
            continue

        if kind in CLOSING_TYPES:
            raise ValueError(f"Неожиданный блок «{BLOCK_META[kind][0]}»")

        nodes.append({"node": "block", "block": block})
        index += 1

    return nodes, index, None


def validate_program(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes, index, token = parse_sequence(blocks)
    if token is not None or index != len(blocks):
        raise ValueError("Ошибка структуры программы")
    return nodes
