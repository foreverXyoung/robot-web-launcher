from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ModuleConfig:
    id: str
    name: str
    category: str
    workdir: Path
    cmd: list[str]
    env: dict[str, str] = field(default_factory=dict)
    domain_id: int | None = None
    setup: str | None = None
    conda_env: str | None = None
    conda_sh: str | None = None
    autostart: bool = False
    restart_on_crash: bool = False
    depends_on: list[str] = field(default_factory=list)
    startup_delay: float = 0.0
    health_nodes: list[str] = field(default_factory=list)
    health_topics: list[str] = field(default_factory=list)
    monitor_topics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LauncherConfig:
    host: str
    port: int
    log_dir: Path
    state_dir: Path
    stop_timeout_sec: float
    modules: dict[str, ModuleConfig]


def _as_str_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"{key} must be a list of strings")
    return value


def load_config(path: str | Path) -> LauncherConfig:
    config_path = Path(path).expanduser().resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    base_dir = config_path.parent.parent
    log_dir = Path(raw.get("log_dir", base_dir / "runtime" / "logs")).expanduser()
    state_dir = Path(raw.get("state_dir", base_dir / "runtime" / "state")).expanduser()
    if not log_dir.is_absolute():
        log_dir = base_dir / log_dir
    if not state_dir.is_absolute():
        state_dir = base_dir / state_dir

    modules: dict[str, ModuleConfig] = {}
    for module_id, item in (raw.get("modules") or {}).items():
        if not isinstance(item, dict):
            raise ValueError(f"module {module_id} must be a mapping")
        cmd = item.get("cmd")
        if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
            raise ValueError(f"module {module_id}.cmd must be a list of strings")
        modules[module_id] = ModuleConfig(
            id=module_id,
            name=str(item.get("name", module_id)),
            category=str(item.get("category", "algorithm")),
            domain_id=item.get("domain_id"),
            workdir=Path(item["workdir"]).expanduser(),
            env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
            setup=item.get("setup"),
            conda_env=item.get("conda_env"),
            conda_sh=item.get("conda_sh"),
            cmd=cmd,
            autostart=bool(item.get("autostart", False)),
            restart_on_crash=bool(item.get("restart_on_crash", False)),
            depends_on=_as_str_list(item.get("depends_on"), f"{module_id}.depends_on"),
            startup_delay=float(item.get("startup_delay", 0.0)),
            health_nodes=_as_str_list(item.get("health_nodes"), f"{module_id}.health_nodes"),
            health_topics=_as_str_list(item.get("health_topics"), f"{module_id}.health_topics"),
            monitor_topics=_as_str_list(item.get("monitor_topics"), f"{module_id}.monitor_topics"),
        )

    for module in modules.values():
        for dep in module.depends_on:
            if dep not in modules:
                raise ValueError(f"module {module.id} depends on unknown module {dep}")

    return LauncherConfig(
        host=str(raw.get("host", "0.0.0.0")),
        port=int(raw.get("port", 8080)),
        log_dir=log_dir,
        state_dir=state_dir,
        stop_timeout_sec=float(raw.get("stop_timeout_sec", 8.0)),
        modules=modules,
    )
