from __future__ import annotations

import asyncio
import os
import re
import signal
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config import LauncherConfig, ModuleConfig
from .ros_monitor import RosTopicMonitor


RUNNING_STATES = {"starting", "running", "stopping"}


@dataclass
class ModuleState:
    id: str
    name: str
    status: str = "stopped"
    pid: int | None = None
    returncode: int | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    log_path: str | None = None
    message: str = ""


class ProcessManager:
    def __init__(self, config: LauncherConfig) -> None:
        self.config = config
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.states = {
            module_id: ModuleState(id=module_id, name=module.name)
            for module_id, module in config.modules.items()
        }
        self.log_queues: set[asyncio.Queue[dict]] = set()
        self.lock = asyncio.Lock()
        self.monitor = RosTopicMonitor(config)
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        self.config.state_dir.mkdir(parents=True, exist_ok=True)

    def list_modules(self) -> list[dict]:
        result = []
        for module_id, module in self.config.modules.items():
            item = asdict(self.states[module_id])
            item.update(
                category=module.category,
                domain_id=module.domain_id,
                autostart=module.autostart,
                restart_on_crash=module.restart_on_crash,
                depends_on=module.depends_on,
                health_nodes=module.health_nodes,
                health_topics=module.health_topics,
                monitor_topics=module.monitor_topics,
            )
            result.append(item)
        return result

    def list_categories(self) -> list[dict]:
        categories: dict[str, dict] = {}
        labels = {
            "sensor": "传感器驱动",
            "algorithm": "算法模块",
        }
        for module_id, module in self.config.modules.items():
            item = categories.setdefault(
                module.category,
                {"id": module.category, "name": labels.get(module.category, module.category), "modules": []},
            )
            item["modules"].append(module_id)
        return list(categories.values())

    def get_state(self, module_id: str) -> dict:
        self._require_module(module_id)
        return asdict(self.states[module_id])

    async def subscribe(self) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self.log_queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self.log_queues.discard(queue)

    async def autostart(self) -> None:
        for module_id, module in self.config.modules.items():
            if module.autostart:
                try:
                    await self.start(module_id)
                    if module.startup_delay > 0:
                        await asyncio.sleep(module.startup_delay)
                except Exception as exc:
                    await self._broadcast(module_id, "error", f"autostart failed: {exc}")

    async def start_many(self, module_ids: list[str]) -> None:
        ordered = self._expand_dependencies(module_ids)
        for module_id in ordered:
            try:
                await self.start(module_id)
            except Exception as exc:
                await self._broadcast(
                    module_id,
                    "warning",
                    f"start failed but sequence continues: {type(exc).__name__}: {exc}",
                )
            delay = self.config.modules[module_id].startup_delay
            if delay > 0:
                await asyncio.sleep(delay)

    async def stop_many(self, module_ids: list[str]) -> None:
        for module_id in reversed(module_ids):
            await self.stop(module_id)

    async def start(self, module_id: str) -> dict:
        self._require_module(module_id)
        async with self.lock:
            state = self.states[module_id]
            if state.status in RUNNING_STATES and state.pid:
                return asdict(state)

            module = self.config.modules[module_id]
            missing_deps = [
                dep for dep in module.depends_on
                if self.states[dep].status not in {"starting", "running"}
            ]
            if missing_deps:
                await self._broadcast(
                    module_id,
                    "warning",
                    f"starting with inactive dependencies: {', '.join(missing_deps)}",
                )

            log_path = self.config.log_dir / f"{module_id}.log"
            argv = self._build_argv(module)
            env = os.environ.copy()
            if module.domain_id is not None:
                env["ROS_DOMAIN_ID"] = str(module.domain_id)
            for key, value in module.env.items():
                env[key] = self._expand_env_value(value, env)
            env["PYTHONUNBUFFERED"] = "1"

            state.status = "starting"
            state.returncode = None
            state.started_at = datetime.now().isoformat(timespec="seconds")
            state.stopped_at = None
            state.log_path = str(log_path)
            state.message = "starting"
            await self._broadcast(module_id, "status", "starting")

            with log_path.open("a", encoding="utf-8", errors="replace") as fp:
                fp.write(f"\n========== {datetime.now():%F %T} start {module_id} ==========\n")
                fp.write(f"workdir: {module.workdir}\n")
                fp.write(f"cmd: {' '.join(argv)}\n")

            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(module.workdir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self.processes[module_id] = process
            state.pid = process.pid
            state.status = "running"
            state.message = "running"
            await self._broadcast(module_id, "status", f"running pid={process.pid}")
            asyncio.create_task(self._pipe_output(module_id, process, log_path))
            asyncio.create_task(self._watch_process(module_id, process))
            return asdict(state)

    async def stop(self, module_id: str) -> dict:
        self._require_module(module_id)
        process = self.processes.get(module_id)
        state = self.states[module_id]
        if not process or process.returncode is not None:
            self.processes.pop(module_id, None)
            state.status = "stopped"
            state.pid = None
            state.message = "stopped"
            state.stopped_at = datetime.now().isoformat(timespec="seconds")
            return asdict(state)

        state.status = "stopping"
        state.message = "stopping"
        await self._broadcast(module_id, "status", "stopping")
        try:
            await self._terminate_process_group(module_id, process)
        except Exception as exc:
            state.status = "crashed"
            state.message = f"stop failed: {type(exc).__name__}: {exc}"
            await self._broadcast(module_id, "error", state.message)
            return asdict(state)
        return await self._mark_stopped(module_id, process)

    async def restart(self, module_id: str) -> dict:
        await self.stop(module_id)
        return await self.start(module_id)

    async def start_category(self, category: str) -> None:
        module_ids = [
            module_id
            for module_id, module in self.config.modules.items()
            if module.category == category
        ]
        await self.start_many(module_ids)

    async def stop_category(self, category: str) -> None:
        module_ids = [
            module_id
            for module_id, module in self.config.modules.items()
            if module.category == category
        ]
        await self.stop_many(module_ids)

    async def measure_sensor_rates(self) -> list[dict]:
        return self.monitor.rates()

    def monitor_status(self) -> dict:
        return self.monitor.status()

    def set_monitor_enabled(self, enabled: bool) -> dict:
        self.monitor.set_enabled(enabled)
        return self.monitor.status()

    async def _pipe_output(
        self,
        module_id: str,
        process: asyncio.subprocess.Process,
        log_path: Path,
    ) -> None:
        assert process.stdout is not None
        with log_path.open("a", encoding="utf-8", errors="replace") as fp:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                fp.write(text + "\n")
                fp.flush()
                await self._broadcast(module_id, "log", text)

    async def _watch_process(self, module_id: str, process: asyncio.subprocess.Process) -> None:
        returncode = await process.wait()
        state = self.states[module_id]
        if self.processes.get(module_id) is process:
            self.processes.pop(module_id, None)
        if state.status == "stopping":
            await self._mark_stopped(module_id, process)
            return

        state.status = "exited" if returncode == 0 else "crashed"
        state.returncode = returncode
        state.pid = None
        state.stopped_at = datetime.now().isoformat(timespec="seconds")
        state.message = f"process exited rc={returncode}"
        await self._broadcast(module_id, "status", state.message)
        module = self.config.modules[module_id]
        if module.restart_on_crash and returncode != 0:
            await asyncio.sleep(2)
            await self.start(module_id)

    async def _terminate_process_group(self, module_id: str, process: asyncio.subprocess.Process) -> None:
        await self._broadcast(module_id, "status", "sending SIGINT")
        await self._send_signal(process, signal.SIGINT)
        if await self._wait_process(process, self.config.stop_timeout_sec):
            return

        await self._broadcast(module_id, "status", "SIGINT timeout, sending SIGTERM")
        await self._send_signal(process, signal.SIGTERM)
        if await self._wait_process(process, 3.0):
            return

        await self._broadcast(module_id, "status", "SIGTERM timeout, sending SIGKILL")
        await self._send_signal(process, signal.SIGKILL)
        if await self._wait_process(process, 2.0):
            return

        # Last resort: avoid leaving the UI in stopping forever. If process.wait()
        # does not complete after SIGKILL, it is usually a stale process handle or
        # a child/pipe cleanup edge case; the OS has already been asked to kill it.
        await self._broadcast(module_id, "error", "SIGKILL wait timeout; marking stopped")

    @staticmethod
    async def _wait_process(process: asyncio.subprocess.Process, timeout: float) -> bool:
        if process.returncode is not None:
            return True
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def _mark_stopped(self, module_id: str, process: asyncio.subprocess.Process) -> dict:
        state = self.states[module_id]
        self.processes.pop(module_id, None)
        state.status = "stopped"
        state.pid = None
        state.returncode = process.returncode
        state.stopped_at = datetime.now().isoformat(timespec="seconds")
        state.message = f"stopped rc={process.returncode}"
        await self._broadcast(module_id, "status", state.message)
        return asdict(state)

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

    def _build_argv(self, module: ModuleConfig) -> list[str]:
        source_lines = ["set -e", f"cd {self._sh_quote(str(module.workdir))}"]
        if module.setup:
            source_lines.append(f"source {self._sh_quote(module.setup)}")
        if module.conda_env:
            conda_sh = module.conda_sh or "$HOME/miniconda3/etc/profile.d/conda.sh"
            source_lines.append(f"source {conda_sh}")
            source_lines.append(f"conda activate {self._sh_quote(module.conda_env)}")
        source_lines.append('exec "$@"')
        script = "\n".join(source_lines)
        return ["bash", "-lc", script, "bash", *module.cmd]

    def _expand_dependencies(self, module_ids: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()

        def visit(module_id: str) -> None:
            self._require_module(module_id)
            if module_id in seen:
                return
            for dep in self.config.modules[module_id].depends_on:
                visit(dep)
            seen.add(module_id)
            result.append(module_id)

        for item in module_ids:
            visit(item)
        return result

    def _require_module(self, module_id: str) -> None:
        if module_id not in self.config.modules:
            raise KeyError(f"unknown module: {module_id}")

    async def _broadcast(self, module_id: str, event: str, message: str) -> None:
        payload = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "module": module_id,
            "event": event,
            "message": message,
            "state": asdict(self.states[module_id]),
        }
        for queue in list(self.log_queues):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    @staticmethod
    def _sh_quote(value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"

    @staticmethod
    def _expand_env_value(value: str, env: dict[str, str]) -> str:
        pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

        def replace(match: re.Match[str]) -> str:
            key = match.group(1) or match.group(2)
            return env.get(key, "")

        return pattern.sub(replace, value)
