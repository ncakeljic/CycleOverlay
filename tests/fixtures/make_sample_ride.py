"""Erzeugt die synthetische Testfahrt ``sample_ride.gpx`` mit bekannten Sollwerten.

Aufruf: ``python tests/fixtures/make_sample_ride.py``

Ablauf (Start 14.06.2026 08:00:00 UTC = 10:00 Uhr in Berlin, Sommerzeit):

| Track-Zeit  | Abschnitt                         | Geschw.  | Steigung | Leistung |
|-------------|-----------------------------------|----------|----------|----------|
|   0 – 300 s | Ebene                             | 30 km/h  |  0 %     | 200 W    |
| 300 – 600 s | Anstieg (350–550 s Smart Recording)| 18 km/h |  5 %     | 300 W    |
| 600 – 660 s | Stillstand oben, GPS-Drift        |  0       |  –       |   0 W    |
| 660 – 720 s | Auto-Pause (keine Punkte)         |  –       |  –       |   –      |
| 720 –1020 s | Abfahrt                           | 45 km/h  | −6 %     |   0 W    |
|1020 –1200 s | Ebene                             | 25 km/h  |  0 %     | 180 W    |

Sollwerte: Distanz 9000 m, Höhenmeter 75 m, Start-/Gipfel-/Endhöhe 300/375/150 m.
Herzfrequenz fehlt bei 100–104 s (kurzer Dropout) und 150–169 s (langer Dropout).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

R = 6_371_000.0
LAT0, LON0 = 48.0, 11.0
START = datetime(2026, 6, 14, 8, 0, 0, tzinfo=timezone.utc)

# (von, bis, Geschwindigkeit m/s, Steigung, Leistung, Trittfrequenz, Herzfrequenz)
PHASES = [
    (0, 300, 30 / 3.6, 0.0, 200, 90, 135),
    (300, 600, 18 / 3.6, 0.05, 300, 75, 160),
    (600, 660, 0.0, 0.0, 0, 0, 140),
    (660, 720, 0.0, 0.0, 0, 0, 130),  # Pause – keine Punkte
    (720, 1020, 45 / 3.6, -0.06, 0, 0, 125),
    (1020, 1200, 25 / 3.6, 0.0, 180, 88, 130),
]


def state(t: float):
    """Distanz (m), Höhe (m) und Phase zur Zeit t."""
    s = 0.0
    z = 300.0
    for ph in PHASES:
        a, b, v, g = ph[:4]
        dt = min(max(t - a, 0.0), b - a)
        s += v * dt
        z += v * dt * g
        if t <= b:
            return s, z, ph
    return s, z, PHASES[-1]


def point_times(rng: np.random.Generator) -> list[int]:
    times = list(range(0, 351))
    t = 350
    while True:
        t += int(rng.choice([1, 2, 3, 4]))
        if t >= 550:
            break
        times.append(t)
    times += list(range(550, 661))
    times += list(range(720, 1201))
    return times


def build(path: Path) -> None:
    rng = np.random.default_rng(42)
    drift = np.zeros(2)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="CycleOverlay Testdaten" '
        'xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">',
        "  <metadata><name>Testfahrt</name></metadata>",
        "  <trk>",
        "    <name>Synthetische Testfahrt</name>",
        "    <trkseg>",
    ]
    for t in point_times(rng):
        if t == 720:  # nach der Pause neues Segment (wie bei manchen Garmin-Exporten)
            lines += ["    </trkseg>", "    <trkseg>"]
        s, z, ph = state(t)
        north, east = s, 0.0
        if 600 <= t <= 660:  # GPS-Drift im Stand (Random Walk, ~0,2 m/s)
            drift += rng.normal(0, 0.15, 2)
            north += drift[0]
            east += drift[1]
        lat = LAT0 + math.degrees(north / R)
        lon = LON0 + math.degrees(east / (R * math.cos(math.radians(lat))))
        ele = z + rng.normal(0, 0.5)
        _, _, _, _, power, cad, hr = ph
        power = max(0, int(power + (rng.normal(0, 10) if power else 0)))
        hr_val = int(hr + 3 * math.sin(t / 30))
        temp = 18.0 + 2.0 * t / 1200
        time = (START + timedelta(seconds=t)).strftime("%Y-%m-%dT%H:%M:%SZ")
        tpx = [f"<ns3:atemp>{temp:.1f}</ns3:atemp>"]
        if not (100 <= t <= 104 or 150 <= t <= 169):
            tpx.append(f"<ns3:hr>{hr_val}</ns3:hr>")
        tpx.append(f"<ns3:cad>{cad}</ns3:cad>")
        lines.append(
            f'      <trkpt lat="{lat:.8f}" lon="{lon:.8f}"><ele>{ele:.1f}</ele><time>{time}</time>'
            f"<extensions><power>{power}</power><ns3:TrackPointExtension>{''.join(tpx)}"
            "</ns3:TrackPointExtension></extensions></trkpt>"
        )
    lines += ["    </trkseg>", "  </trk>", "</gpx>", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    out = Path(__file__).with_name("sample_ride.gpx")
    build(out)
    print(f"geschrieben: {out}")
