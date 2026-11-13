"""Geocercas: distancia geodésica (Haversine) y polígono opcional (ray casting)."""
from __future__ import annotations

import math
from datetime import datetime

RADIO_TIERRA_M = 6_371_008.8  # radio medio IUGG


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * RADIO_TIERRA_M * math.asin(math.sqrt(a))


def punto_en_poligono(lat: float, lon: float, poligono: list[list[float]]) -> bool:
    """poligono = [[lat, lon], ...] (al menos 3 vértices)."""
    dentro = False
    n = len(poligono)
    j = n - 1
    for i in range(n):
        yi, xi = poligono[i]
        yj, xj = poligono[j]
        if (yi > lat) != (yj > lat):
            x_cruce = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cruce:
                dentro = not dentro
        j = i
    return dentro


def velocidad_kmh(lat1: float, lon1: float, t1: datetime, lat2: float, lon2: float, t2: datetime) -> float:
    segundos = abs((t2 - t1).total_seconds())
    if segundos < 1:
        segundos = 1
    return haversine_m(lat1, lon1, lat2, lon2) / 1000 / (segundos / 3600)

