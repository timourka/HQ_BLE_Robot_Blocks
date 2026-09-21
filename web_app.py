# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import queue
import threading
from pathlib import Path
from typing import Any, Optional

import webview

from ble_robot import AsyncLoopThread, RobotBLE
from protocol import CMD_STOP, DEVICE_NAME
from runner import ProgramRunner
from runtime import SafeEvaluator, SharedState, json_safe, validate_program


class BlocklyApi:
    """Python backend exposed to the Blockly web interface by pywebview."""

    def __init__(self) -> None:
        self.window: Optional[webview.Window] = None
        self.events: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue()
        self.async_thread = AsyncLoopThread()
        self.state = SharedState(self._state_changed)
        self.evaluator = SafeEvaluator(self.state)
        self.robot = RobotBLE(self.emit)
        self.runner: Optional[ProgramRunner] = None
        self.project_path: Optional[Path] = None
        self.running = False
        self.connected = False
        self.closed = False
        threading.Thread(target=self._dispatch_events, daemon=True).start()

    def attach(self, window: webview.Window) -> None:
        self.window = window

    def emit(self, event: str, *args: Any) -> None:
        if event == "connected":
            self.connected = bool(args[0])
        elif event == "running":
            self.running = bool(args[0])
        self.events.put((event, args))

    def _state_changed(self, what: str) -> None:
        self.emit("state_changed", what)

    def _dispatch_events(self) -> None:
        while not self.closed:
            try:
                event, args = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            window = self.window
            if window is None:
                continue
            payload = json.dumps(
                {"event": event, "args": json_safe(list(args))},
                ensure_ascii=False,
            )
            try:
                window.evaluate_js(f"window.handleBackendEvent({payload})")
            except Exception:
                # The page may still be loading or already closing.
                pass

    def _future_done(self, future, action: str) -> None:
        try:
            future.result()
        except Exception as exc:
            self.emit("log", f"ОШИБКА ({action}): {exc}")
            self.emit("status", f"Ошибка: {exc}", "error")
            if action == "выполнение":
                self.emit("running", False)

    def initial_state(self) -> dict[str, Any]:
        return {
            "device": DEVICE_NAME,
            "connected": self.connected,
            "running": self.running,
            "variables": self.state.variable_snapshot(),
            "controls": self.state.controls_snapshot(),
        }

    def connect(self) -> dict[str, bool]:
        future = self.async_thread.submit(self.robot.connect())
        future.add_done_callback(lambda f: self._future_done(f, "подключение"))
        return {"ok": True}

    def disconnect(self) -> dict[str, bool]:
        if self.runner:
            self.runner.request_stop()
        future = self.async_thread.submit(self.robot.disconnect())
        future.add_done_callback(lambda f: self._future_done(f, "отключение"))
        return {"ok": True}

    def run_program(
        self, blocks: list[dict[str, Any]], program_id: str
    ) -> dict[str, Any]:
        if self.running:
            return {"ok": False, "error": "Программа уже выполняется"}
        try:
            validate_program(copy.deepcopy(blocks))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not self.robot.connected:
            return {"ok": False, "error": "Сначала подключите HQ_BLE"}

        self.runner = ProgramRunner(self.robot, self.state, self.emit, program_id)
        future = self.async_thread.submit(
            self.runner.run(copy.deepcopy(blocks))
        )
        future.add_done_callback(lambda f: self._future_done(f, "выполнение"))
        return {"ok": True}

    def stop(self) -> dict[str, bool]:
        if self.runner:
            self.runner.request_stop()
        self.emit("log", "!!! АВАРИЙНЫЙ СТОП !!!")
        if self.robot.connected:
            future = self.async_thread.submit(self.robot.write(CMD_STOP))
            future.add_done_callback(lambda f: self._future_done(f, "стоп"))
        return {"ok": True}

    def get_runtime_state(self) -> dict[str, Any]:
        return self.state.snapshot_for_project()

    def set_variable(self, name: str, expression: str) -> dict[str, Any]:
        try:
            self.state.set_var(name, self.evaluator.eval(expression))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def delete_variable(self, name: str) -> dict[str, bool]:
        self.state.del_var(name)
        return {"ok": True}

    def set_control(self, name: str, value: Any) -> dict[str, Any]:
        try:
            self.state.set_control(name, value)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def define_control(self, spec: dict[str, Any]) -> dict[str, Any]:
        try:
            self.state.ensure_control(
                str(spec.get("name", "")),
                label=str(spec.get("label", "") or spec.get("name", "")),
                kind=str(spec.get("kind", "slider")),
                minimum=float(spec.get("min", 0)),
                maximum=float(spec.get("max", 100)),
                step=float(spec.get("step", 1)),
                default=spec.get("value", 0),
                bind_var=str(spec.get("bind_var", "")),
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def delete_control(self, name: str) -> dict[str, bool]:
        self.state.del_control(name)
        return {"ok": True}

    def save_project(
        self, payload: dict[str, Any], save_as: bool = False
    ) -> dict[str, Any]:
        if save_as or self.project_path is None:
            result = self.window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename="project.hqstudio.json",
                file_types=(
                    "HQ BLE Studio (*.hqstudio.json)",
                    "JSON (*.json)",
                ),
            )
            if not result:
                return {"ok": False, "cancelled": True}
            selected = result[0] if isinstance(result, tuple) else result
            self.project_path = Path(selected)

        runtime = self.state.snapshot_for_project()
        document = {
            "format": "hq_ble_robot_blockly",
            "version": 3,
            "device": DEVICE_NAME,
            "programs": payload.get("programs", []),
            "variables": runtime["variables"],
            "controls": runtime["controls"],
        }
        self.project_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.emit("log", f"Проект сохранён: {self.project_path}")
        return {"ok": True, "path": str(self.project_path)}

    def open_project(self) -> dict[str, Any]:
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=(
                "HQ BLE Studio (*.hqstudio.json;*.hqrobot.json)",
                "JSON (*.json)",
            ),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        selected = result[0] if isinstance(result, tuple) else result
        path = Path(selected)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Неизвестный формат проекта")
            self.state.restore_from_project(
                {
                    "variables": payload.get("variables", {}),
                    "controls": payload.get("controls", {}),
                }
            )
            self.project_path = path
            return {"ok": True, "payload": payload, "path": str(path)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def shutdown(self, *_args: Any) -> None:
        self.closed = True
        if self.runner:
            self.runner.request_stop()
        if self.robot.connected:
            future = self.async_thread.submit(self.robot.disconnect())
            try:
                future.result(timeout=2)
            except Exception:
                pass
        self.async_thread.shutdown()


def main() -> None:
    api = BlocklyApi()
    index = Path(__file__).with_name("web") / "index.html"
    window = webview.create_window(
        "HQ_BLE Blockly Studio",
        index.as_uri(),
        js_api=api,
        width=1500,
        height=900,
        min_size=(1050, 650),
    )
    api.attach(window)
    window.events.closed += api.shutdown
    webview.start(debug=False)


if __name__ == "__main__":
    main()
