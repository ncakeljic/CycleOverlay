"""Overlay-Widgets (benötigen PySide6)."""

from __future__ import annotations

from ..layout import WidgetConfig
from ..values import FrameValues
from .arc_gauge import ArcGaugeWidget
from .bar_gauge import BarGaugeWidget
from .climb_preview import ClimbPreviewWidget
from .base import OverlayWidget
from .elev_profile import ElevationProfileWidget
from .minimap import MinimapWidget
from .route_map import RouteMapWidget
from .text_metric import TextMetricWidget

REGISTRY: dict[str, type[OverlayWidget]] = {
    "text": TextMetricWidget,
    "arc": ArcGaugeWidget,
    "bar": BarGaugeWidget,
    "map": RouteMapWidget,
    "profile": ElevationProfileWidget,
    "minimap": MinimapWidget,
    "climb": ClimbPreviewWidget,
}


def create_widget(cfg: WidgetConfig, values: FrameValues) -> OverlayWidget:
    try:
        return REGISTRY[cfg.type](cfg, values)
    except KeyError as exc:
        raise ValueError(f"Unbekannter Widget-Typ: {cfg.type}") from exc


__all__ = ["REGISTRY", "create_widget", "OverlayWidget", "TextMetricWidget", "ArcGaugeWidget", "BarGaugeWidget",
           "RouteMapWidget", "ElevationProfileWidget", "MinimapWidget", "ClimbPreviewWidget"]
