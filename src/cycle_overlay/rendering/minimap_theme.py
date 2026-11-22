"""Farbthemen der Minimap (ohne Qt). Farben als #RRGGBB[AA]."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoadStyle:
    color: str
    width: float  # relativ: 1.0 = Nebenstraße
    casing: str | None = None  # Randfarbe (helles Thema)


@dataclass(frozen=True)
class Theme:
    key: str
    title: str
    background: str
    border: str
    water: str
    green: str
    roads: dict[str, RoadStyle]
    route_ahead: str
    route_ridden: str
    arrow: str
    arrow_outline: str
    text: str
    text_shadow: str


THEMES: dict[str, Theme] = {
    "dark": Theme(
        key="dark", title="Dunkel (wie GTA)",
        background="#141920E6", border="#FFFFFF59",
        water="#1E3A5CFF", green="#1B2B22FF",
        roads={
            "major": RoadStyle("#6B7482", 2.0), "primary": RoadStyle("#565E6B", 1.5),
            "minor": RoadStyle("#3F4652", 1.0), "cycle": RoadStyle("#3B5E4A", 0.8),
            "path": RoadStyle("#353B44", 0.6), "waterline": RoadStyle("#1E3A5C", 0.9),
        },
        route_ahead="#B36BFF", route_ridden="#FFFFFF59",
        arrow="#FFFFFF", arrow_outline="#000000B3", text="#FFFFFF", text_shadow="#000000A0",
    ),
    "light": Theme(
        key="light", title="Hell (wie ein Navi)",
        background="#EEF0F2F0", border="#00000040",
        water="#AAD3DFFF", green="#D3E8C8FF",
        roads={
            "major": RoadStyle("#F6C76B", 2.0, "#D9A441"), "primary": RoadStyle("#FFFFFF", 1.6, "#C8CDD4"),
            "minor": RoadStyle("#FFFFFF", 1.1, "#D3D7DD"), "cycle": RoadStyle("#BFDCC6", 0.8),
            "path": RoadStyle("#D6D0C4", 0.6), "waterline": RoadStyle("#AAD3DF", 0.9),
        },
        route_ahead="#1A73E8", route_ridden="#1A73E866",
        arrow="#1A73E8", arrow_outline="#FFFFFFFF", text="#1F2933", text_shadow="#FFFFFF00",
    ),
}
