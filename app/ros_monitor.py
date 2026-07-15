from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from .config import LauncherConfig, ModuleConfig


@dataclass(frozen=True)
class TopicSpec:
    module_id: str
    topic: str
    domain_id: int
    module: ModuleConfig


class RosTopicMonitor:
    """Persistent rclpy-based topic frequency monitor.

    The launcher must be able to run even when ROS 2 is not sourced. For that
    reason rclpy imports are intentionally lazy and all failures are reported as
    monitor status instead of crashing FastAPI.
    """

    def __init__(self, config: LauncherConfig, window_sec: float = 5.0) -> None:
        self.config = config
        self.window_sec = window_sec
        self.enabled = True
        self.started = False
        self.error: str | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._contexts: list[Any] = []
        self._samples: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._topic_status: dict[tuple[str, str], dict] = {}

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.enabled = True
        self.error = None
        self._stop_event.clear()

        try:
            import rclpy  # noqa: F401
            from rosidl_runtime_py.utilities import get_message  # noqa: F401
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return

        for module_id, module in self.config.modules.items():
            if not module.monitor_topics or module.domain_id is None:
                continue
            for topic in module.monitor_topics:
                spec = TopicSpec(
                    module_id=module_id,
                    topic=topic,
                    domain_id=int(module.domain_id),
                    module=module,
                )
                thread = threading.Thread(
                    target=self._spin_topic,
                    args=(spec,),
                    name=f"ros-topic-monitor-{module_id}-{self._safe_name(topic)}",
                    daemon=True,
                )
                self._threads.append(thread)
                thread.start()

    @staticmethod
    def _safe_name(value: str) -> str:
        return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_") or "topic"

    def stop(self) -> None:
        self.enabled = False
        self.started = False
        self._stop_event.set()
        for context in list(self._contexts):
            try:
                context.shutdown()
            except Exception:
                pass
        for thread in list(self._threads):
            thread.join(timeout=1.0)
        self._threads.clear()
        self._contexts.clear()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            if not self.started:
                self.start()
            self.enabled = True
            self._stop_event.clear()
        else:
            self.stop()

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "started": self.started,
            "error": self.error,
            "window_sec": self.window_sec,
        }

    def rates(self) -> list[dict]:
        specs = [
            TopicSpec(module_id=module_id, topic=topic, domain_id=int(module.domain_id), module=module)
            for module_id, module in self.config.modules.items()
            if module.monitor_topics and module.domain_id is not None
            for topic in module.monitor_topics
        ]

        if not self.enabled:
            return [self._rate_payload(spec, None, "disabled", "monitor disabled") for spec in specs]
        if self.error:
            return [self._rate_payload(spec, None, "error", self.error) for spec in specs]

        now = time.monotonic()
        results = []
        with self._lock:
            for spec in specs:
                key = (spec.module_id, spec.topic)
                samples = self._samples[key]
                self._trim_samples(samples, now)
                hz = self._compute_hz(samples)
                status = self._topic_status.get(key, {})
                if hz is None:
                    results.append(
                        self._rate_payload(
                            spec,
                            None,
                            status.get("status", "no_data"),
                            status.get("message", "waiting for data"),
                        )
                    )
                else:
                    results.append(self._rate_payload(spec, hz, "ok", "ok"))
        return results

    def _spin_topic(self, spec: TopicSpec) -> None:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import qos_profile_sensor_data
        from rclpy.signals import SignalHandlerOptions
        from rosidl_runtime_py.utilities import get_message

        context = rclpy.context.Context()
        self._contexts.append(context)
        executor = None
        node = None
        try:
            rclpy.init(
                context=context,
                domain_id=spec.domain_id,
                signal_handler_options=SignalHandlerOptions.NO,
            )
            node = rclpy.create_node(
                f"robot_web_launcher_monitor_{spec.module_id}_{self._safe_name(spec.topic)}",
                context=context,
            )
            executor = SingleThreadedExecutor(context=context)
            executor.add_node(node)
            subscribed = False
            key = (spec.module_id, spec.topic)

            while not self._stop_event.is_set() and context.ok():
                if not subscribed:
                    topic_types = {name: types for name, types in node.get_topic_names_and_types()}
                    types = topic_types.get(spec.topic)
                    if not types:
                        with self._lock:
                            self._topic_status[key] = {"status": "no_topic", "message": "topic not found"}
                        continue
                    try:
                        msg_type = get_message(types[0])
                        node.create_subscription(
                            msg_type,
                            spec.topic,
                            self._make_callback(spec.module_id, spec.topic),
                            qos_profile_sensor_data,
                        )
                        subscribed = True
                        with self._lock:
                            self._topic_status[key] = {"status": "waiting", "message": f"subscribed {types[0]}"}
                    except Exception as exc:
                        with self._lock:
                            self._topic_status[key] = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}

                executor.spin_once(timeout_sec=0.2)
                self._mark_stale_sample(key)
        except Exception as exc:
            self.error = f"{spec.topic}: {type(exc).__name__}: {exc}"
        finally:
            if executor is not None and node is not None:
                try:
                    executor.remove_node(node)
                except Exception:
                    pass
            if executor is not None:
                try:
                    executor.shutdown()
                except Exception:
                    pass
            try:
                if node is not None:
                    node.destroy_node()
            except Exception:
                pass
            try:
                context.shutdown()
            except Exception:
                pass

    def _make_callback(self, module_id: str, topic: str):
        key = (module_id, topic)

        def callback(_msg: Any) -> None:
            now = time.monotonic()
            with self._lock:
                samples = self._samples[key]
                samples.append(now)
                self._trim_samples(samples, now)
                self._topic_status[key] = {"status": "ok", "message": "receiving"}

        return callback

    def _mark_stale_sample(self, key: tuple[str, str]) -> None:
        now = time.monotonic()
        with self._lock:
            samples = self._samples[key]
            self._trim_samples(samples, now)
            if not samples:
                self._topic_status[key] = {"status": "timeout", "message": "no recent data"}

    def _trim_samples(self, samples: deque[float], now: float) -> None:
        while samples and now - samples[0] > self.window_sec:
            samples.popleft()

    @staticmethod
    def _compute_hz(samples: deque[float]) -> float | None:
        if len(samples) < 2:
            return None
        duration = samples[-1] - samples[0]
        if duration <= 0:
            return None
        return (len(samples) - 1) / duration

    @staticmethod
    def _rate_payload(spec: TopicSpec, hz: float | None, status: str, message: str) -> dict:
        return {
            "module": spec.module_id,
            "topic": spec.topic,
            "domain_id": spec.domain_id,
            "hz": hz,
            "status": status,
            "message": message,
        }
