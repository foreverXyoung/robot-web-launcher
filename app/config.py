from __future__ import annotations

import re
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
    python_script: str | None = None
    autostart: bool = False
    restart_on_crash: bool = False
    depends_on: list[str] = field(default_factory=list)
    startup_delay: float = 0.0
    health_nodes: list[str] = field(default_factory=list)
    health_topics: list[str] = field(default_factory=list)
    monitor_topics: list[str] = field(default_factory=list)
    process_patterns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RecorderModeConfig:
    id: str
    name: str
    topics: list[str]


@dataclass(frozen=True)
class RecorderConfig:
    enabled: bool
    output_dir: Path
    default_domain_id: int
    min_free_gb: float
    stop_timeout_sec: float
    modes: dict[str, RecorderModeConfig]


@dataclass(frozen=True)
class ClusterHostConfig:
    id: str
    name: str
    kind: str
    base_url: str | None = None


@dataclass(frozen=True)
class ClusterConfig:
    enabled: bool
    self_host: str
    hosts: dict[str, ClusterHostConfig]


@dataclass(frozen=True)
class LauncherConfig:
    host: str
    port: int
    paths: dict[str, str]
    ros_setup: Path
    monitor_setups: list[Path]
    log_dir: Path
    state_dir: Path
    stop_timeout_sec: float
    recorder: RecorderConfig
    cluster: ClusterConfig
    modules: dict[str, ModuleConfig]


def _local_override_path(config_path: Path) -> Path:
    return config_path.with_name(f"{config_path.stem}.local{config_path.suffix}")


def _deep_merge_config(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge_config(merged.get(key), value)
        return merged
    return override


def _load_yaml_file(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def _as_str_list(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"{key} must be a list of strings")
    return value


_VARIABLE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_config_vars(value: str, variables: dict[str, str]) -> str:
    return _VARIABLE_PATTERN.sub(
        lambda match: variables.get(match.group(1), match.group(0)),
        value,
    )


def _load_path_variables(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("paths must be a mapping of string values")
    variables = {str(key): str(value) for key, value in raw.items()}
    for _ in range(max(1, len(variables) + 1)):
        expanded = {key: _expand_config_vars(value, variables) for key, value in variables.items()}
        if expanded == variables:
            break
        variables = expanded
    unresolved = {
        name
        for value in variables.values()
        for name in _VARIABLE_PATTERN.findall(value)
        if name in variables
    }
    if unresolved:
        raise ValueError(f"cyclic path variables: {', '.join(sorted(unresolved))}")
    return variables


def _load_recorder_config(raw: dict, base_dir: Path, path_variables: dict[str, str]) -> RecorderConfig:
    item = raw.get("recorder") or {}
    if not isinstance(item, dict):
        raise ValueError("recorder must be a mapping")

    output_dir = Path(
        _expand_config_vars(str(item.get("output_dir", base_dir / "runtime" / "bags")), path_variables)
    ).expanduser()
    if not output_dir.is_absolute():
        output_dir = base_dir / output_dir

    modes: dict[str, RecorderModeConfig] = {}
    for mode_id, mode_item in (item.get("modes") or {}).items():
        if not isinstance(mode_item, dict):
            raise ValueError(f"recorder.modes.{mode_id} must be a mapping")
        topics = _as_str_list(mode_item.get("topics"), f"recorder.modes.{mode_id}.topics")
        modes[str(mode_id)] = RecorderModeConfig(
            id=str(mode_id),
            name=str(mode_item.get("name", mode_id)),
            topics=topics,
        )

    return RecorderConfig(
        enabled=bool(item.get("enabled", True)),
        output_dir=output_dir,
        default_domain_id=int(item.get("default_domain_id", 20)),
        min_free_gb=float(item.get("min_free_gb", 10.0)),
        stop_timeout_sec=float(item.get("stop_timeout_sec", 12.0)),
        modes=modes,
    )


def _load_cluster_config(raw: dict) -> ClusterConfig:
    item = raw.get("cluster") or {}
    if not isinstance(item, dict):
        raise ValueError("cluster must be a mapping")

    hosts: dict[str, ClusterHostConfig] = {}
    for host_id, host_item in (item.get("hosts") or {}).items():
        if not isinstance(host_item, dict):
            raise ValueError(f"cluster.hosts.{host_id} must be a mapping")
        hosts[str(host_id)] = ClusterHostConfig(
            id=str(host_id),
            name=str(host_item.get("name", host_id)),
            kind=str(host_item.get("kind", "remote")),
            base_url=str(host_item["base_url"]).rstrip("/") if host_item.get("base_url") else None,
        )

    self_host = str(item.get("self", "local"))
    if hosts and self_host not in hosts:
        raise ValueError(f"cluster.self references unknown host: {self_host}")

    return ClusterConfig(
        enabled=bool(item.get("enabled", False)),
        self_host=self_host,
        hosts=hosts,
    )


def load_config(path: str | Path) -> LauncherConfig:
    config_path = Path(path).expanduser().resolve()
    raw = _load_yaml_file(config_path)
    if ".local" not in config_path.stem:
        local_path = _local_override_path(config_path)
        if local_path.exists():
            raw = _deep_merge_config(raw, _load_yaml_file(local_path))

    base_dir = config_path.parent.parent
    path_variables = _load_path_variables(raw.get("paths"))
    log_dir = Path(
        _expand_config_vars(str(raw.get("log_dir", base_dir / "runtime" / "logs")), path_variables)
    ).expanduser()
    state_dir = Path(
        _expand_config_vars(str(raw.get("state_dir", base_dir / "runtime" / "state")), path_variables)
    ).expanduser()
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
        python_script = item.get("python_script")
        if python_script is not None and not isinstance(python_script, str):
            raise ValueError(f"module {module_id}.python_script must be a string")
        effective_cmd = [_expand_config_vars(value, path_variables) for value in cmd]
        if python_script:
            python_script = _expand_config_vars(python_script, path_variables)
            effective_cmd.append(python_script)

        setup = item.get("setup")
        if setup is not None:
            setup = _expand_config_vars(str(setup), path_variables)
        conda_sh = item.get("conda_sh")
        if conda_sh is not None:
            conda_sh = _expand_config_vars(str(conda_sh), path_variables)

        modules[module_id] = ModuleConfig(
            id=module_id,
            name=str(item.get("name", module_id)),
            category=str(item.get("category", "algorithm")),
            domain_id=item.get("domain_id"),
            workdir=Path(_expand_config_vars(str(item["workdir"]), path_variables)).expanduser(),
            env={
                str(k): _expand_config_vars(str(v), path_variables)
                for k, v in (item.get("env") or {}).items()
            },
            setup=setup,
            conda_env=item.get("conda_env"),
            conda_sh=conda_sh,
            python_script=python_script,
            cmd=effective_cmd,
            autostart=bool(item.get("autostart", False)),
            restart_on_crash=bool(item.get("restart_on_crash", False)),
            depends_on=_as_str_list(item.get("depends_on"), f"{module_id}.depends_on"),
            startup_delay=float(item.get("startup_delay", 0.0)),
            health_nodes=_as_str_list(item.get("health_nodes"), f"{module_id}.health_nodes"),
            health_topics=_as_str_list(item.get("health_topics"), f"{module_id}.health_topics"),
            monitor_topics=_as_str_list(item.get("monitor_topics"), f"{module_id}.monitor_topics"),
            process_patterns=_as_str_list(item.get("process_patterns"), f"{module_id}.process_patterns"),
        )

    for module in modules.values():
        for dep in module.depends_on:
            if dep not in modules:
                raise ValueError(f"module {module.id} depends on unknown module {dep}")

    ros_setup_value = raw.get("ros_setup", path_variables.get("ros_setup", "/opt/ros/humble/setup.bash"))
    monitor_setup_values = _as_str_list(raw.get("monitor_setups"), "monitor_setups")

    return LauncherConfig(
        host=str(raw.get("host", "0.0.0.0")),
        port=int(raw.get("port", 8080)),
        paths=path_variables,
        ros_setup=Path(_expand_config_vars(str(ros_setup_value), path_variables)).expanduser(),
        monitor_setups=[
            Path(_expand_config_vars(value, path_variables)).expanduser()
            for value in monitor_setup_values
        ],
        log_dir=log_dir,
        state_dir=state_dir,
        stop_timeout_sec=float(raw.get("stop_timeout_sec", 8.0)),
        recorder=_load_recorder_config(raw, base_dir, path_variables),
        cluster=_load_cluster_config(raw),
        modules=modules,
    )
