from __future__ import annotations

import asyncio
import json
import urllib.request
from datetime import datetime
from typing import Any

from .config import ClusterHostConfig, LauncherConfig
from .manager import ProcessManager


CATEGORY_LABELS = {
    "sensor": "传感器驱动",
    "algorithm": "算法模块",
    "control": "控制接口",
}
CATEGORY_ORDER = {"sensor": 0, "algorithm": 1, "control": 2}


class RemoteAgentClient:
    def __init__(self, host: ClusterHostConfig, timeout: float = 2.0) -> None:
        if not host.base_url:
            raise ValueError(f"remote host {host.id} missing base_url")
        self.host = host
        self.base_url = host.base_url.rstrip("/")
        self.timeout = timeout

    async def get(self, path: str) -> Any:
        return await asyncio.to_thread(self._request, "GET", path, None)

    async def post(self, path: str, payload: dict | None = None) -> Any:
        return await asyncio.to_thread(self._request, "POST", path, payload or {})

    def _request(self, method: str, path: str, payload: dict | None) -> Any:
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


class ClusterManager:
    def __init__(self, config: LauncherConfig, local: ProcessManager) -> None:
        self.config = config
        self.local = local
        self.enabled = config.cluster.enabled
        self.self_host_id = config.cluster.self_host
        self.hosts = config.cluster.hosts
        self.local_host = self.hosts.get(self.self_host_id)
        self.remote_clients = {
            host_id: RemoteAgentClient(host)
            for host_id, host in self.hosts.items()
            if host_id != self.self_host_id and host.kind == "remote"
        }
        self.log_queues: set[asyncio.Queue[dict]] = set()
        self._local_queue: asyncio.Queue[dict] | None = None
        self._local_task: asyncio.Task | None = None
        self._remote_tasks: list[asyncio.Task] = []

    def cluster_active(self) -> bool:
        return self.enabled and bool(self.hosts)

    async def startup(self) -> None:
        if not self.cluster_active():
            return
        self._local_queue = await self.local.subscribe()
        self._local_task = asyncio.create_task(self._forward_local_events(self._local_queue))
        for host_id, client in self.remote_clients.items():
            self._remote_tasks.append(asyncio.create_task(self._forward_remote_events(host_id, client)))

    async def shutdown(self) -> None:
        if self._local_queue is not None:
            self.local.unsubscribe(self._local_queue)
        if self._local_task is not None:
            self._local_task.cancel()
        for task in self._remote_tasks:
            task.cancel()
        await asyncio.gather(
            *([self._local_task] if self._local_task is not None else []),
            *self._remote_tasks,
            return_exceptions=True,
        )

    async def subscribe(self) -> asyncio.Queue[dict]:
        if not self.cluster_active():
            return await self.local.subscribe()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self.log_queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        if not self.cluster_active():
            self.local.unsubscribe(queue)
            return
        self.log_queues.discard(queue)

    async def list_modules(self) -> list[dict]:
        if not self.cluster_active():
            return self.local.list_modules()

        result = [
            self._prefix_module(self.self_host_id, self._host_name(self.self_host_id), item)
            for item in self.local.list_modules()
        ]
        for host_id, client in self.remote_clients.items():
            try:
                remote_modules = await client.get("/api/modules")
                result.extend(
                    self._prefix_module(host_id, self._host_name(host_id), item)
                    for item in remote_modules
                )
            except Exception as exc:
                result.append(self._offline_module(host_id, exc))
        return result

    async def list_categories(self) -> list[dict]:
        if not self.cluster_active():
            return self.local.list_categories()
        modules = await self.list_modules()
        categories: dict[str, dict] = {}
        for module in modules:
            if module.get("offline"):
                continue
            host_id = module["host_id"]
            category = module.get("category", "algorithm")
            key = f"{host_id}:{category}"
            item = categories.setdefault(
                key,
                {
                    "id": key,
                    "name": f"{module['host_name']} / {CATEGORY_LABELS.get(category, category)}",
                    "modules": [],
                },
            )
            item["modules"].append(module["id"])
        host_order = {host_id: index for index, host_id in enumerate(self.hosts)}
        return sorted(
            categories.values(),
            key=lambda item: (
                host_order.get(item["id"].split(":", 1)[0], 99),
                CATEGORY_ORDER.get(item["id"].split(":", 1)[1], 99),
            ),
        )

    async def validate_config(self) -> dict:
        if not self.cluster_active():
            return self.local.validate_config()
        result = self.local.validate_config()
        checks = list(result["checks"])
        for host_id, client in self.remote_clients.items():
            try:
                remote = await client.get("/api/config-check")
                for item in remote.get("checks", []):
                    checks.append({**item, "scope": f"{host_id}.{item.get('scope', 'remote')}"})
            except Exception as exc:
                checks.append(
                    {
                        "scope": host_id,
                        "field": "base_url",
                        "value": client.base_url,
                        "ok": False,
                        "severity": "error",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
        errors = [item for item in checks if not item["ok"] and item["severity"] == "error"]
        warnings = [item for item in checks if not item["ok"] and item["severity"] == "warning"]
        return {"ok": not errors, "errors": len(errors), "warnings": len(warnings), "checks": checks}

    async def sensor_rates(self) -> list[dict]:
        if not self.cluster_active():
            return await self.local.measure_sensor_rates()
        result = [
            self._prefix_rate(self.self_host_id, item)
            for item in await self.local.measure_sensor_rates()
        ]
        for host_id, client in self.remote_clients.items():
            try:
                remote = await client.get("/api/sensor-rates")
                result.extend(self._prefix_rate(host_id, item) for item in remote)
            except Exception as exc:
                result.append(
                    {
                        "module": f"{host_id}:offline",
                        "topic": "remote agent",
                        "domain_id": None,
                        "hz": None,
                        "status": "error",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
        return result

    async def monitor_status(self) -> dict:
        status = self.local.monitor_status()
        if not self.cluster_active():
            return status
        remote = {}
        for host_id, client in self.remote_clients.items():
            try:
                remote[host_id] = await client.get("/api/monitor")
            except Exception as exc:
                remote[host_id] = {"enabled": False, "started": False, "error": f"{type(exc).__name__}: {exc}"}
        return {**status, "cluster": True, "remote": remote}

    async def cluster_status(self) -> dict:
        if not self.cluster_active():
            return {
                "enabled": False,
                "self": self.self_host_id,
                "hosts": [
                    {
                        "id": self.self_host_id,
                        "name": self.local_host.name if self.local_host else "本机",
                        "kind": "local",
                        "online": True,
                        "base_url": None,
                        "error": None,
                    }
                ],
            }

        hosts = []
        for host_id, host in self.hosts.items():
            if host_id == self.self_host_id or host.kind == "local":
                hosts.append(
                    {
                        "id": host_id,
                        "name": host.name,
                        "kind": "local",
                        "online": True,
                        "base_url": host.base_url,
                        "error": None,
                    }
                )
                continue

            client = self.remote_clients.get(host_id)
            if client is None:
                hosts.append(
                    {
                        "id": host_id,
                        "name": host.name,
                        "kind": host.kind,
                        "online": False,
                        "base_url": host.base_url,
                        "error": "missing remote client",
                    }
                )
                continue

            try:
                await client.get("/api/monitor")
                hosts.append(
                    {
                        "id": host_id,
                        "name": host.name,
                        "kind": host.kind,
                        "online": True,
                        "base_url": client.base_url,
                        "error": None,
                    }
                )
            except Exception as exc:
                hosts.append(
                    {
                        "id": host_id,
                        "name": host.name,
                        "kind": host.kind,
                        "online": False,
                        "base_url": client.base_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        return {"enabled": True, "self": self.self_host_id, "hosts": hosts}

    async def set_monitor_enabled(self, enabled: bool) -> dict:
        self.local.set_monitor_enabled(enabled)
        if self.cluster_active():
            for client in self.remote_clients.values():
                try:
                    await client.post("/api/monitor", {"enabled": enabled})
                except Exception:
                    pass
        return await self.monitor_status()

    async def start_many(self, module_ids: list[str]) -> None:
        if not self.cluster_active():
            await self.local.start_many(module_ids)
            return
        grouped = self._group_module_ids(module_ids)
        for host_id, ids in grouped.items():
            if host_id == self.self_host_id:
                await self.local.start_many(ids)
            else:
                await self.remote_clients[host_id].post("/api/start-selected", {"modules": ids})

    async def stop_many(self, module_ids: list[str]) -> None:
        if not self.cluster_active():
            await self.local.stop_many(module_ids)
            return
        grouped = self._group_module_ids(module_ids)
        for host_id, ids in grouped.items():
            if host_id == self.self_host_id:
                await self.local.stop_many(ids)
            else:
                await self.remote_clients[host_id].post("/api/stop-selected", {"modules": ids})

    async def start_module(self, module_id: str) -> dict:
        if not self.cluster_active():
            await self.local.start_many([module_id])
            return self.local.get_state(module_id)
        host_id, raw_id = self._split_module_id(module_id)
        if host_id == self.self_host_id:
            await self.local.start_many([raw_id])
            return self.local.get_state(raw_id)
        state = await self.remote_clients[host_id].post(f"/api/modules/{raw_id}/start")
        return self._prefix_module(host_id, self._host_name(host_id), state)

    async def stop_module(self, module_id: str) -> dict:
        if not self.cluster_active():
            return await self.local.stop(module_id)
        host_id, raw_id = self._split_module_id(module_id)
        if host_id == self.self_host_id:
            return await self.local.stop(raw_id)
        state = await self.remote_clients[host_id].post(f"/api/modules/{raw_id}/stop")
        return self._prefix_module(host_id, self._host_name(host_id), state)

    async def restart_module(self, module_id: str) -> dict:
        if not self.cluster_active():
            await self.local.stop(module_id)
            await self.local.start_many([module_id])
            return self.local.get_state(module_id)
        host_id, raw_id = self._split_module_id(module_id)
        if host_id == self.self_host_id:
            await self.local.stop(raw_id)
            await self.local.start_many([raw_id])
            return self.local.get_state(raw_id)
        state = await self.remote_clients[host_id].post(f"/api/modules/{raw_id}/restart")
        return self._prefix_module(host_id, self._host_name(host_id), state)

    async def start_category(self, category_key: str) -> None:
        if not self.cluster_active():
            await self.local.start_category(category_key)
            return
        host_id, category = self._split_category_id(category_key)
        if host_id == self.self_host_id:
            await self.local.start_category(category)
        else:
            await self.remote_clients[host_id].post(f"/api/categories/{category}/start")

    async def stop_category(self, category_key: str) -> None:
        if not self.cluster_active():
            await self.local.stop_category(category_key)
            return
        host_id, category = self._split_category_id(category_key)
        if host_id == self.self_host_id:
            await self.local.stop_category(category)
        else:
            await self.remote_clients[host_id].post(f"/api/categories/{category}/stop")

    def _prefix_module(self, host_id: str, host_name: str, item: dict) -> dict:
        raw_id = item["id"]
        prefixed_deps = [f"{host_id}:{dep}" for dep in item.get("depends_on", [])]
        return {
            **item,
            "id": f"{host_id}:{raw_id}",
            "module_id": raw_id,
            "host_id": host_id,
            "host_name": host_name,
            "category_key": f"{host_id}:{item.get('category', 'algorithm')}",
            "depends_on": prefixed_deps,
        }

    def _prefix_rate(self, host_id: str, item: dict) -> dict:
        return {**item, "module": f"{host_id}:{item['module']}", "host_id": host_id, "host_name": self._host_name(host_id)}

    def _offline_module(self, host_id: str, exc: Exception) -> dict:
        return {
            "id": f"{host_id}:offline",
            "module_id": "offline",
            "host_id": host_id,
            "host_name": self._host_name(host_id),
            "name": f"{self._host_name(host_id)} 离线",
            "status": "crashed",
            "pid": None,
            "category": "algorithm",
            "category_key": f"{host_id}:algorithm",
            "domain_id": None,
            "autostart": False,
            "restart_on_crash": False,
            "depends_on": [],
            "monitor_topics": [],
            "message": f"{type(exc).__name__}: {exc}",
            "offline": True,
        }

    def _group_module_ids(self, module_ids: list[str]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for module_id in module_ids:
            host_id, raw_id = self._split_module_id(module_id)
            if raw_id == "offline":
                continue
            grouped.setdefault(host_id, []).append(raw_id)
        return grouped

    def _split_module_id(self, module_id: str) -> tuple[str, str]:
        if not self.cluster_active():
            return self.self_host_id, module_id
        if ":" not in module_id:
            return self.self_host_id, module_id
        host_id, raw_id = module_id.split(":", 1)
        if host_id not in self.hosts:
            raise KeyError(f"unknown cluster host: {host_id}")
        return host_id, raw_id

    def _split_category_id(self, category_id: str) -> tuple[str, str]:
        if not self.cluster_active():
            return self.self_host_id, category_id
        if ":" not in category_id:
            return self.self_host_id, category_id
        host_id, category = category_id.split(":", 1)
        if host_id not in self.hosts:
            raise KeyError(f"unknown cluster host: {host_id}")
        return host_id, category

    def _host_name(self, host_id: str) -> str:
        host = self.hosts.get(host_id)
        return host.name if host else host_id

    async def _forward_local_events(self, queue: asyncio.Queue[dict]) -> None:
        while True:
            payload = await queue.get()
            await self._broadcast(self._prefix_event(self.self_host_id, payload))

    async def _forward_remote_events(self, host_id: str, client: RemoteAgentClient) -> None:
        while True:
            try:
                import websockets

                ws_url = client.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/events"
                async with websockets.connect(ws_url) as websocket:
                    await self._broadcast(
                        self._system_event(host_id, "status", f"{self._host_name(host_id)} 日志已连接")
                    )
                    async for message in websocket:
                        payload = json.loads(message)
                        await self._broadcast(self._prefix_event(host_id, payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._broadcast(
                    self._system_event(host_id, "warning", f"remote log disconnected: {type(exc).__name__}: {exc}")
                )
                await asyncio.sleep(2.0)

    def _prefix_event(self, host_id: str, payload: dict) -> dict:
        module = payload.get("module", "system")
        return {
            **payload,
            "module": f"{host_id}:{module}",
            "host_id": host_id,
            "host_name": self._host_name(host_id),
        }

    def _system_event(self, host_id: str, event: str, message: str) -> dict:
        return {
            "time": datetime.now().isoformat(timespec="seconds"),
            "module": f"{host_id}:system",
            "host_id": host_id,
            "host_name": self._host_name(host_id),
            "event": event,
            "message": message,
            "state": None,
        }

    async def _broadcast(self, payload: dict) -> None:
        for queue in list(self.log_queues):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass
