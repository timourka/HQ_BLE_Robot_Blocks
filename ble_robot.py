# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import secrets
import struct
import threading
from typing import Callable, Optional

from bleak import BleakClient, BleakScanner

from protocol import (
    DEVICE_NAME,
    COMMAND_UUID,
    NOTIFY_UUID,
    CMD_STOP,
    auth_response,
)


class AsyncLoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True, name="ble-asyncio")
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def shutdown(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


class RobotBLE:
    def __init__(self, emit: Callable[..., None]) -> None:
        self.emit = emit
        self.client: Optional[BleakClient] = None
        self.device = None
        self.auth_future: Optional[asyncio.Future] = None
        self.expected_auth: Optional[bytes] = None
        self.write_lock: Optional[asyncio.Lock] = None

    @property
    def connected(self) -> bool:
        return bool(self.client and self.client.is_connected)

    async def connect(self, timeout: float = 12.0, do_auth: bool = True) -> None:
        if self.connected:
            self.emit("log", "Уже подключено.")
            return

        self.emit("status", "Поиск HQ_BLE…", "searching")
        self.emit("log", f"Сканирование BLE: ищу {DEVICE_NAME}…")

        device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=timeout)
        if device is None:
            device = await BleakScanner.find_device_by_filter(
                lambda d, ad: (
                    getattr(d, "name", None) == DEVICE_NAME
                    or getattr(ad, "local_name", None) == DEVICE_NAME
                ),
                timeout=4.0,
            )
        if device is None:
            raise RuntimeError(
                "HQ_BLE не найден. Проверьте питание робота и закройте другие BLE-приложения."
            )

        self.device = device
        self.emit("log", f"Найден: {getattr(device, 'name', DEVICE_NAME)} / {device.address}")
        self.emit("status", "Подключение…", "searching")

        self.client = BleakClient(device, disconnected_callback=self._on_disconnect)
        await self.client.connect()
        self.write_lock = asyncio.Lock()

        command_char = self.client.services.get_characteristic(COMMAND_UUID)
        notify_char = self.client.services.get_characteristic(NOTIFY_UUID)
        if command_char is None or notify_char is None:
            await self.client.disconnect()
            raise RuntimeError("Подключились, но AE01/AE02 не найдены")

        await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        self.emit("log", "Notify на AE02 включён.")

        if do_auth:
            ok = await self.authenticate()
            if not ok:
                self.emit(
                    "log",
                    "ВНИМАНИЕ: STEM-auth не подтверждён. Соединение оставлено активным."
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
                self.write_lock = None
        self.emit("connected", False)
        self.emit("status", "Не подключено", "disconnected")
        self.emit("log", "Отключено.")

    def _on_disconnect(self, _client: BleakClient) -> None:
        self.emit("connected", False)
        self.emit("status", "Связь потеряна", "error")
        self.emit("log", "BLE-соединение разорвано.")

    def _on_notify(self, _sender, data: bytearray) -> None:
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

        number = 10_000_000 + secrets.randbelow(90_000_000)
        challenge = struct.pack("<I", number)
        expected = auth_response(number)

        self.expected_auth = expected
        self.auth_future = asyncio.get_running_loop().create_future()

        self.emit(
            "log",
            f"AUTH: {number} → {challenge.hex(' ').upper()}, "
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

        if self.write_lock is None:
            self.write_lock = asyncio.Lock()

        async with self.write_lock:
            await self.client.write_gatt_char(COMMAND_UUID, payload, response=False)
        self.emit("tx", payload.hex(" ").upper())
        self.emit("log", "TX → " + payload.hex(" ").upper())
