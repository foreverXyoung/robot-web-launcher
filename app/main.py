from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .cluster import ClusterManager
from .config import load_config
from .debug_publisher import DebugPublisherManager
from .manager import ProcessManager
from .recorder import RecorderManager


BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.environ.get("ROBOT_LAUNCHER_CONFIG", BASE_DIR / "config" / "modules.yaml"))

config = load_config(CONFIG_PATH)
manager = ProcessManager(config)
recorder = RecorderManager(config, lambda event, message: manager.publish_event("recorder", event, message))
debug_publisher = DebugPublisherManager(config, lambda event, message: manager.publish_event("debug", event, message))
cluster = ClusterManager(config, manager)
app = FastAPI(title="Robot Web Launcher")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class ModuleSelection(BaseModel):
    modules: list[str]


class MonitorToggle(BaseModel):
    enabled: bool


class RecorderStart(BaseModel):
    mode: str
    domain_id: int | None = None
    name: str | None = None


@app.on_event("startup")
async def on_startup() -> None:
    await cluster.startup()
    manager.set_monitor_enabled(False)
    if os.environ.get("ROBOT_LAUNCHER_AUTOSTART", "0") == "1":
        await manager.autostart()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await cluster.shutdown()
    await recorder.shutdown()
    await manager.shutdown()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/modules")
async def list_modules() -> list[dict]:
    return await cluster.list_modules()


@app.get("/api/categories")
async def list_categories() -> list[dict]:
    return await cluster.list_categories()


@app.get("/api/config-check")
async def config_check() -> dict:
    return await cluster.validate_config()


@app.get("/api/sensor-rates")
async def sensor_rates() -> list[dict]:
    try:
        return await cluster.sensor_rates()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/monitor")
async def monitor_status() -> dict:
    return await cluster.monitor_status()


@app.get("/api/cluster")
async def cluster_status() -> dict:
    return await cluster.cluster_status()


@app.post("/api/monitor")
async def set_monitor(toggle: MonitorToggle) -> dict:
    return await cluster.set_monitor_enabled(toggle.enabled)


@app.get("/api/recorder")
async def recorder_status() -> dict:
    return recorder.status()


@app.post("/api/recorder/start")
async def start_recorder(request: RecorderStart) -> dict:
    try:
        return await recorder.start(request.mode, request.domain_id, request.name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/recorder/stop")
async def stop_recorder() -> dict:
    try:
        return await recorder.stop()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/debug-publishers")
async def debug_publishers() -> dict:
    return debug_publisher.list_commands()


@app.post("/api/debug-publishers/{command_id}/publish")
async def publish_debug_command(command_id: str) -> dict:
    try:
        return await debug_publisher.publish(command_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/modules/{module_id}/start")
async def start_module(module_id: str) -> dict:
    try:
        return await cluster.start_module(module_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/modules/{module_id}/stop")
async def stop_module(module_id: str) -> dict:
    try:
        return await cluster.stop_module(module_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/modules/{module_id}/restart")
async def restart_module(module_id: str) -> dict:
    try:
        return await cluster.restart_module(module_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/start-selected")
async def start_selected(selection: ModuleSelection) -> dict:
    try:
        await cluster.start_many(selection.modules)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/stop-selected")
async def stop_selected(selection: ModuleSelection) -> dict:
    try:
        await cluster.stop_many(selection.modules)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/categories/{category}/start")
async def start_category(category: str) -> dict:
    try:
        await cluster.start_category(category)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/categories/{category}/stop")
async def stop_category(category: str) -> dict:
    try:
        await cluster.stop_category(category)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.websocket("/ws/events")
async def events(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = await cluster.subscribe()
    try:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        cluster.unsubscribe(queue)
    finally:
        cluster.unsubscribe(queue)
