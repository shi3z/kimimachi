"""Geocoding and the local raster frame (Web Mercator, meters)."""
import math
from dataclasses import dataclass

from .net import fetch_json

EARTH_CIRC = 40075016.686
GEOCODE_ATTRIBUTION = "地名検索: Nominatim / © OpenStreetMap contributors"


def geocode(query):
    """Return dict(lat, lon, name, display_name) for a place name."""
    res = fetch_json(
        "https://nominatim.openstreetmap.org/search",
        params={"q": query, "format": "json", "limit": 1, "addressdetails": 1, "accept-language": "ja"},
    )
    if res:
        r = res[0]
        return {
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "name": r.get("name") or query,
            "display_name": r.get("display_name", ""),
        }
    # Fallback: GSI address search (Japan only)
    res = fetch_json("https://msearch.gsi.go.jp/address-search/AddressSearch", params={"q": query})
    if res:
        lon, lat = res[0]["geometry"]["coordinates"]
        title = res[0]["properties"]["title"]
        return {"lat": lat, "lon": lon, "name": title, "display_name": title}
    raise SystemExit(f"場所が見つかりませんでした: {query}")


def lonlat_to_merc(lon, lat):
    """Web Mercator normalized coordinates in [0,1] (y down)."""
    x = (lon + 180.0) / 360.0
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0
    return x, y


def merc_to_lonlat(x, y):
    lon = x * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))
    return lon, lat


@dataclass
class Frame:
    """A rectangular area around (lat, lon) rasterized at `mpp` meters per pixel."""

    lat: float
    lon: float
    width_m: float
    height_m: float
    mpp: float

    def __post_init__(self):
        self.cx, self.cy = lonlat_to_merc(self.lon, self.lat)
        # ground meters per normalized mercator unit at this latitude
        self.m_per_unit = EARTH_CIRC * math.cos(math.radians(self.lat))
        self.W = int(round(self.width_m / self.mpp))
        self.H = int(round(self.height_m / self.mpp))
        self.x0 = self.cx - self.width_m / 2 / self.m_per_unit
        self.y0 = self.cy - self.height_m / 2 / self.m_per_unit
        self.x1 = self.cx + self.width_m / 2 / self.m_per_unit
        self.y1 = self.cy + self.height_m / 2 / self.m_per_unit

    @property
    def px_per_unit(self):
        return self.m_per_unit / self.mpp

    def merc_to_px(self, x, y):
        s = self.px_per_unit
        return (x - self.x0) * s, (y - self.y0) * s

    def lonlat_to_px(self, lon, lat):
        return self.merc_to_px(*lonlat_to_merc(lon, lat))

    def tiles(self, z):
        """Slippy-map tile indices covering the frame at zoom z."""
        n = 2**z
        tx0, tx1 = int(self.x0 * n), int(self.x1 * n)
        ty0, ty1 = int(self.y0 * n), int(self.y1 * n)
        return [(z, tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]

    def bounds_lonlat(self):
        lon0, lat1 = merc_to_lonlat(self.x0, self.y0)
        lon1, lat0 = merc_to_lonlat(self.x1, self.y1)
        return lon0, lat0, lon1, lat1
