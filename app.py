# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import queue
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from ble_robot import AsyncLoopThread, RobotBLE
from protocol import DEVICE_NAME, CMD_STOP
from runtime import (
    SharedState,
    SafeEvaluator,
    BLOCK_META,
    block_title,
    new_block,
    validate_program,
    json_safe,
)
from runner import ProgramRunner
from ui_helpers import ScrollFrame
from workspace import ProgramWorkspace
from windows import VariablesWindow, ControlsWindow


class RobotStudioApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("HQ_BLE Robot Studio v2.0")
        self.root.geometry("1500x900")
        self.root.minsize(1150, 720)

        self.bg = "#EEF1F5"
        self.panel = "#FFFFFF"
        self.text = "#17212B"
        self.muted = "#6B7785"
        self.border = "#D9DEE6"
        self.root.configure(bg=self.bg)

        self.ui_queue: queue.Queue = queue.Queue()
        self.async_thread = AsyncLoopThread()
        self.state = SharedState(self._state_notify)
        self.evaluator = SafeEvaluator(self.state)
        self.robot = RobotBLE(self.emit)

        self.running = False
        self.connected = False
        self.current_runner: Optional[ProgramRunner] = None
        self.variables_window: Optional[VariablesWindow] = None
        self.controls_window: Optional[ControlsWindow] = None
        self.project_path: Optional[Path] = None

        self.programs: list[dict[str, Any]] = []
        self.workspaces: dict[str, ProgramWorkspace] = {}

        self._build_styles()
        self._build_ui()
        self.new_program("Программа 1", demo=True)

        self.root.after(40, self._poll_ui)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Cross-thread events
    # ------------------------------------------------------------------

    def emit(self, event: str, *args) -> None:
        self.ui_queue.put((event, args))

    def _state_notify(self, what: str) -> None:
        self.emit("state_changed", what)

    def _poll_ui(self) -> None:
        try:
            while True:
                event, args = self.ui_queue.get_nowait()
                self._handle_event(event, *args)
        except queue.Empty:
            pass
        self.root.after(40, self._poll_ui)

    def _handle_event(self, event: str, *args) -> None:
        if event == "log":
            self.append_log(str(args[0]))
        elif event == "status":
            self.set_status(str(args[0]), str(args[1]))
        elif event == "connected":
            self.connected = bool(args[0])
            self.refresh_toolbar()
        elif event == "running":
            self.running = bool(args[0])
            self.refresh_toolbar()
        elif event == "tx":
            self.tx_var.set(str(args[0]))
        elif event == "rx":
            self.rx_var.set(str(args[0]))
        elif event == "state_changed":
            what = str(args[0])
            if what == "variables" and self.variables_window:
                try:
                    self.variables_window.refresh()
                except tk.TclError:
                    self.variables_window = None
            if what == "controls" and self.controls_window:
                try:
                    self.controls_window.refresh()
                except tk.TclError:
                    self.controls_window = None
            if what == "control_values" and self.controls_window:
                try:
                    self.controls_window.sync_values()
                except tk.TclError:
                    self.controls_window = None
        elif event == "highlight":
            program_id, block_id, active = args
            ws = self.workspaces.get(program_id)
            if ws:
                if active:
                    ws.active_ids.add(block_id)
                else:
                    ws.active_ids.discard(block_id)
                ws.render()
        elif event == "clear_highlights":
            program_id = args[0]
            ws = self.workspaces.get(program_id)
            if ws:
                ws.active_ids.clear()
                ws.render()

    def _future_done(self, future, action: str) -> None:
        try:
            future.result()
        except Exception as exc:
            self.emit("log", f"ОШИБКА ({action}): {exc}")
            self.emit("status", f"Ошибка: {exc}", "error")
            self.emit("running", False)

    # ------------------------------------------------------------------
    # Styles / layout
    # ------------------------------------------------------------------

    def _build_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(8, 6), font=("Segoe UI", 9))
        style.configure("TLabel", font=("Segoe UI", 9))
        style.configure("TEntry", padding=4)

    def _build_ui(self):
        self._build_toolbar()

        body = tk.Frame(self.root, bg=self.bg)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.grid_columnconfigure(0, weight=0, minsize=250)
        body.grid_columnconfigure(1, weight=1)
        body.grid_columnconfigure(2, weight=0, minsize=365)
        body.grid_rowconfigure(0, weight=1)

        palette = tk.Frame(
            body, bg=self.panel, highlightbackground=self.border, highlightthickness=1
        )
        palette.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._build_palette(palette)

        center = tk.Frame(
            body, bg=self.panel, highlightbackground=self.border, highlightthickness=1
        )
        center.grid(row=0, column=1, sticky="nsew", padx=4)
        self.notebook = ttk.Notebook(center)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._tab_changed())

        right = tk.Frame(
            body, bg=self.panel, highlightbackground=self.border, highlightthickness=1
        )
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=3)
        right.grid_rowconfigure(3, weight=2)

        tk.Label(
            right, text="ПАРАМЕТРЫ БЛОКА",
            bg=self.panel, fg=self.text,
            font=("Segoe UI Semibold", 11)
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 5))

        self.inspector_scroll = ScrollFrame(right, bg=self.panel)
        self.inspector_scroll.grid(row=1, column=0, sticky="nsew", padx=8)

        ttk.Separator(right).grid(row=2, column=0, sticky="ew", padx=8, pady=6)

        log_frame = tk.Frame(right, bg=self.panel)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        log_frame.grid_rowconfigure(2, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        tk.Label(
            log_frame, text="BLE / ЖУРНАЛ",
            bg=self.panel, fg=self.text,
            font=("Segoe UI Semibold", 10)
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))

        io = tk.Frame(log_frame, bg=self.panel)
        io.grid(row=1, column=0, sticky="ew")
        io.grid_columnconfigure(1, weight=1)

        self.tx_var = tk.StringVar(value="—")
        self.rx_var = tk.StringVar(value="—")
        tk.Label(io, text="TX", bg=self.panel, fg=self.muted,
                 font=("Consolas", 8, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(io, textvariable=self.tx_var, bg=self.panel, fg=self.text,
                 font=("Consolas", 8), anchor="w").grid(row=0, column=1, sticky="ew", padx=5)
        tk.Label(io, text="RX", bg=self.panel, fg=self.muted,
                 font=("Consolas", 8, "bold")).grid(row=1, column=0, sticky="w")
        tk.Label(io, textvariable=self.rx_var, bg=self.panel, fg=self.text,
                 font=("Consolas", 8), anchor="w").grid(row=1, column=1, sticky="ew", padx=5)

        self.log_text = tk.Text(
            log_frame, height=12, wrap="word", state="disabled",
            bg="#0F172A", fg="#D1E7DD",
            font=("Consolas", 8), relief="flat", padx=7, pady=7
        )
        self.log_text.grid(row=2, column=0, sticky="nsew", pady=(5, 0))

    def _build_toolbar(self):
        bar = tk.Frame(
            self.root, bg=self.panel, height=64,
            highlightbackground=self.border, highlightthickness=1
        )
        bar.pack(fill="x", padx=12, pady=12)
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=self.panel)
        left.pack(side="left", fill="y", padx=8)
        tk.Label(
            left, text="HQ_BLE Studio",
            bg=self.panel, fg=self.text,
            font=("Segoe UI Semibold", 14)
        ).pack(side="left", padx=(0, 12), pady=14)

        self.connect_btn = ttk.Button(left, text="Подключить", command=self.connect)
        self.connect_btn.pack(side="left", padx=2, pady=12)
        self.disconnect_btn = ttk.Button(left, text="Отключить", command=self.disconnect)
        self.disconnect_btn.pack(side="left", padx=2, pady=12)
        self.run_btn = ttk.Button(left, text="▶ Запустить", command=self.run_program)
        self.run_btn.pack(side="left", padx=(12, 2), pady=12)
        self.stop_btn = ttk.Button(left, text="■ СТОП", command=self.emergency_stop)
        self.stop_btn.pack(side="left", padx=2, pady=12)

        mid = tk.Frame(bar, bg=self.panel)
        mid.pack(side="left", fill="y", padx=12)
        for text, cmd in (
            ("+ Вкладка", lambda: self.new_program()),
            ("Переименовать", self.rename_program),
            ("Закрыть вкладку", self.close_program),
            ("Переменные", self.open_variables),
            ("Пульт", self.open_controls),
        ):
            ttk.Button(mid, text=text, command=cmd).pack(side="left", padx=2, pady=12)

        right = tk.Frame(bar, bg=self.panel)
        right.pack(side="right", fill="y", padx=8)
        for text, cmd in (
            ("Открыть проект", self.open_project),
            ("Сохранить", self.save_project),
            ("Сохранить как", lambda: self.save_project(as_new=True)),
        ):
            ttk.Button(right, text=text, command=cmd).pack(side="left", padx=2, pady=12)

        status = tk.Frame(bar, bg=self.panel)
        status.pack(side="right", fill="y", padx=10)
        self.status_dot = tk.Label(
            status, text="●", bg=self.panel, fg="#9CA3AF",
            font=("Segoe UI", 13)
        )
        self.status_dot.pack(side="left", pady=17)
        self.status_var = tk.StringVar(value="Не подключено")
        tk.Label(
            status, textvariable=self.status_var,
            bg=self.panel, fg=self.text
        ).pack(side="left", padx=4, pady=17)

        self.refresh_toolbar()

    def _build_palette(self, parent):
        scroll = ScrollFrame(parent, bg=self.panel)
        scroll.pack(fill="both", expand=True)
        inner = scroll.inner

        tk.Label(
            inner, text="БЛОКИ",
            bg=self.panel, fg=self.text,
            font=("Segoe UI Semibold", 11)
        ).pack(anchor="w", padx=12, pady=(10, 7))

        groups = [
            ("Движение", [
                ("forward", "Вперёд"), ("backward", "Назад"),
                ("left", "Налево"), ("right", "Направо"), ("stop", "Стоп"),
            ]),
            ("Поток", [
                ("wait", "Пауза"),
                ("repeat_start", "Повторить"), ("repeat_end", "Конец повтора"),
                ("parallel_start", "Параллельно"),
                ("parallel_branch", "Новая ветка"),
                ("parallel_end", "Конец параллели"),
            ]),
            ("Переменные / массивы", [
                ("var_set", "Задать переменную"),
                ("var_change", "Изменить переменную"),
                ("var_math", "Арифметика с переменной"),
                ("array_set", "Задать элемент массива"),
                ("array_append", "Добавить в массив"),
                ("array_insert", "Вставить в массив"),
                ("array_pop", "Удалить из массива"),
                ("log_expr", "Вывести в журнал"),
            ]),
            ("Элементы управления", [
                ("control_define", "Создать управление"),
                ("control_read", "Считать управление"),
                ("control_write", "Задать управление"),
            ]),
            ("Экран / звук", [
                ("matrix", "Матрица 5×5"),
                ("marquee", "Бегущая строка"),
                ("clear_matrix", "Очистить матрицу"),
                ("melody", "Мелодия"),
            ]),
            ("Файлы", [
                ("file_read", "Прочитать файл"),
                ("file_write", "Записать файл"),
                ("file_list", "Список файлов"),
            ]),
            ("Дополнительно", [("raw", "HEX-команда")]),
        ]

        for group, items in groups:
            tk.Label(
                inner, text=group.upper(),
                bg=self.panel, fg=self.muted,
                font=("Segoe UI Semibold", 8)
            ).pack(anchor="w", padx=12, pady=(8, 3))
            for kind, label in items:
                color = BLOCK_META[kind][1]
                tk.Button(
                    inner, text="+  " + label,
                    command=lambda k=kind: self.add_block(k),
                    bg=color, fg="white",
                    activebackground=color, activeforeground="white",
                    relief="flat", bd=0, cursor="hand2",
                    anchor="w", padx=9, pady=6,
                    font=("Segoe UI Semibold", 9)
                ).pack(fill="x", padx=10, pady=2)

    # ------------------------------------------------------------------
    # Program tabs
    # ------------------------------------------------------------------

    def active_workspace(self) -> Optional[ProgramWorkspace]:
        current = self.notebook.select()
        if not current:
            return None
        widget = self.root.nametowidget(current)
        return widget if isinstance(widget, ProgramWorkspace) else None

    def new_program(self, name: Optional[str] = None, demo: bool = False):
        if name is None:
            name = f"Программа {len(self.programs) + 1}"
        program = {"id": str(uuid.uuid4()), "name": name, "blocks": []}

        if demo:
            program["blocks"] = [
                {
                    **new_block("var_set"),
                    "name": "screen",
                    "expr": "[[0,1,0,1,0],[1,1,1,1,1],[1,1,1,1,1],[0,1,1,1,0],[0,0,1,0,0]]",
                },
                {**new_block("matrix"), "source": "expr", "matrix_expr": "screen"},
                {
                    **new_block("control_define"),
                    "control_name": "delay", "label": "Пауза",
                    "control_kind": "slider", "min_expr": "0.05",
                    "max_expr": "2.0", "step_expr": "0.05",
                    "default_expr": "0.3", "bind_var": "delay",
                },
                new_block("parallel_start"),
                {
                    **new_block("marquee"), "text_expr": "'ПРИВЕТ'",
                    "frame_time_expr": "delay", "duration_expr": "4",
                },
                new_block("parallel_branch"),
                {**new_block("repeat_start"), "count_expr": "4"},
                {**new_block("forward"), "duration_expr": "0.4"},
                {**new_block("right"), "duration_expr": "0.25"},
                new_block("repeat_end"),
                new_block("parallel_end"),
                new_block("stop"),
            ]

        self.programs.append(program)
        ws = ProgramWorkspace(self.notebook, self, program)
        self.workspaces[program["id"]] = ws
        self.notebook.add(ws, text=program["name"])
        self.notebook.select(ws)
        self.render_inspector()
        self.refresh_toolbar()

    def rename_program(self):
        ws = self.active_workspace()
        if not ws:
            return
        name = simpledialog.askstring(
            "Переименовать", "Название программы:",
            initialvalue=ws.program["name"], parent=self.root
        )
        if name:
            ws.program["name"] = name
            self.notebook.tab(ws, text=name)

    def close_program(self):
        ws = self.active_workspace()
        if not ws:
            return
        if len(self.programs) == 1:
            messagebox.showinfo("Вкладки", "Должна остаться хотя бы одна программа.")
            return
        if not messagebox.askyesno(
            "Закрыть вкладку", f"Закрыть «{ws.program['name']}»?"
        ):
            return
        pid = ws.program["id"]
        self.notebook.forget(ws)
        self.workspaces.pop(pid, None)
        self.programs = [p for p in self.programs if p["id"] != pid]
        self.render_inspector()

    def _tab_changed(self):
        self.render_inspector()
        self.refresh_toolbar()

    # ------------------------------------------------------------------
    # BLE / execution
    # ------------------------------------------------------------------

    def connect(self):
        self.set_status("Поиск HQ_BLE…", "searching")
        fut = self.async_thread.submit(self.robot.connect())
        fut.add_done_callback(lambda f: self._future_done(f, "подключение"))

    def disconnect(self):
        if self.current_runner:
            self.current_runner.request_stop()
        fut = self.async_thread.submit(self.robot.disconnect())
        fut.add_done_callback(lambda f: self._future_done(f, "отключение"))

    def run_program(self):
        ws = self.active_workspace()
        if not ws or self.running:
            return
        try:
            validate_program(copy.deepcopy(ws.blocks))
        except Exception as exc:
            messagebox.showerror("Ошибка структуры", str(exc))
            return

        runner = ProgramRunner(self.robot, self.state, self.emit, ws.program["id"])
        self.current_runner = runner
        fut = self.async_thread.submit(runner.run(copy.deepcopy(ws.blocks)))
        fut.add_done_callback(lambda f: self._future_done(f, "выполнение"))

    def emergency_stop(self):
        if self.current_runner:
            self.current_runner.request_stop()
        self.emit("log", "!!! АВАРИЙНЫЙ СТОП !!!")
        if self.robot.connected:
            fut = self.async_thread.submit(self.robot.write(CMD_STOP))
            fut.add_done_callback(lambda f: self._future_done(f, "стоп"))

    # ------------------------------------------------------------------
    # Project files
    # ------------------------------------------------------------------

    def project_payload(self) -> dict[str, Any]:
        runtime = self.state.snapshot_for_project()
        return {
            "format": "hq_ble_robot_studio",
            "version": 2,
            "device": DEVICE_NAME,
            "programs": json_safe(self.programs),
            "variables": runtime["variables"],
            "controls": runtime["controls"],
        }

    def save_project(self, as_new: bool = False):
        if as_new or self.project_path is None:
            path = filedialog.asksaveasfilename(
                title="Сохранить проект",
                defaultextension=".hqstudio.json",
                filetypes=[
                    ("HQ BLE Studio", "*.hqstudio.json"),
                    ("JSON", "*.json"),
                    ("Все файлы", "*.*"),
                ]
            )
            if not path:
                return
            self.project_path = Path(path)

        self.project_path.write_text(
            json.dumps(self.project_payload(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        self.append_log(f"Проект сохранён: {self.project_path}")

    def open_project(self):
        path = filedialog.askopenfilename(
            title="Открыть проект",
            filetypes=[
                ("HQ BLE Studio", "*.hqstudio.json"),
                ("HQ Robot old", "*.hqrobot.json"),
                ("JSON", "*.json"),
                ("Все файлы", "*.*"),
            ]
        )
        if not path:
            return

        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))

            if isinstance(payload, dict) and "programs" in payload:
                programs = payload["programs"]
                state_payload = {
                    "variables": payload.get("variables", {}),
                    "controls": payload.get("controls", {}),
                }
            elif isinstance(payload, dict) and "blocks" in payload:
                programs = [{
                    "id": str(uuid.uuid4()),
                    "name": "Импортированная программа",
                    "blocks": payload["blocks"],
                }]
                state_payload = {"variables": {}, "controls": {}}
            else:
                raise ValueError("Неизвестный формат проекта")

            for ws in list(self.workspaces.values()):
                self.notebook.forget(ws)
            self.workspaces.clear()
            self.programs.clear()

            for program in programs:
                program = copy.deepcopy(program)
                program.setdefault("id", str(uuid.uuid4()))
                program.setdefault("name", "Программа")
                program.setdefault("blocks", [])
                for block in program["blocks"]:
                    block.setdefault("id", str(uuid.uuid4()))
                    self._migrate_old_block(block)
                self.programs.append(program)
                ws = ProgramWorkspace(self.notebook, self, program)
                self.workspaces[program["id"]] = ws
                self.notebook.add(ws, text=program["name"])

            if not self.programs:
                self.new_program()

            self.state.restore_from_project(state_payload)
            self.notebook.select(0)
            self.project_path = Path(path)
            self.render_inspector()
            self.refresh_toolbar()
            self.append_log(f"Проект открыт: {path}")
        except Exception as exc:
            messagebox.showerror("Ошибка открытия", str(exc))


    def _migrate_old_block(self, block: dict[str, Any]) -> None:
        """Совместимость проектов v1.x с новыми expression-полями v2."""
        kind = block.get("type")
        if kind in ("forward", "backward", "left", "right", "wait"):
            if "duration_expr" not in block:
                block["duration_expr"] = str(block.get("duration", 1.0))
        elif kind == "repeat_start":
            if "count_expr" not in block:
                block["count_expr"] = str(block.get("count", 2))
        elif kind == "melody":
            if "number_expr" not in block:
                block["number_expr"] = str(block.get("number", 0))
        elif kind == "marquee":
            if "text_expr" not in block:
                block["text_expr"] = repr(block.get("text", "ПРИВЕТ"))
            if "frame_time_expr" not in block:
                block["frame_time_expr"] = str(block.get("frame_time", 0.15))
            if "duration_expr" not in block:
                block["duration_expr"] = str(block.get("duration", 0.0))
        elif kind == "matrix":
            block.setdefault("source", "manual")
            block.setdefault("matrix_expr", "screen")

    # ------------------------------------------------------------------
    # Variables / controls windows
    # ------------------------------------------------------------------

    def open_variables(self):
        if self.variables_window:
            try:
                self.variables_window.win.lift()
                return
            except tk.TclError:
                self.variables_window = None
        self.variables_window = VariablesWindow(self)

    def open_controls(self):
        if self.controls_window:
            try:
                self.controls_window.win.lift()
                return
            except tk.TclError:
                self.controls_window = None
        self.controls_window = ControlsWindow(self)

    # ------------------------------------------------------------------
    # Block editor entry point
    # ------------------------------------------------------------------

    def add_block(self, kind: str):
        ws = self.active_workspace()
        if ws:
            ws.add_block(kind)

    # ------------------------------------------------------------------
    # Inspector
    # ------------------------------------------------------------------

    def render_inspector(self):
        inner = self.inspector_scroll.inner
        for child in inner.winfo_children():
            child.destroy()

        ws = self.active_workspace()
        block = ws.selected_block() if ws else None
        if not block:
            tk.Label(
                inner,
                text=(
                    "Выберите блок.\n\n"
                    "В выражениях можно использовать переменные, массивы и control('имя')."
                ),
                bg=self.panel, fg=self.muted, justify="left",
                wraplength=325
            ).pack(anchor="w", padx=6, pady=8)
            return

        kind = block["type"]
        color = BLOCK_META[kind][1]
        tk.Label(
            inner, text=BLOCK_META[kind][0],
            bg=color, fg="white",
            font=("Segoe UI Semibold", 10),
            padx=9, pady=6
        ).pack(fill="x", padx=4, pady=(4, 10))

        fields: dict[str, tk.Variable] = {}

        def entry(label: str, key: str, default: str = ""):
            tk.Label(inner, text=label, bg=self.panel, fg=self.text).pack(
                anchor="w", padx=5, pady=(4, 1)
            )
            var = tk.StringVar(value=str(block.get(key, default)))
            ttk.Entry(inner, textvariable=var).pack(fill="x", padx=5)
            fields[key] = var
            return var

        def combo(label: str, key: str, values: list[str], default: str):
            tk.Label(inner, text=label, bg=self.panel, fg=self.text).pack(
                anchor="w", padx=5, pady=(4, 1)
            )
            var = tk.StringVar(value=str(block.get(key, default)))
            ttk.Combobox(
                inner, textvariable=var, values=values, state="readonly"
            ).pack(fill="x", padx=5)
            fields[key] = var
            return var

        def check(label: str, key: str, default=False):
            var = tk.BooleanVar(value=bool(block.get(key, default)))
            ttk.Checkbutton(inner, text=label, variable=var).pack(
                anchor="w", padx=5, pady=4
            )
            fields[key] = var
            return var

        def apply_fields(extra: Optional[Callable[[], None]] = None):
            for key, var in fields.items():
                block[key] = var.get()
            if extra:
                extra()
            ws.render()
            self.render_inspector()

        if kind in ("forward", "backward", "left", "right", "wait"):
            entry("Продолжительность — выражение, сек.", "duration_expr", "1.0")
            self._expression_help(inner)
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "repeat_start":
            entry("Количество повторов — выражение", "count_expr", "2")
            self._expression_help(inner)
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind in ("repeat_end", "parallel_start", "parallel_branch", "parallel_end"):
            descriptions = {
                "repeat_end": "Закрывает ближайший блок «ПОВТОРИТЬ».",
                "parallel_start": (
                    "Начало параллельной секции. Всё до «НОВАЯ ВЕТКА» — первая ветка."
                ),
                "parallel_branch": (
                    "Разделяет параллельные ветки. Они запускаются конкурентно через asyncio.gather."
                ),
                "parallel_end": "Завершает параллельную секцию.",
            }
            tk.Label(
                inner, text=descriptions[kind],
                bg=self.panel, fg=self.muted,
                justify="left", wraplength=325
            ).pack(anchor="w", padx=5)

        elif kind == "var_set":
            entry("Имя переменной", "name", "x")
            entry("Значение / выражение", "expr", "0")
            self._expression_help(inner)
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "var_change":
            entry("Имя переменной", "name", "x")
            entry("Прибавить — выражение", "expr", "1")
            self._expression_help(inner)
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "var_math":
            entry("Имя переменной", "name", "x")
            combo("Операция", "op", ["+", "-", "*", "/", "//", "%", "**"], "*")
            entry("Операнд — выражение", "expr", "2")
            self._expression_help(inner)
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "array_set":
            entry("Имя массива", "name", "arr")
            entry("Индекс / путь индексов", "index_expr", "0")
            entry("Новое значение", "value_expr", "0")
            tk.Label(
                inner,
                text=(
                    "Для вложенного массива используйте путь, например [2, 3]. "
                    "Это означает arr[2][3]."
                ),
                bg=self.panel, fg=self.muted, wraplength=325, justify="left"
            ).pack(anchor="w", padx=5, pady=5)
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "array_append":
            entry("Имя массива", "name", "arr")
            entry("Добавить значение", "value_expr", "0")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "array_insert":
            entry("Имя массива", "name", "arr")
            entry("Индекс вставки — выражение", "index_expr", "0")
            entry("Вставить значение", "value_expr", "0")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "array_pop":
            entry("Имя массива", "name", "arr")
            entry("Индекс удаления — выражение", "index_expr", "-1")
            entry("Сохранить удалённое в переменную (можно пусто)", "target", "")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "log_expr":
            entry("Выражение", "expr", "x")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "control_define":
            entry("Имя элемента", "control_name", "speed")
            entry("Подпись", "label", "Скорость")
            combo(
                "Тип", "control_kind",
                ["slider", "number", "checkbox", "text", "button"], "slider"
            )
            entry("Минимум — выражение", "min_expr", "0")
            entry("Максимум — выражение", "max_expr", "100")
            entry("Шаг — выражение", "step_expr", "1")
            entry("Начальное значение — выражение", "default_expr", "50")
            entry("Привязать к переменной (можно пусто)", "bind_var", "speed")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "control_read":
            entry("Имя элемента управления", "control_name", "speed")
            entry("Записать в переменную", "target", "speed")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "control_write":
            entry("Имя элемента управления", "control_name", "speed")
            entry("Новое значение — выражение", "value_expr", "50")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "matrix":
            source_var = combo(
                "Источник", "source", ["manual", "expr"], block.get("source", "manual")
            )
            matrix_expr_var = entry(
                "Выражение / переменная матрицы", "matrix_expr", "screen"
            )

            tk.Label(
                inner,
                text=(
                    "Переменная может быть [10,31,31,14,4] или массивом массивов 5×5. "
                    "Например screen = [[0,1,0,1,0], ...]."
                ),
                bg=self.panel, fg=self.muted,
                wraplength=325, justify="left"
            ).pack(anchor="w", padx=5, pady=5)

            grid = tk.Frame(inner, bg=self.panel)
            grid.pack(anchor="w", padx=5, pady=6)
            rows = [int(x) & 31 for x in block.get("rows", [0]*5)]
            buttons: list[list[tk.Button]] = []

            def refresh_matrix():
                for y in range(5):
                    for x in range(5):
                        on = bool(rows[y] & (1 << (4 - x)))
                        buttons[y][x].configure(
                            bg="#E53935" if on else "#E5E7EB",
                            activebackground="#EF5350" if on else "#D1D5DB"
                        )

            def toggle(y, x):
                rows[y] ^= 1 << (4 - x)
                block["rows"] = rows
                refresh_matrix()

            for y in range(5):
                line = []
                for x in range(5):
                    btn = tk.Button(
                        grid, width=2, height=1, relief="flat", bd=0,
                        command=lambda yy=y, xx=x: toggle(yy, xx)
                    )
                    btn.grid(row=y, column=x, padx=2, pady=2, ipadx=5, ipady=5)
                    line.append(btn)
                buttons.append(line)
            refresh_matrix()

            def apply_matrix():
                block["source"] = source_var.get()
                block["matrix_expr"] = matrix_expr_var.get()
                block["rows"] = rows
                ws.render()
                self.render_inspector()

            ttk.Button(inner, text="Применить", command=apply_matrix).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "marquee":
            entry("Текст — выражение", "text_expr", "'ПРИВЕТ'")
            entry("Интервал кадра — выражение, сек.", "frame_time_expr", "0.15")
            entry("Общая длительность — выражение (0 = один проход)", "duration_expr", "0")
            self._expression_help(inner)
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "clear_matrix":
            tk.Label(
                inner, text="Отправляет 03 00 00 00 00 00",
                bg=self.panel, fg=self.muted, font=("Consolas", 9)
            ).pack(anchor="w", padx=5)

        elif kind == "melody":
            entry("Номер мелодии — выражение (0…20)", "number_expr", "0")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "file_read":
            entry("Путь — выражение", "path_expr", "'data.json'")
            entry("Переменная результата", "target", "data")
            combo("Формат", "mode", ["text", "json", "lines"], "json")
            tk.Label(
                inner,
                text="JSON автоматически превращается в переменные/вложенные массивы Python.",
                bg=self.panel, fg=self.muted, wraplength=325, justify="left"
            ).pack(anchor="w", padx=5, pady=4)
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "file_write":
            entry("Путь — выражение", "path_expr", "'data.json'")
            entry("Значение — выражение", "value_expr", "data")
            combo("Формат", "mode", ["text", "json"], "json")
            check("Добавлять в конец", "append", False)
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "file_list":
            entry("Папка — выражение", "path_expr", "'.'")
            entry("Переменная результата", "target", "files")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        elif kind == "raw":
            entry("HEX-байты", "hex", "00")
            ttk.Button(inner, text="Применить", command=lambda: apply_fields()).pack(
                anchor="w", padx=5, pady=8
            )

        ttk.Separator(inner).pack(fill="x", padx=5, pady=10)
        actions = tk.Frame(inner, bg=self.panel)
        actions.pack(fill="x", padx=5, pady=(0, 12))
        ttk.Button(actions, text="Дублировать", command=ws.duplicate_selected).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(actions, text="Удалить", command=ws.delete_selected).pack(side="left")

    def _expression_help(self, parent):
        tk.Label(
            parent,
            text=(
                "Выражения: x*2+1, arr[0], matrix[2][3], control('speed'), "
                "len(arr), sum(arr), zeros(5,5), [[0,1],[1,0]]."
            ),
            bg=self.panel, fg=self.muted,
            wraplength=325, justify="left",
            font=("Segoe UI", 8)
        ).pack(anchor="w", padx=5, pady=5)

    # ------------------------------------------------------------------
    # Log / status / close
    # ------------------------------------------------------------------

    def append_log(self, text: str):
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_status(self, text: str, kind: str):
        colors = {
            "connected": "#22C55E",
            "searching": "#F59E0B",
            "error": "#EF4444",
            "disconnected": "#9CA3AF",
        }
        self.status_var.set(text)
        self.status_dot.configure(fg=colors.get(kind, "#9CA3AF"))

    def refresh_toolbar(self):
        if not hasattr(self, "connect_btn"):
            return
        self.connect_btn.configure(state="disabled" if self.connected else "normal")
        self.disconnect_btn.configure(state="normal" if self.connected else "disabled")
        ws = self.active_workspace() if hasattr(self, "notebook") else None
        self.run_btn.configure(
            state="normal"
            if (self.connected and not self.running and ws and ws.blocks)
            else "disabled"
        )
        self.stop_btn.configure(state="normal" if self.connected else "disabled")

    def _on_close(self):
        if self.current_runner:
            self.current_runner.request_stop()
        if self.robot.connected:
            fut = self.async_thread.submit(self.robot.disconnect())
            try:
                fut.result(timeout=2.0)
            except Exception:
                pass
        self.async_thread.shutdown()
        self.root.destroy()
