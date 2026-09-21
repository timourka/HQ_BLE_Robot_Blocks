# -*- coding: utf-8 -*-
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from ui_helpers import ScrollFrame, short_repr


class VariablesWindow:
    def __init__(self, app) -> None:
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Переменные")
        self.win.geometry("760x460")
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        toolbar = tk.Frame(self.win)
        toolbar.pack(fill="x", padx=8, pady=8)
        ttk.Button(toolbar, text="Добавить", command=self.add).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Изменить", command=self.edit).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Удалить", command=self.delete).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Обновить", command=self.refresh).pack(side="left", padx=3)

        tk.Label(
            self.win,
            text=(
                "Значения вводятся как выражения: 10, 'текст', [1,2,3], "
                "[[0,1],[1,0]], zeros(5,5). Значения обновляются и во время работы программы."
            ),
            anchor="w", justify="left", wraplength=730
        ).pack(fill="x", padx=10, pady=(0, 6))

        self.tree = ttk.Treeview(
            self.win, columns=("type", "value"), show="tree headings"
        )
        self.tree.heading("#0", text="Имя")
        self.tree.heading("type", text="Тип")
        self.tree.heading("value", text="Значение")
        self.tree.column("#0", width=170)
        self.tree.column("type", width=120)
        self.tree.column("value", width=430)
        self.tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree.bind("<Double-1>", lambda _e: self.edit())
        self.refresh()

    def close(self):
        self.app.variables_window = None
        self.win.destroy()

    def refresh(self):
        if not self.win.winfo_exists():
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        for name, value in sorted(self.app.state.variable_snapshot().items()):
            self.tree.insert(
                "", "end", iid=name, text=name,
                values=(type(value).__name__, short_repr(value, 300))
            )

    def add(self):
        name = simpledialog.askstring("Новая переменная", "Имя:", parent=self.win)
        if not name:
            return
        expr = simpledialog.askstring(
            "Новая переменная", "Начальное значение / выражение:",
            initialvalue="0", parent=self.win
        )
        if expr is None:
            return
        try:
            value = self.app.evaluator.eval(expr)
            self.app.state.set_var(name, value)
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self.win)

    def edit(self):
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        current = self.app.state.get_var(name)
        expr = simpledialog.askstring(
            "Изменить переменную",
            f"{name} =",
            initialvalue=repr(current),
            parent=self.win
        )
        if expr is None:
            return
        try:
            self.app.state.set_var(name, self.app.evaluator.eval(expr))
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self.win)

    def delete(self):
        sel = self.tree.selection()
        if sel:
            self.app.state.del_var(sel[0])


class ControlsWindow:
    def __init__(self, app) -> None:
        self.app = app
        self.syncing = False
        self.widget_vars: dict[str, tuple[str, tk.Variable, object]] = {}

        self.win = tk.Toplevel(app.root)
        self.win.title("Пульт управления")
        self.win.geometry("650x640")
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        toolbar = tk.Frame(self.win)
        toolbar.pack(fill="x", padx=8, pady=8)
        ttk.Button(toolbar, text="Добавить элемент", command=self.add_control).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="Обновить", command=self.refresh).pack(
            side="left", padx=3
        )

        tk.Label(
            self.win,
            text=(
                "Программа может читать значения блоком «СЧИТАТЬ УПРАВЛЕНИЕ» или прямо "
                "в выражении через control('имя'). При привязке к переменной значение "
                "попадает в неё сразу."
            ),
            justify="left", anchor="w", wraplength=620
        ).pack(fill="x", padx=10, pady=(0, 7))

        self.scroll = ScrollFrame(self.win, bg="#F5F7FA")
        self.scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.refresh()

    def close(self):
        self.app.controls_window = None
        self.win.destroy()

    def add_control(self):
        name = simpledialog.askstring(
            "Элемент управления", "Имя (например speed):", parent=self.win
        )
        if not name:
            return
        kind = simpledialog.askstring(
            "Элемент управления",
            "Тип: slider / number / checkbox / text / button",
            initialvalue="slider", parent=self.win
        )
        if not kind:
            return
        kind = kind.strip().lower()
        if kind not in {"slider", "number", "checkbox", "text", "button"}:
            messagebox.showerror("Ошибка", "Неизвестный тип", parent=self.win)
            return

        bind_var = simpledialog.askstring(
            "Элемент управления",
            "Привязанная переменная (можно пусто):",
            initialvalue=name, parent=self.win
        ) or ""

        try:
            if kind in ("slider", "number"):
                minimum = float(simpledialog.askstring(
                    "Минимум", "Минимум:", initialvalue="0", parent=self.win
                ) or "0")
                maximum = float(simpledialog.askstring(
                    "Максимум", "Максимум:", initialvalue="100", parent=self.win
                ) or "100")
                step = float(simpledialog.askstring(
                    "Шаг", "Шаг:", initialvalue="1", parent=self.win
                ) or "1")
                default = float(simpledialog.askstring(
                    "Значение", "Начальное значение:", initialvalue="0", parent=self.win
                ) or "0")
            elif kind == "checkbox":
                minimum, maximum, step, default = 0, 1, 1, False
            elif kind == "button":
                minimum, maximum, step, default = 0, 1, 1, 0
            else:
                minimum, maximum, step = 0, 1, 1
                default = simpledialog.askstring(
                    "Текст", "Начальный текст:", initialvalue="", parent=self.win
                ) or ""

            self.app.state.ensure_control(
                name, label=name, kind=kind,
                minimum=minimum, maximum=maximum, step=step,
                default=default, bind_var=bind_var
            )
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self.win)

    def refresh(self):
        if not self.win.winfo_exists():
            return
        inner = self.scroll.inner
        for child in inner.winfo_children():
            child.destroy()
        self.widget_vars.clear()

        controls = self.app.state.controls_snapshot()
        if not controls:
            tk.Label(
                inner,
                text=(
                    "Элементов управления пока нет.\n\n"
                    "Их можно создать здесь или блоком «ЭЛЕМЕНТ УПРАВЛЕНИЯ»."
                ),
                bg="#F5F7FA", fg="#6B7280", justify="center"
            ).pack(pady=70)
            return

        for name, spec in controls.items():
            card = tk.Frame(
                inner, bg="white", highlightbackground="#D9DEE6",
                highlightthickness=1
            )
            card.pack(fill="x", padx=8, pady=6)

            top = tk.Frame(card, bg="white")
            top.pack(fill="x", padx=10, pady=(8, 4))
            tk.Label(
                top, text=spec.get("label", name),
                bg="white", font=("Segoe UI Semibold", 10)
            ).pack(side="left")
            tk.Label(
                top, text=f"[{name}]",
                bg="white", fg="#6B7280", font=("Consolas", 9)
            ).pack(side="left", padx=6)
            ttk.Button(
                top, text="Удалить",
                command=lambda n=name: self.app.state.del_control(n)
            ).pack(side="right")

            body = tk.Frame(card, bg="white")
            body.pack(fill="x", padx=10, pady=(0, 10))

            kind = spec["kind"]
            value = spec.get("value")

            if kind == "slider":
                value_var = tk.DoubleVar(value=float(value))
                val_label = tk.Label(
                    body, text=f"{float(value):g}", bg="white", width=10
                )
                val_label.pack(side="right")
                self.widget_vars[name] = (kind, value_var, val_label)

                def on_slider(v, n=name, lbl=val_label):
                    if self.syncing:
                        return
                    number = float(v)
                    lbl.configure(text=f"{number:g}")
                    self.app.state.set_control(n, number)

                tk.Scale(
                    body, from_=spec["min"], to=spec["max"],
                    resolution=spec["step"], orient="horizontal",
                    variable=value_var, command=on_slider,
                    bg="white", highlightthickness=0
                ).pack(side="left", fill="x", expand=True)

            elif kind == "number":
                var = tk.StringVar(value=str(value))
                self.widget_vars[name] = (kind, var, None)
                spin = ttk.Spinbox(
                    body, from_=spec["min"], to=spec["max"],
                    increment=spec["step"], textvariable=var
                )
                spin.pack(side="left", fill="x", expand=True)

                def commit_number(_e=None, n=name, v=var):
                    if self.syncing:
                        return
                    try:
                        self.app.state.set_control(n, float(v.get()))
                    except Exception:
                        pass

                spin.bind("<Return>", commit_number)
                spin.bind("<FocusOut>", commit_number)

            elif kind == "checkbox":
                var = tk.BooleanVar(value=bool(value))
                self.widget_vars[name] = (kind, var, None)

                def checkbox_changed(n=name, v=var):
                    if not self.syncing:
                        self.app.state.set_control(n, v.get())

                ttk.Checkbutton(
                    body, text="Включено", variable=var,
                    command=checkbox_changed
                ).pack(side="left")

            elif kind == "text":
                var = tk.StringVar(value=str(value))
                self.widget_vars[name] = (kind, var, None)
                entry = ttk.Entry(body, textvariable=var)
                entry.pack(fill="x", expand=True)

                def text_changed(*_a, n=name, v=var):
                    if not self.syncing:
                        self.app.state.set_control(n, v.get())

                var.trace_add("write", text_changed)

            elif kind == "button":
                count = int(value or 0)
                label = tk.StringVar(value=f"Нажать   (счётчик: {count})")
                self.widget_vars[name] = (kind, label, None)

                def click(n=name, lbl=label):
                    current = int(self.app.state.get_control(n))
                    self.app.state.set_control(n, current + 1)
                    lbl.set(f"Нажать   (счётчик: {current + 1})")

                ttk.Button(body, textvariable=label, command=click).pack(
                    side="left", fill="x", expand=True
                )

            bind_var = spec.get("bind_var", "")
            if bind_var:
                tk.Label(
                    card, text=f"↳ переменная: {bind_var}",
                    bg="white", fg="#6B7280", font=("Segoe UI", 8)
                ).pack(anchor="w", padx=10, pady=(0, 7))

    def sync_values(self):
        """Обновляет значения без разрушения виджетов — важно во время drag слайдера."""
        if not self.win.winfo_exists():
            return
        controls = self.app.state.controls_snapshot()
        self.syncing = True
        try:
            for name, entry in self.widget_vars.items():
                spec = controls.get(name)
                if not spec:
                    continue
                kind, var, extra = entry
                value = spec.get("value")
                if kind == "slider":
                    var.set(float(value))
                    if extra is not None:
                        extra.configure(text=f"{float(value):g}")
                elif kind == "number":
                    var.set(str(value))
                elif kind == "checkbox":
                    var.set(bool(value))
                elif kind == "text":
                    var.set(str(value))
                elif kind == "button":
                    var.set(f"Нажать   (счётчик: {int(value or 0)})")
        finally:
            self.syncing = False
