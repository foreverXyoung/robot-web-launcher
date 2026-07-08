from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import load_config
from .manager import ProcessManager


BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.environ.get("ROBOT_LAUNCHER_CONFIG", BASE_DIR / "config" / "modules.yaml"))

config = load_config(CONFIG_PATH)
manager = ProcessManager(config)
app = FastAPI(title="Robot Web Launcher")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class ModuleSelection(BaseModel):
    modules: list[str]


class MonitorToggle(BaseModel):
    enabled: bool


@app.on_event("startup")
async def on_startup() -> None:
    manager.set_monitor_enabled(True)
    if os.environ.get("ROBOT_LAUNCHER_AUTOSTART", "0") == "1":
        await manager.autostart()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    manager.set_monitor_enabled(False)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/modules")
async def list_modules() -> list[dict]:
    return manager.list_modules()


@app.get("/api/categories")
async def list_categories() -> list[dict]:
    return manager.list_categories()


@app.get("/api/sensor-rates")
async def sensor_rates() -> list[dict]:
    try:
        return await manager.measure_sensor_rates()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/monitor")
async def monitor_status() -> dict:
    return manager.monitor_status()


@app.post("/api/monitor")
async def set_monitor(toggle: MonitorToggle) -> dict:
    return manager.set_monitor_enabled(toggle.enabled)


@app.post("/api/modules/{module_id}/start")
async def start_module(module_id: str) -> dict:
    try:
        return await manager.start(module_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/modules/{module_id}/stop")
async def stop_module(module_id: str) -> dict:
    try:
        return await manager.stop(module_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/modules/{module_id}/restart")
async def restart_module(module_id: str) -> dict:
    try:
        return await manager.restart(module_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/start-selected")
async def start_selected(selection: ModuleSelection) -> dict:
    try:
        await manager.start_many(selection.modules)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/stop-selected")
async def stop_selected(selection: ModuleSelection) -> dict:
    try:
        await manager.stop_many(selection.modules)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/categories/{category}/start")
async def start_category(category: str) -> dict:
    try:
        await manager.start_category(category)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/categories/{category}/stop")
async def stop_category(category: str) -> dict:
    try:
        await manager.stop_category(category)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.websocket("/ws/events")
async def events(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = await manager.subscribe()
    try:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        manager.unsubscribe(queue)
    finally:
        manager.unsubscribe(queue)
