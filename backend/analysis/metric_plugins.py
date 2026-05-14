from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from backend.analysis.metrics import MetricResult
from backend.analysis.transcripts import Transcript


MetricCalculator = Callable[[Transcript], MetricResult]


@dataclass(frozen=True)
class MetricPlugin:
    id: str
    label: str
    description: str
    category: str
    output_schema: dict[str, str]
    calculate: MetricCalculator


_REGISTRY: dict[str, MetricPlugin] = {}


def register_metric_plugin(plugin: MetricPlugin, *, replace: bool = False) -> None:
    if not plugin.id:
        raise ValueError("Metric plugin id must not be empty")
    if plugin.id in _REGISTRY and not replace:
        raise ValueError(f"Metric plugin already registered: {plugin.id}")
    _REGISTRY[plugin.id] = plugin


def get_metric_plugin(metric_id: str) -> MetricPlugin:
    try:
        return _REGISTRY[metric_id]
    except KeyError as exc:
        raise ValueError(f"Unknown metric skill: {metric_id}") from exc


def registered_metric_ids() -> set[str]:
    return set(_REGISTRY)


def metric_plugin_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": plugin.id,
            "label": plugin.label,
            "description": plugin.description,
            "category": plugin.category,
            "output_schema": dict(plugin.output_schema),
        }
        for plugin in _REGISTRY.values()
    ]


def metric_calculators() -> dict[str, MetricCalculator]:
    return {
        metric_id: plugin.calculate for metric_id, plugin in _REGISTRY.items()
    }
