# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import struct
from typing import Any

from font5x5 import text_to_matrix_frames

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
    text = f"{number:08d}".encode("ascii")
    digest = hashlib.md5(b"\x01" + AUTH_SECRET + text).digest()
    return bytes((digest[1], digest[2], digest[6], digest[9]))


def matrix_packet(rows: list[int]) -> bytes:
    if len(rows) != 5:
        raise ValueError("Матрица должна содержать 5 строк")
    return bytes([0x03] + [int(x) & 0x1F for x in rows])


def matrix_value_to_rows(value: Any) -> list[int]:
    """
    Допустимые форматы:
      [10, 31, 31, 14, 4]
      [[0,1,0,1,0], ... 5 строк ...]
      ["01010", "11111", "11111", "01110", "00100"]
    """
    if isinstance(value, tuple):
        value = list(value)

    if not isinstance(value, list) or len(value) != 5:
        raise ValueError("Матрица должна быть массивом из 5 строк")

    if all(isinstance(row, (int, bool, float)) for row in value):
        return [int(row) & 0x1F for row in value]

    rows: list[int] = []
    for row in value:
        if isinstance(row, str):
            s = row.strip()
            if len(s) != 5 or any(ch not in "01" for ch in s):
                raise ValueError("Строка матрицы должна выглядеть как '01010'")
            rows.append(int(s, 2))
            continue

        if isinstance(row, tuple):
            row = list(row)
        if not isinstance(row, list) or len(row) != 5:
            raise ValueError("Каждая строка вложенной матрицы должна иметь 5 элементов")

        bits = 0
        for x, cell in enumerate(row):
            if bool(cell):
                bits |= 1 << (4 - x)
        rows.append(bits)

    return rows


def number_to_challenge(number: int) -> bytes:
    return struct.pack("<I", int(number))
