from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from datetime import datetime
from typing import Awaitable, Callable

from .config import DebugPublishCommandConfig, LauncherConfig


class DebugPublisherManager:
    def __init__(
        self,
        config: LauncherConfig,
        publish_event: Callable[[str, str], Awaitable[None]],
        timeout_sec: float = 8.0,
    ) -> None:
        self.config = config
        self.publish_event = publish_event
        self.timeout_sec = timeout_sec

    def list_commands(self) -> dict:
        return {
            "enabled": self.config.debug_publish.enabled,
            "commands": [
                asdict(command)
                for command in self.config.debug_publish.commands.values()
            ],
        }

    async def publish(self, command_id: str) -> dict:
        if not self.config.debug_publish.enabled:
            raise RuntimeError("debug publish is disabled")
        command = self.config.debug_publish.commands.get(command_id)
        if command is None:
            raise KeyError(f"unknown debug publish command: {command_id}")

        await self.publish_event("status", f"publishing {command.topic} {command.payload}")
        argv = self._build_argv(command)
        env = os.environ.copy()
        if command.domain_id is not None:
            env["ROS_DOMAIN_ID"] = str(command.domain_id)

        process = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.timeout_sec)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            await self.publish_event("error", f"{command_id} timeout after {self.timeout_sec}s")
            raise RuntimeError(f"debug publish timeout after {self.timeout_sec}s") from exc

        output = stdout.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            await self.publish_event("error", f"{command_id} failed rc={process.returncode}: {output}")
            raise RuntimeError(output or f"ros2 topic pub exited {process.returncode}")

        message = f"{command.name} sent to {command.topic}"
        await self.publish_event("status", message)
        return {
            "ok": True,
            "id": command.id,
            "name": command.name,
            "topic": command.topic,
            "msg_type": command.msg_type,
            "payload": command.payload,
            "domain_id": command.domain_id,
            "time": datetime.now().isoformat(timespec="seconds"),
            "output": output,
        }

    def _build_argv(self, command: DebugPublishCommandConfig) -> list[str]:
        source_lines = ["set -e"]
        if self.config.ros_setup.is_file():
            source_lines.append(f"source {self._sh_quote(str(self.config.ros_setup))}")
        source_lines.append('exec "$@"')
        script = "\n".join(source_lines)
        return [
            "bash",
            "-lc",
            script,
            "bash",
            "ros2",
            "topic",
            "pub",
            "--once",
            "--wait-matching-subscriptions",
            "0",
            command.topic,
            command.msg_type,
            command.payload,
        ]

    @staticmethod
    def _sh_quote(value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"
