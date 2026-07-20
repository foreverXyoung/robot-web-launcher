from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from .config import LauncherConfig


Broadcast = Callable[[str, str, str], Awaitable[None]]


@dataclass
class RecorderState:
    status: str = "stopped"
    pid: int | None = None
    mode: str | None = None
    domain_id: int | None = None
    output_path: str | None = None
    topics: list[str] | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    returncode: int | None = None
    message: str = "stopped"


class RecorderManager:
    def __init__(self, config: LauncherConfig, broadcast: Broadcast) -> None:
        self.config = config
        self.broadcast = broadcast
        self.process: asyncio.subprocess.Process | None = None
        self.state = RecorderState()
        self.lock = asyncio.Lock()
        self.config.recorder.output_dir.mkdir(parents=True, exist_ok=True)
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self._restore_state()

    def status(self) -> dict:
        result = asdict(self.state)
        result.update(
            enabled=self.config.recorder.enabled,
            output_dir=str(self.config.recorder.output_dir),
            default_domain_id=self.config.recorder.default_domain_id,
            min_free_gb=self.config.recorder.min_free_gb,
            modes=[
                {"id": mode.id, "name": mode.name, "topics": mode.topics}
                for mode in self.config.recorder.modes.values()
            ],
            free_gb=self._free_gb(self.config.recorder.output_dir),
        )
        return result

    async def start(self, mode: str, domain_id: int | None, name: str | None) -> dict:
        if not self.config.recorder.enabled:
            raise ValueError("recorder is disabled")
        if mode not in self.config.recorder.modes:
            raise KeyError(f"unknown recorder mode: {mode}")

        async with self.lock:
            if self.process and self.process.returncode is None:
                return self.status()
            if self.state.pid and self._pid_alive(self.state.pid):
                return self.status()

            recorder = self.config.recorder
            free_gb = self._free_gb(recorder.output_dir)
            if free_gb < recorder.min_free_gb:
                raise RuntimeError(
                    f"insufficient disk space: {free_gb:.1f} GB free, need at least {recorder.min_free_gb:.1f} GB"
                )

            mode_config = recorder.modes[mode]
            topics = list(dict.fromkeys(mode_config.topics))
            if not topics:
                raise ValueError(f"recorder mode {mode} has no topics")

            effective_domain = int(domain_id if domain_id is not None else recorder.default_domain_id)
            output_path = self._make_output_path(name or mode)
            env = os.environ.copy()
            env["ROS_DOMAIN_ID"] = str(effective_domain)
            env["PYTHONUNBUFFERED"] = "1"

            argv = ["ros2", "bag", "record", "-o", str(output_path), *topics]
            self.state = RecorderState(
                status="starting",
                pid=None,
                mode=mode,
                domain_id=effective_domain,
                output_path=str(output_path),
                topics=topics,
                started_at=datetime.now().isoformat(timespec="seconds"),
                stopped_at=None,
                returncode=None,
                message="starting",
            )
            await self.broadcast("status", f"starting mode={mode} domain={effective_domain}")

            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(recorder.output_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self.process = process
            self.state.pid = process.pid
            self.state.status = "running"
            self.state.message = f"recording pid={process.pid}"
            self._save_state()
            await self.broadcast("status", self.state.message)
            asyncio.create_task(self._pipe_output(process))
            asyncio.create_task(self._watch_process(process))
            return self.status()

    async def stop(self) -> dict:
        async with self.lock:
            process = self.process
            if process and process.returncode is None:
                self.state.status = "stopping"
                self.state.message = "stopping"
                await self.broadcast("status", "sending SIGINT")
                await self._send_signal(process, signal.SIGINT)
                if not await self._wait_process(process, self.config.recorder.stop_timeout_sec):
                    await self.broadcast("status", "SIGINT timeout, sending SIGTERM")
                    await self._send_signal(process, signal.SIGTERM)
                    if not await self._wait_process(process, 3.0):
                        await self.broadcast("status", "SIGTERM timeout, sending SIGKILL")
                        await self._send_signal(process, signal.SIGKILL)
                        await self._wait_process(process, 2.0)
                return await self._mark_stopped(process.returncode)

            if self.state.pid and self._pid_alive(self.state.pid):
                self.state.status = "stopping"
                self.state.message = "stopping recovered process"
                await self.broadcast("status", f"sending SIGINT to recovered pid={self.state.pid}")
                self._send_signal_to_pid(self.state.pid, signal.SIGINT)
                if not await self._wait_pid_exit(self.state.pid, self.config.recorder.stop_timeout_sec):
                    await self.broadcast("status", "SIGINT timeout, sending SIGTERM")
                    self._send_signal_to_pid(self.state.pid, signal.SIGTERM)
                    if not await self._wait_pid_exit(self.state.pid, 3.0):
                        await self.broadcast("status", "SIGTERM timeout, sending SIGKILL")
                        self._send_signal_to_pid(self.state.pid, signal.SIGKILL)
                        await self._wait_pid_exit(self.state.pid, 2.0)
                return await self._mark_stopped(None)

            self._clear_state()
            return self.status()

    async def shutdown(self) -> None:
        if self.state.status in {"starting", "running", "stopping"} or (
            self.state.pid and self._pid_alive(self.state.pid)
        ):
            await self.stop()

    async def _pipe_output(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            await self.broadcast("log", text)

    async def _watch_process(self, process: asyncio.subprocess.Process) -> None:
        returncode = await process.wait()
        if self.process is process:
            self.process = None
        if self.state.status == "stopping":
            await self._mark_stopped(returncode)
            return
        self.state.status = "exited" if returncode == 0 else "crashed"
        self.state.pid = None
        self.state.returncode = returncode
        self.state.stopped_at = datetime.now().isoformat(timespec="seconds")
        self.state.message = f"process exited rc={returncode}"
        self._remove_state_file()
        await self.broadcast("status", self.state.message)

    async def _mark_stopped(self, returncode: int | None) -> dict:
        self.process = None
        self.state.status = "stopped"
        self.state.pid = None
        self.state.returncode = returncode
        self.state.stopped_at = datetime.now().isoformat(timespec="seconds")
        self.state.message = f"stopped rc={returncode}"
        self._remove_state_file()
        await self.broadcast("status", self.state.message)
        return self.status()

    async def _send_signal(self, process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except ProcessLookupError:
            return
        except Exception:
            try:
                process.send_signal(sig)
            except ProcessLookupError:
                return

    @staticmethod
    async def _wait_process(process: asyncio.subprocess.Process, timeout: float) -> bool:
        if process.returncode is not None:
            return True
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    def _send_signal_to_pid(self, pid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(os.getpgid(pid), sig)
        except ProcessLookupError:
            return
        except Exception:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return

    async def _wait_pid_exit(self, pid: int, timeout: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if not self._pid_alive(pid):
                return True
            await asyncio.sleep(0.1)
        return not self._pid_alive(pid)

    def _make_output_path(self, name: str) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()) or "record"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = self.config.recorder.output_dir / f"{safe_name}_{timestamp}"
        path = base
        index = 1
        while path.exists():
            path = self.config.recorder.output_dir / f"{base.name}_{index}"
            index += 1
        return path

    def _restore_state(self) -> None:
        data = self._load_state_file()
        pid = data.get("pid") if data else None
        if not isinstance(pid, int) or not self._pid_alive(pid):
            self._remove_state_file()
            return
        self.state = RecorderState(
            status="running",
            pid=pid,
            mode=data.get("mode"),
            domain_id=data.get("domain_id"),
            output_path=data.get("output_path"),
            topics=data.get("topics"),
            started_at=data.get("started_at"),
            stopped_at=None,
            returncode=None,
            message=f"recovered running pid={pid}",
        )

    def _save_state(self) -> None:
        tmp_path = self._state_path().with_suffix(".tmp")
        tmp_path.write_text(json.dumps(asdict(self.state), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._state_path())

    def _load_state_file(self) -> dict:
        try:
            return json.loads(self._state_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def _remove_state_file(self) -> None:
        try:
            self._state_path().unlink()
        except FileNotFoundError:
            pass

    def _clear_state(self) -> None:
        self.process = None
        self.state = RecorderState()
        self._remove_state_file()

    def _state_path(self) -> Path:
        return self.config.state_dir / "recorder.json"

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @staticmethod
    def _free_gb(path: Path) -> float:
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
        return usage.free / (1024 ** 3)
