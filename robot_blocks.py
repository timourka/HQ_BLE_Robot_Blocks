#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HQ_BLE Robot Blocks
===================

Визуальный блочный редактор программ для BLE-робота HQ_BLE. Версия 1.1.

Протокол:
  Service: AE00
  AE01: Write Without Response — команды
  AE02: Notify — ответы/авторизация

Поддерживаемые команды:
  00                stop
  01 08             forward
  01 04             backward
  01 02             left
  01 01             right
  02 NN             melody NN (0..20)
  03 R1 R2 R3 R4 R5 matrix 5x5, 5 младших бит каждого байта
  scrolling text    software sequence of 03 frames

Зависимость: bleak
GUI: tkinter (входит в обычную поставку Python для Windows)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import secrets
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError
except Exception as exc:
    raise SystemExit(
        "Не найден пакет 'bleak'.\n"
        "Установите его командой:\n\n"
        "    python -m pip install bleak\n\n"
        f"Ошибка импорта: {exc}"
    )


# ---------------------------------------------------------------------------
# BLE protocol
# ---------------------------------------------------------------------------

DEVICE_NAME = "HQ_BLE"

SERVICE_UUID = "0000ae00-0000-1000-8000-00805f9b34fb"
COMMAND_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"

AUTH_SECRET = b"910726305003efb0f819537fa6e19269"

CMD_STOP = b"\x00"
CMD_FORWARD = b"\x01\x08"
CMD_BACKWARD = b"\x01\x04"
CMD_LEFT = b"\x01\x02"
CMD_RIGHT = b"\x01\x01"

MOVE_INTERVAL = 0.20


def auth_response(number: int) -> bytes:
    """Ответ, ожидаемый приложением STEM для 8-значного challenge."""
    text = f"{number:08d}".encode("ascii")
    digest = hashlib.md5(b"\x01" + AUTH_SECRET + text).digest()
    return bytes((digest[1], digest[2], digest[6], digest[9]))


def matrix_packet(rows: list[int]) -> bytes:
    if len(rows) != 5:
        raise ValueError("Матрица должна содержать 5 строк")
    return bytes([0x03] + [int(x) & 0x1F for x in rows])


# ---------------------------------------------------------------------------
# 5x5 font + software scrolling text
# ---------------------------------------------------------------------------

def _g(*rows: str) -> tuple[int, int, int, int, int]:
    """Создать 5x5 glyph из строк вида '01110'."""
    if len(rows) != 5:
        raise ValueError("Glyph must have 5 rows")
    return tuple(int(row, 2) & 0x1F for row in rows)


FONT_5X5: dict[str, tuple[int, int, int, int, int]] = {
    " ": _g("00000","00000","00000","00000","00000"),
    "?": _g("01110","10001","00010","00100","00100"),
    "!": _g("00100","00100","00100","00000","00100"),
    ".": _g("00000","00000","00000","00000","00100"),
    ",": _g("00000","00000","00000","00100","01000"),
    ":": _g("00000","00100","00000","00100","00000"),
    "-": _g("00000","00000","11111","00000","00000"),
    "+": _g("00000","00100","11111","00100","00000"),
    "/": _g("00001","00010","00100","01000","10000"),
    "_": _g("00000","00000","00000","00000","11111"),
    "0": _g("01110","10011","10101","11001","01110"),
    "1": _g("00100","01100","00100","00100","01110"),
    "2": _g("01110","10001","00010","00100","11111"),
    "3": _g("11110","00001","00110","00001","11110"),
    "4": _g("00010","00110","01010","11111","00010"),
    "5": _g("11111","10000","11110","00001","11110"),
    "6": _g("01110","10000","11110","10001","01110"),
    "7": _g("11111","00001","00010","00100","01000"),
    "8": _g("01110","10001","01110","10001","01110"),
    "9": _g("01110","10001","01111","00001","01110"),

    "A": _g("01110","10001","11111","10001","10001"),
    "B": _g("11110","10001","11110","10001","11110"),
    "C": _g("01111","10000","10000","10000","01111"),
    "D": _g("11110","10001","10001","10001","11110"),
    "E": _g("11111","10000","11110","10000","11111"),
    "F": _g("11111","10000","11110","10000","10000"),
    "G": _g("01111","10000","10111","10001","01110"),
    "H": _g("10001","10001","11111","10001","10001"),
    "I": _g("11111","00100","00100","00100","11111"),
    "J": _g("00111","00010","00010","10010","01100"),
    "K": _g("10001","10010","11100","10010","10001"),
    "L": _g("10000","10000","10000","10000","11111"),
    "M": _g("10001","11011","10101","10001","10001"),
    "N": _g("10001","11001","10101","10011","10001"),
    "O": _g("01110","10001","10001","10001","01110"),
    "P": _g("11110","10001","11110","10000","10000"),
    "Q": _g("01110","10001","10101","10010","01101"),
    "R": _g("11110","10001","11110","10010","10001"),
    "S": _g("01111","10000","01110","00001","11110"),
    "T": _g("11111","00100","00100","00100","00100"),
    "U": _g("10001","10001","10001","10001","01110"),
    "V": _g("10001","10001","10001","01010","00100"),
    "W": _g("10001","10001","10101","11011","10001"),
    "X": _g("10001","01010","00100","01010","10001"),
    "Y": _g("10001","01010","00100","00100","00100"),
    "Z": _g("11111","00010","00100","01000","11111"),

    # Кириллица. Похожие по начертанию символы ниже дополнительно
    # ссылаются на латинские глифы.
    "Б": _g("11111","10000","11110","10001","11110"),
    "Г": _g("11111","10000","10000","10000","10000"),
    "Д": _g("00110","01010","01010","11111","10001"),
    "Ё": _g("01010","11111","11110","10000","11111"),
    "Ж": _g("10101","10101","01110","10101","10101"),
    "З": _g("11110","00001","00110","00001","11110"),
    "И": _g("10001","10011","10101","11001","10001"),
    "Й": _g("01010","10001","10011","10101","11001"),
    "Л": _g("00111","01001","10001","10001","10001"),
    "П": _g("11111","10001","10001","10001","10001"),
    "У": _g("10001","01010","00100","01000","10000"),
    "Ф": _g("00100","11111","10101","11111","00100"),
    "Ц": _g("10001","10001","10001","11111","00001"),
    "Ч": _g("10001","10001","01111","00001","00001"),
    "Ш": _g("10101","10101","10101","10101","11111"),
    "Щ": _g("10101","10101","10101","11111","00001"),
    "Ъ": _g("11000","01000","01110","01001","01110"),
    "Ы": _g("10001","10001","11101","10101","11101"),
    "Ь": _g("10000","10000","11110","10001","11110"),
    "Э": _g("11110","00001","01111","00001","11110"),
    "Ю": _g("10111","10101","11101","10101","10111"),
    "Я": _g("01111","10001","01111","00101","01001"),
}

# Кириллические буквы, совпадающие/почти совпадающие по рисунку.
for _cyr, _latin in {
    "А":"A", "В":"B", "Е":"E", "К":"K", "М":"M",
    "Н":"H", "О":"O", "Р":"P", "С":"C", "Т":"T", "Х":"X"
}.items():
    FONT_5X5[_cyr] = FONT_5X5[_latin]


def text_to_matrix_frames(text: str) -> list[list[int]]:
    """
    Рендерит строку в последовательность кадров 5x5.
    Строка программно прокручивается справа налево.
    """
    text = (text or " ").upper()

    # Каждый элемент columns — вертикальная колонка из 5 бит:
    # bit4 = верхний пиксель, bit0 = нижний.
    columns: list[int] = [0] * 5

    for ch in text:
        glyph = FONT_5X5.get(ch, FONT_5X5["?"])
        for x in range(5):
            column = 0
            for y in range(5):
                if glyph[y] & (1 << (4 - x)):
                    column |= 1 << (4 - y)
            columns.append(column)
        columns.append(0)  # пробел между символами

    columns.extend([0] * 5)

    frames: list[list[int]] = []
    for offset in range(max(1, len(columns) - 4)):
        window = columns[offset:offset + 5]
        while len(window) < 5:
            window.append(0)

        rows = [0, 0, 0, 0, 0]
        for x, column in enumerate(window):
            for y in range(5):
                if column & (1 << (4 - y)):
                    rows[y] |= 1 << (4 - x)
        frames.append(rows)

    return frames


# ---------------------------------------------------------------------------
# Background asyncio loop
# ---------------------------------------------------------------------------

class AsyncLoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self._run, name="ble-asyncio", daemon=True
        )
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def shutdown(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


# ---------------------------------------------------------------------------
# BLE client
# ---------------------------------------------------------------------------

class RobotBLE:
    def __init__(self, emit: Callable[..., None]) -> None:
        self.emit = emit
        self.client: Optional[BleakClient] = None
        self.device = None
        self.auth_future: Optional[asyncio.Future] = None
        self.expected_auth: Optional[bytes] = None

    @property
    def connected(self) -> bool:
        return bool(self.client and self.client.is_connected)

    async def connect(self, timeout: float = 12.0, do_auth: bool = True) -> None:
        if self.connected:
            self.emit("log", "Уже подключено.")
            return

        self.emit("status", "Поиск HQ_BLE…", "searching")
        self.emit("log", f"Сканирование BLE: ищу {DEVICE_NAME}…")

        # find_device_by_name смотрит local name из advertising data.
        device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=timeout)
        if device is None:
            # Запасной вариант: некоторые адаптеры/рекламные пакеты отдают имя иначе.
            device = await BleakScanner.find_device_by_filter(
                lambda d, ad: (
                    (getattr(d, "name", None) == DEVICE_NAME)
                    or (getattr(ad, "local_name", None) == DEVICE_NAME)
                ),
                timeout=4.0,
            )

        if device is None:
            raise RuntimeError(
                "HQ_BLE не найден. Проверьте, что робот включён и не занят другим приложением."
            )

        self.device = device
        self.emit("log", f"Найден: {getattr(device, 'name', DEVICE_NAME)} / {device.address}")
        self.emit("status", "Подключение…", "searching")

        self.client = BleakClient(device, disconnected_callback=self._on_disconnect)
        await self.client.connect()

        # Проверяем наличие нужного сервиса/характеристик после подключения.
        services = self.client.services
        command_char = services.get_characteristic(COMMAND_UUID)
        notify_char = services.get_characteristic(NOTIFY_UUID)
        if command_char is None or notify_char is None:
            await self.client.disconnect()
            raise RuntimeError(
                "Подключились, но AE01/AE02 не найдены. Это не тот HQ_BLE или другой протокол."
            )

        await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        self.emit("log", "Notify на AE02 включён.")

        if do_auth:
            ok = await self.authenticate()
            if not ok:
                self.emit(
                    "log",
                    "ВНИМАНИЕ: ответ авторизации не получен. "
                    "Соединение оставлено активным — можно попробовать команды вручную."
                )

        self.emit("status", f"Подключено: {DEVICE_NAME}", "connected")
        self.emit("connected", True)
        self.emit("log", "BLE готов.")

    async def disconnect(self) -> None:
        if self.client:
            try:
                if self.client.is_connected:
                    try:
                        await self.write(CMD_STOP)
                    except Exception:
                        pass
                    await self.client.disconnect()
            finally:
                self.client = None
                self.device = None
        self.emit("connected", False)
        self.emit("status", "Не подключено", "disconnected")
        self.emit("log", "Отключено.")

    def _on_disconnect(self, client: BleakClient) -> None:
        self.emit("connected", False)
        self.emit("status", "Связь потеряна", "error")
        self.emit("log", "BLE-соединение разорвано.")

    def _on_notify(self, sender, data: bytearray) -> None:
        payload = bytes(data)
        self.emit("rx", payload.hex(" ").upper())
        self.emit("log", "RX ← " + payload.hex(" ").upper())

        if (
            self.auth_future is not None
            and not self.auth_future.done()
            and self.expected_auth
            and self.expected_auth in payload
        ):
            self.auth_future.set_result(True)

    async def authenticate(self) -> bool:
        if not self.connected:
            return False

        # Восемь десятичных цифр — так делает STEM.
        number = 10_000_000 + secrets.randbelow(90_000_000)
        challenge = struct.pack("<I", number)
        expected = auth_response(number)

        self.expected_auth = expected
        self.auth_future = asyncio.get_running_loop().create_future()

        self.emit(
            "log",
            f"AUTH: {number} → TX {challenge.hex(' ').upper()}, "
            f"жду {expected.hex(' ').upper()}"
        )
        await self.write(challenge)

        try:
            await asyncio.wait_for(self.auth_future, timeout=2.5)
            self.emit("log", "AUTH: OK.")
            return True
        except asyncio.TimeoutError:
            self.emit("log", "AUTH: таймаут.")
            return False
        finally:
            self.auth_future = None
            self.expected_auth = None

    async def write(self, payload: bytes) -> None:
        if not self.connected or self.client is None:
            raise RuntimeError("Робот не подключён")
        # AE01 имеет WriteWithoutResponse, поэтому response=False задаём явно.
        await self.client.write_gatt_char(COMMAND_UUID, payload, response=False)
        self.emit("tx", payload.hex(" ").upper())
        self.emit("log", "TX → " + payload.hex(" ").upper())


# ---------------------------------------------------------------------------
# Program blocks
# ---------------------------------------------------------------------------

BLOCK_META = {
    "forward":     ("ВПЕРЁД", "#4CAF50"),
    "backward":    ("НАЗАД", "#43A047"),
    "left":        ("ПОВОРОТ НАЛЕВО", "#2196F3"),
    "right":       ("ПОВОРОТ НАПРАВО", "#1E88E5"),
    "stop":        ("СТОП", "#E53935"),
    "wait":        ("ПАУЗА", "#FF9800"),
    "matrix":      ("МАТРИЦА 5×5", "#8E24AA"),
    "clear_matrix":("ОЧИСТИТЬ МАТРИЦУ", "#7B1FA2"),
    "marquee":     ("БЕГУЩАЯ СТРОКА", "#AB47BC"),
    "melody":      ("МЕЛОДИЯ", "#00ACC1"),
    "repeat_start":("ПОВТОРИТЬ", "#FFB300"),
    "repeat_end":  ("КОНЕЦ ПОВТОРА", "#F57C00"),
    "raw":         ("HEX-КОМАНДА", "#607D8B"),
}


def new_block(kind: str) -> dict[str, Any]:
    b: dict[str, Any] = {"id": str(uuid.uuid4()), "type": kind}
    if kind in ("forward", "backward", "left", "right"):
        b["duration"] = 1.0
    elif kind == "wait":
        b["duration"] = 1.0
    elif kind == "matrix":
        b["rows"] = [0, 0, 0, 0, 0]
    elif kind == "marquee":
        b["text"] = "ПРИВЕТ"
        b["frame_time"] = 0.15
        b["duration"] = 0.0
    elif kind == "melody":
        b["number"] = 1
    elif kind == "repeat_start":
        b["count"] = 2
    elif kind == "raw":
        b["hex"] = "00"
    return b


def block_title(block: dict[str, Any]) -> str:
    kind = block["type"]
    name = BLOCK_META[kind][0]
    if kind in ("forward", "backward", "left", "right", "wait"):
        return f"{name}   {float(block.get('duration', 1.0)):.2f} с"
    if kind == "marquee":
        value = str(block.get("text", ""))
        if len(value) > 18:
            value = value[:18] + "…"
        return f"{name}   «{value}»"
    if kind == "melody":
        return f"{name}   № {int(block.get('number', 1))}"
    if kind == "repeat_start":
        return f"{name}   {int(block.get('count', 2))} раз"
    if kind == "matrix":
        rows = block.get("rows", [0] * 5)
        bits = "  ".join(f"{int(r) & 31:05b}" for r in rows)
        return f"{name}   {bits}"
    if kind == "raw":
        return f"{name}   {block.get('hex', '')}"
    return name


def validate_and_nest(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Преобразует линейные ПОВТОРИТЬ/КОНЕЦ в дерево.
    Каждый узел repeat сохраняет start/end для подсветки.
    """
    root: list[dict[str, Any]] = []
    stack: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    current = root

    for block in blocks:
        kind = block["type"]
        if kind == "repeat_start":
            node = {
                "type": "__repeat__",
                "start": block,
                "end": None,
                "count": int(block.get("count", 2)),
                "children": [],
            }
            current.append(node)
            stack.append((node, current))
            current = node["children"]
        elif kind == "repeat_end":
            if not stack:
                raise ValueError("Есть «КОНЕЦ ПОВТОРА» без соответствующего «ПОВТОРИТЬ».")
            node, parent = stack.pop()
            node["end"] = block
            current = parent
        else:
            current.append(block)

    if stack:
        raise ValueError("Для блока «ПОВТОРИТЬ» не найден «КОНЕЦ ПОВТОРА».")

    return root


# ---------------------------------------------------------------------------
# Program runner
# ---------------------------------------------------------------------------

class ProgramRunner:
    def __init__(self, robot: RobotBLE, emit: Callable[..., None]) -> None:
        self.robot = robot
        self.emit = emit
        self.stop_flag = threading.Event()

    def request_stop(self) -> None:
        self.stop_flag.set()

    async def _sleep_interruptible(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, seconds)
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

    async def _marquee(
        self,
        text: str,
        frame_time: float,
        duration: float,
    ) -> None:
        """
        Бегущая строка реализована на ПК: последовательно отправляем
        обычные кадры команды 03 R1 R2 R3 R4 R5.
        duration <= 0 означает один полный проход.
        """
        frames = text_to_matrix_frames(text)
        frame_time = max(0.03, min(5.0, float(frame_time)))
        duration = max(0.0, float(duration))

        if not frames:
            await self.robot.write(matrix_packet([0, 0, 0, 0, 0]))
            return

        deadline = (time.monotonic() + duration) if duration > 0 else None

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

            # duration == 0: ровно один проход.
            if deadline is None:
                break

            if time.monotonic() >= deadline:
                break

        # Последний кадр — пустой экран, чтобы хвост строки исчез.
        if not self.stop_flag.is_set():
            await self.robot.write(matrix_packet([0, 0, 0, 0, 0]))

    async def _run_block(self, block: dict[str, Any]) -> None:
        kind = block["type"]
        self.emit("highlight", block["id"])
        self.emit("log", "▶ " + block_title(block))

        if kind == "forward":
            await self._move(CMD_FORWARD, block.get("duration", 1.0))
        elif kind == "backward":
            await self._move(CMD_BACKWARD, block.get("duration", 1.0))
        elif kind == "left":
            await self._move(CMD_LEFT, block.get("duration", 1.0))
        elif kind == "right":
            await self._move(CMD_RIGHT, block.get("duration", 1.0))
        elif kind == "stop":
            await self.robot.write(CMD_STOP)
        elif kind == "wait":
            await self._sleep_interruptible(float(block.get("duration", 1.0)))
        elif kind == "matrix":
            await self.robot.write(matrix_packet(block.get("rows", [0] * 5)))
        elif kind == "clear_matrix":
            await self.robot.write(matrix_packet([0, 0, 0, 0, 0]))
        elif kind == "marquee":
            await self._marquee(
                str(block.get("text", "")),
                float(block.get("frame_time", 0.15)),
                float(block.get("duration", 0.0)),
            )
        elif kind == "melody":
            # Agmt.playMusicHex(int) в STEM формирует "02" + numToHex8(n).
            # Поэтому 0 — валидное значение пакета 02 00.
            number = max(0, min(20, int(block.get("number", 1))))
            await self.robot.write(bytes([0x02, number]))
        elif kind == "raw":
            text = str(block.get("hex", "")).replace(" ", "").replace("-", "")
            if len(text) % 2:
                raise ValueError(f"HEX-команда должна иметь чётное число цифр: {text!r}")
            await self.robot.write(bytes.fromhex(text))

    async def _run_nodes(self, nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if self.stop_flag.is_set():
                return

            if node.get("type") == "__repeat__":
                start_block = node["start"]
                self.emit("highlight", start_block["id"])
                count = max(0, int(node.get("count", 0)))

                for i in range(count):
                    if self.stop_flag.is_set():
                        return
                    self.emit("log", f"↻ Повтор {i + 1}/{count}")
                    await self._run_nodes(node["children"])

                if node.get("end"):
                    self.emit("highlight", node["end"]["id"])
                    await asyncio.sleep(0.06)
            else:
                await self._run_block(node)

    async def run(self, blocks: list[dict[str, Any]]) -> None:
        if not self.robot.connected:
            raise RuntimeError("Сначала подключите HQ_BLE.")

        tree = validate_and_nest(blocks)
        self.stop_flag.clear()
        self.emit("running", True)
        self.emit("log", "=== Запуск программы ===")

        try:
            await self._run_nodes(tree)
        finally:
            try:
                if self.robot.connected:
                    await self.robot.write(CMD_STOP)
            except Exception:
                pass
            self.emit("highlight", None)
            self.emit("running", False)
            self.emit("log", "=== Программа остановлена ===")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ScrollFrame(tk.Frame):
    def __init__(self, master, bg="#F3F5F8", **kwargs):
        super().__init__(master, bg=bg, **kwargs)
        self.canvas = tk.Canvas(
            self, bg=bg, highlightthickness=0, borderwidth=0
        )
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview
        )
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.canvas.bind_all("<MouseWheel>", self._mousewheel)

    def _on_inner(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _mousewheel(self, event):
        # Скроллим только когда указатель над этим canvas.
        x, y = self.winfo_pointerxy()
        widget = self.winfo_containing(x, y)
        if widget is None:
            return
        w = widget
        while w is not None:
            if w == self:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return
            w = getattr(w, "master", None)


class RobotBlocksApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("HQ_BLE Robot Blocks")
        self.root.geometry("1380x850")
        self.root.minsize(1080, 700)

        self.bg = "#EEF1F5"
        self.panel = "#FFFFFF"
        self.text = "#17212B"
        self.muted = "#6B7785"
        self.border = "#D9DEE6"
        self.active_outline = "#111827"

        self.root.configure(bg=self.bg)

        self.ui_queue: queue.Queue = queue.Queue()
        self.async_thread = AsyncLoopThread()
        self.robot = RobotBLE(self.emit)
        self.runner = ProgramRunner(self.robot, self.emit)

        self.blocks: list[dict[str, Any]] = []
        self.selected_id: Optional[str] = None
        self.running = False
        self.connected = False
        self.drag_block_id: Optional[str] = None
        self.highlight_id: Optional[str] = None

        self._build_styles()
        self._build_ui()
        self._load_demo()

        self.root.after(40, self._poll_ui_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------ infrastructure ------------------------

    def emit(self, event: str, *args) -> None:
        self.ui_queue.put((event, args))

    def _poll_ui_events(self) -> None:
        try:
            while True:
                event, args = self.ui_queue.get_nowait()
                self._handle_ui_event(event, *args)
        except queue.Empty:
            pass
        self.root.after(40, self._poll_ui_events)

    def _handle_ui_event(self, event: str, *args) -> None:
        if event == "log":
            self._append_log(str(args[0]))
        elif event == "status":
            self._set_status(str(args[0]), str(args[1]))
        elif event == "connected":
            self.connected = bool(args[0])
            self._refresh_toolbar()
        elif event == "running":
            self.running = bool(args[0])
            self._refresh_toolbar()
        elif event == "highlight":
            self.highlight_id = args[0]
            self._render_program()
        elif event == "rx":
            self.rx_var.set(str(args[0]))
        elif event == "tx":
            self.tx_var.set(str(args[0]))

    def _future_done(self, future, action: str) -> None:
        try:
            future.result()
        except Exception as exc:
            self.emit("log", f"ОШИБКА ({action}): {exc}")
            self.emit("status", f"Ошибка: {exc}", "error")
            self.emit("running", False)

    def _build_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TButton", padding=(9, 6), font=("Segoe UI", 10))
        style.configure("Accent.TButton", padding=(12, 7), font=("Segoe UI Semibold", 10))
        style.configure("Danger.TButton", padding=(12, 7), font=("Segoe UI Semibold", 10))
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TEntry", padding=4)
        style.configure("TSpinbox", padding=4)

    # ------------------------ UI layout ------------------------

    def _build_ui(self) -> None:
        self._build_toolbar()

        body = tk.Frame(self.root, bg=self.bg)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.grid_columnconfigure(0, weight=0, minsize=235)
        body.grid_columnconfigure(1, weight=1)
        body.grid_columnconfigure(2, weight=0, minsize=325)
        body.grid_rowconfigure(0, weight=1)

        # Palette
        self.palette = tk.Frame(body, bg=self.panel, highlightbackground=self.border,
                                highlightthickness=1)
        self.palette.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._build_palette()

        # Program
        center = tk.Frame(body, bg=self.panel, highlightbackground=self.border,
                          highlightthickness=1)
        center.grid(row=0, column=1, sticky="nsew", padx=4)
        tk.Label(
            center, text="ПРОГРАММА", bg=self.panel, fg=self.text,
            font=("Segoe UI Semibold", 12)
        ).pack(anchor="w", padx=14, pady=(12, 8))

        hint = tk.Label(
            center,
            text="Нажмите блок слева, затем перетаскивайте блоки здесь за верхнюю часть. "
                 "Блоки «ПОВТОРИТЬ / КОНЕЦ» можно вкладывать.",
            bg=self.panel, fg=self.muted, justify="left", wraplength=700,
            font=("Segoe UI", 9)
        )
        hint.pack(anchor="w", padx=14, pady=(0, 8))

        self.program_scroll = ScrollFrame(center, bg="#F5F7FA")
        self.program_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Inspector + log
        right = tk.Frame(body, bg=self.panel, highlightbackground=self.border,
                         highlightthickness=1)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        right.grid_rowconfigure(1, weight=1)
        right.grid_rowconfigure(3, weight=1)
        right.grid_columnconfigure(0, weight=1)

        tk.Label(
            right, text="ПАРАМЕТРЫ БЛОКА", bg=self.panel, fg=self.text,
            font=("Segoe UI Semibold", 12)
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 8))

        self.inspector = tk.Frame(right, bg=self.panel)
        self.inspector.grid(row=1, column=0, sticky="nsew", padx=12)

        sep = ttk.Separator(right, orient="horizontal")
        sep.grid(row=2, column=0, sticky="ew", padx=10, pady=8)

        log_box = tk.Frame(right, bg=self.panel)
        log_box.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        log_box.grid_rowconfigure(2, weight=1)
        log_box.grid_columnconfigure(0, weight=1)

        tk.Label(
            log_box, text="BLE / ЖУРНАЛ", bg=self.panel, fg=self.text,
            font=("Segoe UI Semibold", 11)
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        io = tk.Frame(log_box, bg=self.panel)
        io.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        io.grid_columnconfigure(1, weight=1)

        self.tx_var = tk.StringVar(value="—")
        self.rx_var = tk.StringVar(value="—")
        tk.Label(io, text="TX", bg=self.panel, fg=self.muted,
                 font=("Consolas", 9, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(io, textvariable=self.tx_var, bg=self.panel, fg=self.text,
                 font=("Consolas", 9), anchor="w").grid(row=0, column=1, sticky="ew", padx=6)
        tk.Label(io, text="RX", bg=self.panel, fg=self.muted,
                 font=("Consolas", 9, "bold")).grid(row=1, column=0, sticky="w")
        tk.Label(io, textvariable=self.rx_var, bg=self.panel, fg=self.text,
                 font=("Consolas", 9), anchor="w").grid(row=1, column=1, sticky="ew", padx=6)

        self.log_text = tk.Text(
            log_box, height=12, wrap="word", state="disabled",
            bg="#0F172A", fg="#D1E7DD", insertbackground="white",
            font=("Consolas", 9), relief="flat", padx=8, pady=8
        )
        self.log_text.grid(row=2, column=0, sticky="nsew")

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self.root, bg=self.panel, height=62,
                       highlightbackground=self.border, highlightthickness=1)
        bar.pack(fill="x", padx=12, pady=12)
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=self.panel)
        left.pack(side="left", fill="y", padx=10)

        tk.Label(
            left, text="HQ_BLE", bg=self.panel, fg=self.text,
            font=("Segoe UI Semibold", 15)
        ).pack(side="left", padx=(0, 14))

        self.connect_btn = ttk.Button(left, text="Подключить", command=self._connect)
        self.connect_btn.pack(side="left", padx=3)

        self.disconnect_btn = ttk.Button(left, text="Отключить", command=self._disconnect)
        self.disconnect_btn.pack(side="left", padx=3)

        self.run_btn = ttk.Button(left, text="▶ Запустить", command=self._run_program)
        self.run_btn.pack(side="left", padx=(14, 3))

        self.stop_btn = ttk.Button(left, text="■ СТОП", command=self._emergency_stop)
        self.stop_btn.pack(side="left", padx=3)

        right = tk.Frame(bar, bg=self.panel)
        right.pack(side="right", fill="y", padx=10)

        for text, cmd in (
            ("Новая", self._new_program),
            ("Открыть", self._load_program),
            ("Сохранить", self._save_program),
        ):
            ttk.Button(right, text=text, command=cmd).pack(side="left", padx=3)

        status_frame = tk.Frame(bar, bg=self.panel)
        status_frame.pack(side="right", fill="y", padx=12)

        self.status_dot = tk.Label(
            status_frame, text="●", bg=self.panel, fg="#9CA3AF",
            font=("Segoe UI", 13)
        )
        self.status_dot.pack(side="left", pady=18)

        self.status_var = tk.StringVar(value="Не подключено")
        tk.Label(
            status_frame, textvariable=self.status_var,
            bg=self.panel, fg=self.text, font=("Segoe UI", 10)
        ).pack(side="left", padx=(5, 0), pady=18)

        self._refresh_toolbar()

    def _build_palette(self) -> None:
        tk.Label(
            self.palette, text="БЛОКИ", bg=self.panel, fg=self.text,
            font=("Segoe UI Semibold", 12)
        ).pack(anchor="w", padx=14, pady=(12, 10))

        groups = [
            ("Движение", [
                ("forward", "Вперёд"),
                ("backward", "Назад"),
                ("left", "Налево"),
                ("right", "Направо"),
                ("stop", "Стоп"),
            ]),
            ("Управление", [
                ("wait", "Пауза"),
                ("repeat_start", "Повторить"),
                ("repeat_end", "Конец повтора"),
            ]),
            ("Экран и звук", [
                ("matrix", "Матрица 5×5"),
                ("marquee", "Бегущая строка"),
                ("clear_matrix", "Очистить матрицу"),
                ("melody", "Мелодия"),
            ]),
            ("Дополнительно", [
                ("raw", "HEX-команда"),
            ]),
        ]

        for group_name, items in groups:
            tk.Label(
                self.palette, text=group_name.upper(), bg=self.panel,
                fg=self.muted, font=("Segoe UI Semibold", 8)
            ).pack(anchor="w", padx=14, pady=(8, 4))

            for kind, label in items:
                color = BLOCK_META[kind][1]
                btn = tk.Button(
                    self.palette,
                    text="+  " + label,
                    command=lambda k=kind: self._add_block(k),
                    bg=color, fg="white", activebackground=color,
                    activeforeground="white", relief="flat", bd=0,
                    font=("Segoe UI Semibold", 10),
                    padx=10, pady=7, anchor="w", cursor="hand2"
                )
                btn.pack(fill="x", padx=12, pady=3)

    # ------------------------ status/log ------------------------

    def _set_status(self, text: str, kind: str) -> None:
        colors = {
            "connected": "#22C55E",
            "searching": "#F59E0B",
            "error": "#EF4444",
            "disconnected": "#9CA3AF",
        }
        self.status_var.set(text)
        self.status_dot.configure(fg=colors.get(kind, "#9CA3AF"))

    def _append_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _refresh_toolbar(self) -> None:
        self.connect_btn.configure(state="disabled" if self.connected else "normal")
        self.disconnect_btn.configure(state="normal" if self.connected else "disabled")
        self.run_btn.configure(
            state="normal" if (self.connected and not self.running and self.blocks) else "disabled"
        )
        self.stop_btn.configure(state="normal" if self.connected else "disabled")

    # ------------------------ BLE actions ------------------------

    def _connect(self) -> None:
        self._set_status("Поиск HQ_BLE…", "searching")
        fut = self.async_thread.submit(self.robot.connect())
        fut.add_done_callback(lambda f: self._future_done(f, "подключение"))

    def _disconnect(self) -> None:
        self.runner.request_stop()
        fut = self.async_thread.submit(self.robot.disconnect())
        fut.add_done_callback(lambda f: self._future_done(f, "отключение"))

    def _run_program(self) -> None:
        if self.running:
            return
        snapshot = json.loads(json.dumps(self.blocks))
        try:
            validate_and_nest(snapshot)
        except ValueError as exc:
            messagebox.showerror("Ошибка программы", str(exc))
            return

        fut = self.async_thread.submit(self.runner.run(snapshot))
        fut.add_done_callback(lambda f: self._future_done(f, "выполнение"))

    def _emergency_stop(self) -> None:
        self.runner.request_stop()
        self.emit("log", "!!! АВАРИЙНЫЙ СТОП !!!")
        if self.robot.connected:
            fut = self.async_thread.submit(self.robot.write(CMD_STOP))
            fut.add_done_callback(lambda f: self._future_done(f, "стоп"))

    # ------------------------ program editing ------------------------

    def _add_block(self, kind: str) -> None:
        block = new_block(kind)
        if self.selected_id:
            idx = next(
                (i for i, b in enumerate(self.blocks) if b["id"] == self.selected_id),
                len(self.blocks) - 1,
            )
            self.blocks.insert(idx + 1, block)
        else:
            self.blocks.append(block)

        self.selected_id = block["id"]
        self._render_program()
        self._render_inspector()
        self._refresh_toolbar()

    def _delete_selected(self) -> None:
        if not self.selected_id:
            return
        self.blocks = [b for b in self.blocks if b["id"] != self.selected_id]
        self.selected_id = None
        self._render_program()
        self._render_inspector()
        self._refresh_toolbar()

    def _duplicate_selected(self) -> None:
        block = self._selected_block()
        if not block:
            return
        clone = json.loads(json.dumps(block))
        clone["id"] = str(uuid.uuid4())
        idx = self.blocks.index(block)
        self.blocks.insert(idx + 1, clone)
        self.selected_id = clone["id"]
        self._render_program()
        self._render_inspector()

    def _selected_block(self) -> Optional[dict[str, Any]]:
        if not self.selected_id:
            return None
        return next((b for b in self.blocks if b["id"] == self.selected_id), None)

    def _select_block(self, block_id: str) -> None:
        self.selected_id = block_id
        self._render_program()
        self._render_inspector()

    def _block_depths(self) -> dict[str, int]:
        depths: dict[str, int] = {}
        depth = 0
        for block in self.blocks:
            if block["type"] == "repeat_end":
                depth = max(0, depth - 1)
            depths[block["id"]] = depth
            if block["type"] == "repeat_start":
                depth += 1
        return depths

    def _render_program(self) -> None:
        inner = self.program_scroll.inner
        for child in inner.winfo_children():
            child.destroy()

        if not self.blocks:
            tk.Label(
                inner,
                text="Программа пустая\n\nДобавьте блоки из палитры слева.",
                bg="#F5F7FA", fg=self.muted,
                font=("Segoe UI", 11), justify="center"
            ).pack(fill="both", expand=True, pady=90)
            return

        depths = self._block_depths()

        for index, block in enumerate(self.blocks):
            depth = depths.get(block["id"], 0)
            color = BLOCK_META[block["type"]][1]
            selected = block["id"] == self.selected_id
            highlighted = block["id"] == self.highlight_id

            outer = tk.Frame(inner, bg="#F5F7FA")
            outer.pack(fill="x", padx=(10 + depth * 26, 10), pady=4)

            card = tk.Frame(
                outer,
                bg=color,
                highlightbackground=(
                    "#111827" if selected else "#FFD54F" if highlighted else color
                ),
                highlightthickness=3 if (selected or highlighted) else 1,
                cursor="hand2",
            )
            card.pack(fill="x")

            drag = tk.Label(
                card, text="⋮⋮", bg=color, fg="white",
                font=("Segoe UI", 14), cursor="fleur", padx=8
            )
            drag.pack(side="left", fill="y")

            title = tk.Label(
                card, text=block_title(block),
                bg=color, fg="white",
                font=("Segoe UI Semibold", 10),
                anchor="w", padx=5, pady=9
            )
            title.pack(side="left", fill="x", expand=True)

            number = tk.Label(
                card, text=str(index + 1), bg=color, fg="#EAF2F8",
                font=("Segoe UI", 8), padx=7
            )
            number.pack(side="right")

            for w in (card, drag, title, number):
                w.bind("<Button-1>", lambda e, bid=block["id"]: self._drag_start(bid))
                w.bind("<ButtonRelease-1>", lambda e, bid=block["id"]: self._drag_end(e, bid))
                w.bind("<Double-Button-1>", lambda e, bid=block["id"]: self._select_block(bid))

    def _drag_start(self, block_id: str) -> None:
        self.drag_block_id = block_id
        self._select_block(block_id)

    def _drag_end(self, event, block_id: str) -> None:
        if not self.drag_block_id:
            return
        source_id = self.drag_block_id
        self.drag_block_id = None

        # Определяем позицию относительно середины карточек по экранной Y.
        y_root = event.y_root
        target_index = len(self.blocks) - 1

        children = self.program_scroll.inner.winfo_children()
        card_rows = [c for c in children if c.winfo_height() > 1]
        for i, row in enumerate(card_rows):
            mid = row.winfo_rooty() + row.winfo_height() / 2
            if y_root < mid:
                target_index = i
                break

        source_index = next(
            (i for i, b in enumerate(self.blocks) if b["id"] == source_id), None
        )
        if source_index is None:
            return

        block = self.blocks.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        target_index = max(0, min(target_index, len(self.blocks)))
        self.blocks.insert(target_index, block)
        self._render_program()

    # ------------------------ inspector ------------------------

    def _clear_inspector(self) -> None:
        for w in self.inspector.winfo_children():
            w.destroy()

    def _render_inspector(self) -> None:
        self._clear_inspector()
        block = self._selected_block()

        if not block:
            tk.Label(
                self.inspector,
                text="Выберите блок в программе.",
                bg=self.panel, fg=self.muted, justify="left"
            ).pack(anchor="w", pady=8)
            return

        kind = block["type"]
        color = BLOCK_META[kind][1]

        header = tk.Label(
            self.inspector, text=BLOCK_META[kind][0],
            bg=color, fg="white", font=("Segoe UI Semibold", 11),
            padx=10, pady=7
        )
        header.pack(fill="x", pady=(0, 12))

        if kind in ("forward", "backward", "left", "right", "wait"):
            tk.Label(
                self.inspector, text="Продолжительность, секунд",
                bg=self.panel, fg=self.text
            ).pack(anchor="w")

            var = tk.DoubleVar(value=float(block.get("duration", 1.0)))
            spin = ttk.Spinbox(
                self.inspector, from_=0.0, to=3600.0, increment=0.1,
                textvariable=var, width=12
            )
            spin.pack(anchor="w", pady=(4, 8))

            def apply_duration():
                try:
                    block["duration"] = max(0.0, float(var.get()))
                    self._render_program()
                except Exception:
                    messagebox.showerror("Ошибка", "Введите число.")

            ttk.Button(
                self.inspector, text="Применить",
                command=apply_duration
            ).pack(anchor="w")

            if kind in ("forward", "backward", "left", "right"):
                tk.Label(
                    self.inspector,
                    text="Во время движения команда повторяется каждые 200 мс, "
                         "после времени автоматически отправляется STOP.",
                    bg=self.panel, fg=self.muted, wraplength=290, justify="left",
                    font=("Segoe UI", 9)
                ).pack(anchor="w", pady=(10, 0))

        elif kind == "marquee":
            tk.Label(
                self.inspector, text="Текст",
                bg=self.panel, fg=self.text
            ).pack(anchor="w")

            text_var = tk.StringVar(value=str(block.get("text", "ПРИВЕТ")))
            entry = ttk.Entry(self.inspector, textvariable=text_var)
            entry.pack(fill="x", pady=(4, 10))

            tk.Label(
                self.inspector, text="Интервал между кадрами, секунд",
                bg=self.panel, fg=self.text
            ).pack(anchor="w")

            frame_var = tk.DoubleVar(value=float(block.get("frame_time", 0.15)))
            ttk.Spinbox(
                self.inspector, from_=0.03, to=5.0, increment=0.01,
                textvariable=frame_var, width=12
            ).pack(anchor="w", pady=(4, 10))

            tk.Label(
                self.inspector,
                text="Общая длительность, секунд (0 = один проход)",
                bg=self.panel, fg=self.text
            ).pack(anchor="w")

            duration_var = tk.DoubleVar(value=float(block.get("duration", 0.0)))
            ttk.Spinbox(
                self.inspector, from_=0.0, to=3600.0, increment=0.5,
                textvariable=duration_var, width=12
            ).pack(anchor="w", pady=(4, 10))

            preview_var = tk.StringVar(value="")

            def update_marquee_preview():
                value = text_var.get()
                frames = text_to_matrix_frames(value)
                if frames:
                    first_nonempty = next(
                        (rows for rows in frames if any(rows)),
                        frames[0]
                    )
                    preview_var.set(
                        matrix_packet(first_nonempty).hex(" ").upper()
                        + f"   ({len(frames)} кадров/проход)"
                    )
                else:
                    preview_var.set("—")

            def apply_marquee():
                try:
                    block["text"] = text_var.get()
                    block["frame_time"] = max(0.03, min(5.0, float(frame_var.get())))
                    block["duration"] = max(0.0, float(duration_var.get()))
                except Exception:
                    messagebox.showerror("Ошибка", "Проверьте числовые параметры.")
                    return
                update_marquee_preview()
                self._render_program()

            ttk.Button(
                self.inspector, text="Применить",
                command=apply_marquee
            ).pack(anchor="w")

            tk.Label(
                self.inspector,
                text="Реализовано программно: приложение отправляет последовательность "
                     "обычных кадров матрицы 03 xx xx xx xx xx. "
                     "Поддерживаются русские/латинские буквы, цифры и базовая пунктуация.",
                bg=self.panel, fg=self.muted, wraplength=290, justify="left",
                font=("Segoe UI", 9)
            ).pack(anchor="w", pady=(10, 4))

            tk.Label(
                self.inspector, textvariable=preview_var,
                bg=self.panel, fg=self.muted, font=("Consolas", 8),
                wraplength=290, justify="left"
            ).pack(anchor="w")
            update_marquee_preview()

        elif kind == "melody":
            tk.Label(
                self.inspector, text="Номер мелодии (0…20)",
                bg=self.panel, fg=self.text
            ).pack(anchor="w")

            var = tk.IntVar(value=int(block.get("number", 1)))
            spin = ttk.Spinbox(
                self.inspector, from_=0, to=20, increment=1,
                textvariable=var, width=10
            )
            spin.pack(anchor="w", pady=(4, 8))

            def apply_melody():
                block["number"] = max(0, min(20, int(var.get())))
                self._render_program()

            ttk.Button(
                self.inspector, text="Применить",
                command=apply_melody
            ).pack(anchor="w")

            tk.Label(
                self.inspector,
                text="0 отправляет пакет 02 00. В декомпилированном STEM "
                     "playMusicHex(n) формирует «02» + однобайтовый n.",
                bg=self.panel, fg=self.muted, wraplength=290, justify="left",
                font=("Segoe UI", 9)
            ).pack(anchor="w", pady=(10, 0))

        elif kind == "repeat_start":
            tk.Label(
                self.inspector, text="Количество повторов",
                bg=self.panel, fg=self.text
            ).pack(anchor="w")

            var = tk.IntVar(value=int(block.get("count", 2)))
            spin = ttk.Spinbox(
                self.inspector, from_=1, to=999, increment=1,
                textvariable=var, width=10
            )
            spin.pack(anchor="w", pady=(4, 8))

            def apply_repeat():
                block["count"] = max(1, int(var.get()))
                self._render_program()

            ttk.Button(
                self.inspector, text="Применить",
                command=apply_repeat
            ).pack(anchor="w")

            tk.Label(
                self.inspector,
                text="Все блоки до соответствующего «КОНЕЦ ПОВТОРА» "
                     "будут выполнены указанное число раз. Вложенные циклы поддерживаются.",
                bg=self.panel, fg=self.muted, wraplength=290, justify="left",
                font=("Segoe UI", 9)
            ).pack(anchor="w", pady=(10, 0))

        elif kind == "repeat_end":
            tk.Label(
                self.inspector,
                text="Закрывает ближайший незакрытый блок «ПОВТОРИТЬ».",
                bg=self.panel, fg=self.muted, wraplength=290, justify="left"
            ).pack(anchor="w")

        elif kind == "matrix":
            self._matrix_editor(block)

        elif kind == "clear_matrix":
            tk.Label(
                self.inspector,
                text="Отправляет: 03 00 00 00 00 00",
                bg=self.panel, fg=self.muted, font=("Consolas", 9)
            ).pack(anchor="w")

        elif kind == "raw":
            tk.Label(
                self.inspector, text="HEX-байты",
                bg=self.panel, fg=self.text
            ).pack(anchor="w")

            var = tk.StringVar(value=str(block.get("hex", "00")))
            entry = ttk.Entry(self.inspector, textvariable=var)
            entry.pack(fill="x", pady=(4, 8))

            def apply_raw():
                text = var.get().strip().upper()
                try:
                    bytes.fromhex(text)
                except ValueError:
                    messagebox.showerror(
                        "Ошибка HEX",
                        "Введите байты, например: 01 08"
                    )
                    return
                block["hex"] = text
                self._render_program()

            ttk.Button(
                self.inspector, text="Применить",
                command=apply_raw
            ).pack(anchor="w")

        # Generic actions
        spacer = tk.Frame(self.inspector, bg=self.panel, height=18)
        spacer.pack()

        actions = tk.Frame(self.inspector, bg=self.panel)
        actions.pack(fill="x")
        ttk.Button(
            actions, text="Дублировать",
            command=self._duplicate_selected
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="Удалить",
            command=self._delete_selected
        ).pack(side="left")

    def _matrix_editor(self, block: dict[str, Any]) -> None:
        rows = [int(x) & 0x1F for x in block.get("rows", [0] * 5)]
        block["rows"] = rows

        tk.Label(
            self.inspector,
            text="Нажимайте клетки. Красная = 1.",
            bg=self.panel, fg=self.muted
        ).pack(anchor="w", pady=(0, 8))

        grid = tk.Frame(self.inspector, bg=self.panel)
        grid.pack(anchor="w")

        buttons: list[list[tk.Button]] = []

        def refresh_matrix():
            for y in range(5):
                for x in range(5):
                    on = bool(rows[y] & (1 << (4 - x)))
                    buttons[y][x].configure(
                        bg="#E53935" if on else "#E5E7EB",
                        activebackground="#EF5350" if on else "#D1D5DB"
                    )
            hexvar.set(matrix_packet(rows).hex(" ").upper())
            self._render_program()

        def toggle(y: int, x: int):
            rows[y] ^= 1 << (4 - x)
            block["rows"] = rows
            refresh_matrix()

        for y in range(5):
            line = []
            for x in range(5):
                btn = tk.Button(
                    grid, width=2, height=1, relief="flat", bd=0,
                    command=lambda yy=y, xx=x: toggle(yy, xx),
                    cursor="hand2"
                )
                btn.grid(row=y, column=x, padx=2, pady=2, ipadx=5, ipady=5)
                line.append(btn)
            buttons.append(line)

        hexvar = tk.StringVar()
        tk.Label(
            self.inspector, text="BLE-пакет:",
            bg=self.panel, fg=self.text
        ).pack(anchor="w", pady=(10, 2))
        tk.Entry(
            self.inspector, textvariable=hexvar, state="readonly",
            relief="flat", bg="#F3F4F6", fg=self.text,
            readonlybackground="#F3F4F6", font=("Consolas", 9)
        ).pack(fill="x")

        presets = tk.Frame(self.inspector, bg=self.panel)
        presets.pack(fill="x", pady=(8, 0))

        def set_rows(new_rows):
            rows[:] = list(new_rows)
            block["rows"] = rows
            refresh_matrix()

        ttk.Button(
            presets, text="Очистить",
            command=lambda: set_rows([0, 0, 0, 0, 0])
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            presets, text="Все",
            command=lambda: set_rows([31, 31, 31, 31, 31])
        ).pack(side="left", padx=5)

        # Пример рисунка из диалога пользователя
        ttk.Button(
            presets, text="Сердце",
            command=lambda: set_rows([0b01010, 0b11111, 0b11111, 0b01110, 0b00100])
        ).pack(side="left", padx=5)

        refresh_matrix()

    # ------------------------ files ------------------------

    def _new_program(self) -> None:
        if self.blocks and not messagebox.askyesno(
            "Новая программа", "Очистить текущую программу?"
        ):
            return
        self.blocks = []
        self.selected_id = None
        self.highlight_id = None
        self._render_program()
        self._render_inspector()
        self._refresh_toolbar()

    def _save_program(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Сохранить программу",
            defaultextension=".hqrobot.json",
            filetypes=[
                ("HQ Robot program", "*.hqrobot.json"),
                ("JSON", "*.json"),
                ("Все файлы", "*.*"),
            ],
        )
        if not path:
            return

        payload = {
            "format": "hq_ble_robot_blocks",
            "version": 1,
            "device": DEVICE_NAME,
            "blocks": self.blocks,
        }
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._append_log(f"Сохранено: {path}")

    def _load_program(self) -> None:
        path = filedialog.askopenfilename(
            title="Открыть программу",
            filetypes=[
                ("HQ Robot program", "*.hqrobot.json"),
                ("JSON", "*.json"),
                ("Все файлы", "*.*"),
            ],
        )
        if not path:
            return

        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            blocks = payload["blocks"] if isinstance(payload, dict) else payload
            if not isinstance(blocks, list):
                raise ValueError("Нет массива blocks")
            for block in blocks:
                if "type" not in block or block["type"] not in BLOCK_META:
                    raise ValueError(f"Неизвестный блок: {block}")
                block.setdefault("id", str(uuid.uuid4()))
            self.blocks = blocks
            self.selected_id = blocks[0]["id"] if blocks else None
            self._render_program()
            self._render_inspector()
            self._refresh_toolbar()
            self._append_log(f"Открыто: {path}")
        except Exception as exc:
            messagebox.showerror("Ошибка открытия", str(exc))

    def _load_demo(self) -> None:
        # Небольшая стартовая программа — можно сразу удалить/изменить.
        self.blocks = [
            {**new_block("matrix"), "rows": [0b01010, 0b11111, 0b11111, 0b01110, 0b00100]},
            {**new_block("wait"), "duration": 1.0},
            {**new_block("repeat_start"), "count": 2},
            {**new_block("forward"), "duration": 0.7},
            {**new_block("right"), "duration": 0.45},
            new_block("repeat_end"),
            new_block("stop"),
        ]
        self.selected_id = self.blocks[0]["id"]
        self._render_program()
        self._render_inspector()
        self._refresh_toolbar()
        self._append_log("Готово. Включите робота и нажмите «Подключить».")

    # ------------------------ close ------------------------

    def _on_close(self) -> None:
        self.runner.request_stop()
        if self.robot.connected:
            fut = self.async_thread.submit(self.robot.disconnect())
            try:
                fut.result(timeout=2.0)
            except Exception:
                pass
        self.async_thread.shutdown()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = RobotBlocksApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
