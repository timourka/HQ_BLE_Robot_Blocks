# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import tkinter as tk
from typing import Any, Optional

from runtime import BLOCK_META, block_title, new_block
from ui_helpers import ScrollFrame


class ProgramWorkspace(tk.Frame):
    """
    Одна вкладка программы.

    Drag&drop реализован как live-preview:
    - исходный блок исчезает из списка;
    - жёлтый placeholder двигается по списку во время B1-Motion;
    - соседние блоки перестраиваются до отпускания мыши.
    """

    def __init__(self, master, app, program: dict[str, Any]):
        super().__init__(master, bg=app.panel)
        self.app = app
        self.program = program
        self.selected_id: Optional[str] = None
        self.active_ids: set[str] = set()

        self.drag_id: Optional[str] = None
        self.drag_target = 0
        self.drag_rows: list[tk.Widget] = []
        self.drag_ghost: Optional[tk.Toplevel] = None

        hint = tk.Label(
            self,
            text=(
                "Live drag&drop: во время перетаскивания список перестраивается, "
                "а жёлтый маркер показывает точное место вставки."
            ),
            bg=app.panel, fg=app.muted, justify="left", anchor="w",
            font=("Segoe UI", 9),
        )
        hint.pack(fill="x", padx=12, pady=(10, 6))

        self.scroll = ScrollFrame(self, bg="#F5F7FA")
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.render()

    @property
    def blocks(self) -> list[dict[str, Any]]:
        return self.program["blocks"]

    def select(self, block_id: Optional[str]) -> None:
        self.selected_id = block_id
        self.render()
        self.app.render_inspector()

    def selected_block(self) -> Optional[dict[str, Any]]:
        if not self.selected_id:
            return None
        return next((b for b in self.blocks if b["id"] == self.selected_id), None)

    def add_block(self, kind: str) -> None:
        block = new_block(kind)
        if self.selected_id:
            idx = next(
                (i for i, b in enumerate(self.blocks) if b["id"] == self.selected_id),
                len(self.blocks) - 1
            )
            self.blocks.insert(idx + 1, block)
        else:
            self.blocks.append(block)
        self.selected_id = block["id"]
        self.render()
        self.app.render_inspector()
        self.app.refresh_toolbar()

    def delete_selected(self) -> None:
        if not self.selected_id:
            return
        self.program["blocks"] = [b for b in self.blocks if b["id"] != self.selected_id]
        self.selected_id = None
        self.render()
        self.app.render_inspector()
        self.app.refresh_toolbar()

    def duplicate_selected(self) -> None:
        block = self.selected_block()
        if not block:
            return
        clone = copy.deepcopy(block)
        import uuid
        clone["id"] = str(uuid.uuid4())
        idx = self.blocks.index(block)
        self.blocks.insert(idx + 1, clone)
        self.selected_id = clone["id"]
        self.render()
        self.app.render_inspector()

    def depths(self) -> dict[str, int]:
        depth = 0
        result: dict[str, int] = {}
        for block in self.blocks:
            kind = block["type"]
            if kind in ("repeat_end", "parallel_end", "parallel_branch"):
                depth = max(0, depth - 1)
            result[block["id"]] = depth
            if kind in ("repeat_start", "parallel_start", "parallel_branch"):
                depth += 1
        return result

    def render(self) -> None:
        inner = self.scroll.inner
        for child in inner.winfo_children():
            child.destroy()
        self.drag_rows = []

        if not self.blocks and not self.drag_id:
            tk.Label(
                inner,
                text="Программа пустая\n\nДобавьте блоки из палитры слева.",
                bg="#F5F7FA", fg=self.app.muted,
                font=("Segoe UI", 11), justify="center"
            ).pack(fill="both", expand=True, pady=90)
            return

        depths = self.depths()

        if self.drag_id:
            remaining = [b for b in self.blocks if b["id"] != self.drag_id]
            sequence: list[tuple[str, Optional[dict[str, Any]]]] = []
            for pos in range(len(remaining) + 1):
                if pos == self.drag_target:
                    sequence.append(("placeholder", None))
                if pos < len(remaining):
                    sequence.append(("block", remaining[pos]))
        else:
            sequence = [("block", b) for b in self.blocks]

        display_number = 0
        for entry_kind, block in sequence:
            if entry_kind == "placeholder":
                holder = tk.Label(
                    inner,
                    text="⇩  ВСТАВИТЬ СЮДА  ⇩",
                    bg="#FFF3BF", fg="#8A5A00",
                    font=("Segoe UI Semibold", 10),
                    relief="solid", bd=1, pady=8
                )
                holder.pack(fill="x", padx=12, pady=4)
                continue

            assert block is not None
            display_number += 1
            depth = depths.get(block["id"], 0)
            outer = tk.Frame(inner, bg="#F5F7FA")
            outer.pack(fill="x", padx=(10 + depth * 24, 10), pady=4)
            self.drag_rows.append(outer)

            color = BLOCK_META[block["type"]][1]
            selected = block["id"] == self.selected_id
            active = block["id"] in self.active_ids

            card = tk.Frame(
                outer, bg=color,
                highlightbackground=(
                    "#111827" if selected else "#FFD54F" if active else color
                ),
                highlightthickness=3 if (selected or active) else 1,
                cursor="hand2",
            )
            card.pack(fill="x")

            handle = tk.Label(
                card, text="⋮⋮", bg=color, fg="white",
                font=("Segoe UI", 14), padx=8, cursor="fleur"
            )
            handle.pack(side="left", fill="y")

            title = tk.Label(
                card, text=block_title(block),
                bg=color, fg="white",
                font=("Segoe UI Semibold", 10),
                anchor="w", padx=5, pady=9
            )
            title.pack(side="left", fill="x", expand=True)

            number = tk.Label(
                card, text=str(display_number), bg=color, fg="#F5F5F5",
                font=("Segoe UI", 8), padx=7
            )
            number.pack(side="right")

            for widget in (card, handle, title, number):
                widget.bind(
                    "<Button-1>",
                    lambda e, bid=block["id"]: self.start_drag(bid, e)
                )

    def start_drag(self, block_id: str, event) -> None:
        if self.app.running:
            return
        self.selected_id = block_id
        source = next(i for i, b in enumerate(self.blocks) if b["id"] == block_id)
        self.drag_id = block_id
        self.drag_target = source
        self.app.render_inspector()

        ghost = tk.Toplevel(self)
        ghost.overrideredirect(True)
        try:
            ghost.attributes("-alpha", 0.88)
        except tk.TclError:
            pass
        block = next(b for b in self.blocks if b["id"] == block_id)
        color = BLOCK_META[block["type"]][1]
        tk.Label(
            ghost, text=block_title(block),
            bg=color, fg="white",
            font=("Segoe UI Semibold", 10),
            padx=14, pady=8, relief="solid", bd=1
        ).pack()
        self.drag_ghost = ghost
        ghost.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")

        self.render()
        top = self.winfo_toplevel()
        top.bind_all("<B1-Motion>", self._drag_motion)
        top.bind_all("<ButtonRelease-1>", self._drag_release)

    def _drag_motion(self, event) -> None:
        if not self.drag_id:
            return

        if self.drag_ghost:
            self.drag_ghost.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")

        cy = self.scroll.canvas.winfo_rooty()
        ch = self.scroll.canvas.winfo_height()
        if event.y_root < cy + 35:
            self.scroll.canvas.yview_scroll(-1, "units")
        elif event.y_root > cy + ch - 35:
            self.scroll.canvas.yview_scroll(1, "units")

        target = len(self.drag_rows)
        for i, row in enumerate(self.drag_rows):
            midpoint = row.winfo_rooty() + row.winfo_height() / 2
            if event.y_root < midpoint:
                target = i
                break

        if target != self.drag_target:
            self.drag_target = target
            self.render()
            self.scroll.canvas.update_idletasks()

    def _drag_release(self, _event) -> None:
        if not self.drag_id:
            return

        source_id = self.drag_id
        target = self.drag_target
        remaining = [b for b in self.blocks if b["id"] != source_id]
        block = next(b for b in self.blocks if b["id"] == source_id)
        target = max(0, min(target, len(remaining)))
        remaining.insert(target, block)
        self.program["blocks"] = remaining

        self.drag_id = None
        if self.drag_ghost:
            try:
                self.drag_ghost.destroy()
            except Exception:
                pass
        self.drag_ghost = None

        top = self.winfo_toplevel()
        top.unbind_all("<B1-Motion>")
        top.unbind_all("<ButtonRelease-1>")
        self.render()
