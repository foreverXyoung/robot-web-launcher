from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
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
        self._restore_process_states()

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
            "control": "控制接口",
        }
        for module_id, module in self.config.modules.items():
            item = categories.setdefault(
                module.category,
                {"id": module.category, "name": labels.get(module.category, module.category), "modules": []},
            )
            item["modules"].append(module_id)
        return list(categories.values())

    def validate_config(self) -> dict:
        checks: list[dict] = []

        def add(scope: str, field: str, path: Path | str, ok: bool, severity: str = "error") -> None:
            checks.append(
                {
                    "scope": scope,
                    "field": field,
                    "value": str(path),
                    "ok": ok,
                    "severity": "ok" if ok else severity,
                }
            )

        add("launcher", "ros_setup", self.config.ros_setup, self.config.ros_setup.is_file())
        for index, setup in enumerate(self.config.monitor_setups):
            add("launcher", f"monitor_setups[{index}]", setup, setup.is_file(), "warning")
        recorder_output = self.config.recorder.output_dir
        add("recorder", "output_dir_parent", recorder_output.parent, recorder_output.parent.is_dir())
        add("recorder", "ros2", shutil.which("ros2") or "ros2", shutil.which("ros2") is not None)

        for module_id, module in self.config.modules.items():
            add(module_id, "workdir", module.workdir, module.workdir.is_dir())

            if module.setup:
                setup_path = Path(module.setup).expanduser()
                if not setup_path.is_absolute():
                    setup_path = module.workdir / setup_path
                add(module_id, "setup", setup_path, setup_path.is_file())

            executable = module.cmd[0]
            if "/" in executable or "\\" in executable:
                executable_path = Path(executable).expanduser()
                if not executable_path.is_absolute():
                    executable_path = module.workdir / executable_path
                add(module_id, "cmd[0]", executable_path, executable_path.is_file())
            else:
                resolved = shutil.which(executable)
                add(module_id, "cmd[0]", resolved or executable, resolved is not None)

            if module.python_script:
                script_path = Path(module.python_script).expanduser()
                if not script_path.is_absolute():
                    script_path = module.workdir / script_path
                add(module_id, "python_script", script_path, script_path.is_file())

        errors = [item for item in checks if not item["ok"] and item["severity"] == "error"]
        warnings = [item for item in checks if not item["ok"] and item["severity"] == "warning"]
        return {
            "ok": not errors,
            "errors": len(errors),
            "warnings": len(warnings),
            "checks": checks,
        }

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
        for module_id in module_ids:
            self._require_module(module_id)
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

    async def shutdown(self) -> None:
        await self.stop_many(list(self.config.modules.keys()))
        self.monitor.set_enabled(False)

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
            self._save_process_state(module_id, process.pid)
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
            if state.pid and self._pid_alive(state.pid):
                state.status = "stopping"
                state.message = "stopping recovered process"
                await self._broadcast(module_id, "status", f"stopping recovered pid={state.pid}")
                try:
                    await self._terminate_pid_group(module_id, state.pid)
                except Exception as exc:
                    state.status = "crashed"
                    state.message = f"stop failed: {type(exc).__name__}: {exc}"
                    await self._broadcast(module_id, "error", state.message)
                    return asdict(state)
                return await self._mark_stopped_pid(module_id)
            state.status = "stopped"
            state.pid = None
            state.message = "stopped"
            state.stopped_at = datetime.now().isoformat(timespec="seconds")
            self._remove_process_state(module_id)
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
        results = []
        for item in self.monitor.rates():
            state = self.states.get(item["module"])
            if state is not None and state.status not in RUNNING_STATES:
                if item.get("hz") is None or item.get("hz") == 0:
                    item = {
                        **item,
                        "hz": None,
                        "status": "inactive",
                        "message": f"module {state.status}",
                    }
                else:
                    item = {
                        **item,
                        "status": "unexpected_publisher",
                        "message": f"module {state.status}, but topic is still receiving data",
                    }
            results.append(item)
        return results

    def monitor_status(self) -> dict:
        return self.monitor.status()

    def set_monitor_enabled(self, enabled: bool) -> dict:
        self.monitor.set_enabled(enabled)
        return self.monitor.status()

    async def publish_event(self, module_id: str, event: str, message: str) -> None:
        await self._broadcast(module_id, event, message)

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
        self._remove_process_state(module_id)
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
        self._remove_process_state(module_id)
        await self._broadcast(module_id, "status", state.message)
        return asdict(state)

    async def _mark_stopped_pid(self, module_id: str) -> dict:
        state = self.states[module_id]
        self.processes.pop(module_id, None)
        state.status = "stopped"
        state.pid = None
        state.returncode = None
        state.stopped_at = datetime.now().isoformat(timespec="seconds")
        state.message = "stopped recovered process"
        self._remove_process_state(module_id)
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

    async def _terminate_pid_group(self, module_id: str, pid: int) -> None:
        await self._broadcast(module_id, "status", "sending SIGINT")
        self._send_signal_to_pid(pid, signal.SIGINT)
        if await self._wait_pid_exit(pid, self.config.stop_timeout_sec):
            return

        await self._broadcast(module_id, "status", "SIGINT timeout, sending SIGTERM")
        self._send_signal_to_pid(pid, signal.SIGTERM)
        if await self._wait_pid_exit(pid, 3.0):
            return

        await self._broadcast(module_id, "status", "SIGTERM timeout, sending SIGKILL")
        self._send_signal_to_pid(pid, signal.SIGKILL)
        if await self._wait_pid_exit(pid, 2.0):
            return

        await self._broadcast(module_id, "error", "SIGKILL wait timeout; marking stopped")

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

    def _restore_process_states(self) -> None:
        for module_id in self.config.modules:
            data = self._load_process_state(module_id)
            pid = data.get("pid") if data else None
            if not isinstance(pid, int) or not self._pid_alive(pid):
                self._remove_process_state(module_id)
                continue
            state = self.states[module_id]
            state.status = "running"
            state.pid = pid
            state.returncode = None
            state.started_at = data.get("started_at")
            state.stopped_at = None
            state.log_path = data.get("log_path")
            state.message = f"recovered running pid={pid}"

    def _save_process_state(self, module_id: str, pid: int) -> None:
        state = self.states[module_id]
        payload = {
            "module_id": module_id,
            "pid": pid,
            "started_at": state.started_at,
            "log_path": state.log_path,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        path = self._process_state_path(module_id)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _load_process_state(self, module_id: str) -> dict:
        path = self._process_state_path(module_id)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _remove_process_state(self, module_id: str) -> None:
        try:
            self._process_state_path(module_id).unlink()
        except FileNotFoundError:
            pass

    def _process_state_path(self, module_id: str) -> Path:
        return self.config.state_dir / f"{module_id}.json"

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

    def _require_module(self, module_id: str) -> None:
        if module_id not in self.config.modules:
            raise KeyError(f"unknown module: {module_id}")

    async def _broadcast(self, module_id: str, event: str, message: str) -> None:
        payload = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "module": module_id,
            "event": event,
            "message": message,
            "state": asdict(self.states[module_id]) if module_id in self.states else None,
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
