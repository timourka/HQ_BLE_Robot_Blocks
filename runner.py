# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from protocol import (
    CMD_STOP,
    CMD_FORWARD,
    CMD_BACKWARD,
    CMD_LEFT,
    CMD_RIGHT,
    MOVE_INTERVAL,
    matrix_packet,
    matrix_value_to_rows,
    text_to_matrix_frames,
)
from runtime import SharedState, SafeEvaluator, nested_set, json_safe, block_title, validate_program


class ProgramRunner:
    def __init__(
        self,
        robot,
        state: SharedState,
        emit: Callable[..., None],
        program_id: str,
    ) -> None:
        self.robot = robot
        self.state = state
        self.evalr = SafeEvaluator(state)
        self.emit = emit
        self.program_id = program_id
        self.stop_flag = threading.Event()

    def request_stop(self) -> None:
        self.stop_flag.set()

    def _eval(self, expr: Any) -> Any:
        return self.evalr.eval(expr)

    async def _sleep_interruptible(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            if self.stop_flag.is_set():
                return False
            await asyncio.sleep(max(0.0, min(0.05, deadline - time.monotonic())))
        return not self.stop_flag.is_set()

    async def _move(self, payload: bytes, duration: float) -> None:
        duration = max(0.0, float(duration))
        start = time.monotonic()
        await self.robot.write(payload)

        while time.monotonic() - start < duration:
            if self.stop_flag.is_set():
                break
            remaining = duration - (time.monotonic() - start)
            await asyncio.sleep(min(MOVE_INTERVAL, max(0.0, remaining)))
            if self.stop_flag.is_set():
                break
            if time.monotonic() - start < duration:
                await self.robot.write(payload)

        await self.robot.write(CMD_STOP)

    async def _marquee(self, text: str, frame_time: float, duration: float) -> None:
        frames = text_to_matrix_frames(text)
        frame_time = max(0.03, min(5.0, float(frame_time)))
        duration = max(0.0, float(duration))
        deadline = time.monotonic() + duration if duration > 0 else None

        while True:
            for rows in frames:
                if self.stop_flag.is_set():
                    return
                if deadline is not None and time.monotonic() >= deadline:
                    return
                await self.robot.write(matrix_packet(rows))
                sleep_for = frame_time
                if deadline is not None:
                    sleep_for = min(sleep_for, max(0.0, deadline - time.monotonic()))
                if not await self._sleep_interruptible(sleep_for):
                    return

            if deadline is None or time.monotonic() >= deadline:
                break

        if not self.stop_flag.is_set():
            await self.robot.write(matrix_packet([0, 0, 0, 0, 0]))

    def _highlight(self, block_id: str, active: bool) -> None:
        self.emit("highlight", self.program_id, block_id, active)

    async def _run_simple(self, block: dict[str, Any]) -> None:
        kind = block["type"]
        self._highlight(block["id"], True)
        self.emit("log", "▶ " + block_title(block))
        try:
            if kind == "forward":
                await self._move(CMD_FORWARD, float(self._eval(block["duration_expr"])))
            elif kind == "backward":
                await self._move(CMD_BACKWARD, float(self._eval(block["duration_expr"])))
            elif kind == "left":
                await self._move(CMD_LEFT, float(self._eval(block["duration_expr"])))
            elif kind == "right":
                await self._move(CMD_RIGHT, float(self._eval(block["duration_expr"])))
            elif kind == "stop":
                await self.robot.write(CMD_STOP)
            elif kind == "wait":
                await self._sleep_interruptible(float(self._eval(block["duration_expr"])))

            elif kind == "var_set":
                self.state.set_var(block["name"], self._eval(block["expr"]))
            elif kind == "var_change":
                current = self.state.get_var(block["name"])
                delta = self._eval(block["expr"])
                self.state.set_var(block["name"], current + delta)
            elif kind == "var_math":
                current = self.state.get_var(block["name"])
                value = self._eval(block["expr"])
                op = block.get("op", "*")
                operations = {
                    "+": lambda a, b: a + b,
                    "-": lambda a, b: a - b,
                    "*": lambda a, b: a * b,
                    "/": lambda a, b: a / b,
                    "//": lambda a, b: a // b,
                    "%": lambda a, b: a % b,
                    "**": lambda a, b: a ** b,
                }
                if op not in operations:
                    raise ValueError(f"Неизвестная арифметическая операция: {op}")
                self.state.set_var(block["name"], operations[op](current, value))
            elif kind == "array_set":
                current = self.state.get_var(block["name"])
                index_spec = self._eval(block["index_expr"])
                value = self._eval(block["value_expr"])
                self.state.set_var(block["name"], nested_set(current, index_spec, value))
            elif kind == "array_append":
                current = self.state.get_var(block["name"])
                if not isinstance(current, list):
                    raise TypeError(f"{block['name']} не является массивом")
                current.append(self._eval(block["value_expr"]))
                self.state.set_var(block["name"], current)
            elif kind == "array_insert":
                current = self.state.get_var(block["name"])
                if not isinstance(current, list):
                    raise TypeError(f"{block['name']} не является массивом")
                idx = int(self._eval(block["index_expr"]))
                current.insert(idx, self._eval(block["value_expr"]))
                self.state.set_var(block["name"], current)
            elif kind == "array_pop":
                current = self.state.get_var(block["name"])
                if not isinstance(current, list):
                    raise TypeError(f"{block['name']} не является массивом")
                idx = int(self._eval(block.get("index_expr", "-1")))
                popped = current.pop(idx)
                self.state.set_var(block["name"], current)
                target = str(block.get("target", "")).strip()
                if target:
                    self.state.set_var(target, popped)
            elif kind == "log_expr":
                value = self._eval(block["expr"])
                self.emit("log", "VALUE: " + repr(value))

            elif kind == "control_define":
                self.state.ensure_control(
                    block["control_name"],
                    label=block.get("label", block["control_name"]),
                    kind=block.get("control_kind", "slider"),
                    minimum=float(self._eval(block.get("min_expr", "0"))),
                    maximum=float(self._eval(block.get("max_expr", "100"))),
                    step=float(self._eval(block.get("step_expr", "1"))),
                    default=self._eval(block.get("default_expr", "0")),
                    bind_var=block.get("bind_var", ""),
                )
            elif kind == "control_read":
                self.state.set_var(
                    block["target"], self.state.get_control(block["control_name"])
                )
            elif kind == "control_write":
                self.state.set_control(
                    block["control_name"], self._eval(block["value_expr"])
                )

            elif kind == "matrix":
                if block.get("source") == "expr":
                    value = self._eval(block.get("matrix_expr", "screen"))
                    rows = matrix_value_to_rows(value)
                else:
                    rows = [int(x) & 31 for x in block.get("rows", [0] * 5)]
                await self.robot.write(matrix_packet(rows))
            elif kind == "clear_matrix":
                await self.robot.write(matrix_packet([0, 0, 0, 0, 0]))
            elif kind == "marquee":
                await self._marquee(
                    str(self._eval(block.get("text_expr", "''"))),
                    float(self._eval(block.get("frame_time_expr", "0.15"))),
                    float(self._eval(block.get("duration_expr", "0"))),
                )
            elif kind == "melody":
                number = int(self._eval(block.get("number_expr", "0")))
                number = max(0, min(20, number))
                await self.robot.write(bytes([0x02, number]))

            elif kind == "file_read":
                path = Path(str(self._eval(block["path_expr"]))).expanduser()
                mode = block.get("mode", "text")

                def read_file():
                    if mode == "json":
                        return json.loads(path.read_text(encoding="utf-8"))
                    if mode == "lines":
                        return path.read_text(encoding="utf-8").splitlines()
                    return path.read_text(encoding="utf-8")

                value = await asyncio.to_thread(read_file)
                self.state.set_var(block["target"], value)

            elif kind == "file_write":
                path = Path(str(self._eval(block["path_expr"]))).expanduser()
                value = self._eval(block["value_expr"])
                mode = block.get("mode", "text")
                append = bool(block.get("append", False))

                def write_file():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    text = (
                        json.dumps(json_safe(value), ensure_ascii=False, indent=2)
                        if mode == "json"
                        else str(value)
                    )
                    if append:
                        with path.open("a", encoding="utf-8") as f:
                            f.write(text)
                    else:
                        path.write_text(text, encoding="utf-8")

                await asyncio.to_thread(write_file)

            elif kind == "file_list":
                path = Path(str(self._eval(block["path_expr"]))).expanduser()

                def list_dir():
                    return sorted(p.name for p in path.iterdir())

                self.state.set_var(block["target"], await asyncio.to_thread(list_dir))

            elif kind == "raw":
                payload = bytes.fromhex(str(block.get("hex", "")).strip())
                await self.robot.write(payload)
        finally:
            self._highlight(block["id"], False)

    async def _run_nodes(self, nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if self.stop_flag.is_set():
                return

            if node["node"] == "block":
                await self._run_simple(node["block"])
                continue

            if node["node"] == "repeat":
                start = node["start"]
                self._highlight(start["id"], True)
                try:
                    count = max(0, int(self._eval(start.get("count_expr", "0"))))
                    for i in range(count):
                        if self.stop_flag.is_set():
                            return
                        self.emit("log", f"↻ Повтор {i + 1}/{count}")
                        await self._run_nodes(node["children"])
                finally:
                    self._highlight(start["id"], False)
                continue

            if node["node"] == "parallel":
                start = node["start"]
                self._highlight(start["id"], True)
                self.emit("log", f"⇉ Параллельно: {len(node['branches'])} веток")
                try:
                    await asyncio.gather(
                        *(self._run_nodes(branch) for branch in node["branches"])
                    )
                finally:
                    self._highlight(start["id"], False)
                continue

    async def run(self, blocks: list[dict[str, Any]]) -> None:
        if not self.robot.connected:
            raise RuntimeError("Сначала подключите HQ_BLE")

        nodes = validate_program(blocks)
        self.stop_flag.clear()
        self.emit("running", True)
        self.emit("log", "=== Запуск программы ===")
        try:
            await self._run_nodes(nodes)
        finally:
            try:
                if self.robot.connected:
                    await self.robot.write(CMD_STOP)
            except Exception:
                pass
            self.emit("clear_highlights", self.program_id)
            self.emit("running", False)
            self.emit("log", "=== Программа завершена ===")
