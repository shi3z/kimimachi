"""Google Maps Static API: fetch a flat-styled map image and classify its colors.

Needs GOOGLE_MAPS_API_KEY. The style removes labels and paints every feature type
with a unique flat color, so the image can be turned back into classes reliably.
"""
import io
import math
import os

import numpy as np
from PIL import Image

from ..net import fetch
from ..raster import FOREST, GRASS, LOT_COM, LOT_PUB, LOT_RES, NONE, PARK, RAIL, ROAD, WATER

URL = "https://maps.googleapis.com/maps/api/staticmap"
ATTRIBUTION = "Map data ©Google"
SIZE = 640          # logical px per request (API max)
SCALE = 2           # 2x pixels per request
MARGIN = 40         # logical px trimmed from each edge (logo / copyright)

# (style selector, flat color, semantic class)
PALETTE = [
    ("feature:landscape", "0xe0e0e0", NONE),
    ("feature:landscape.man_made", "0xf0c8a0", LOT_RES),
    ("feature:landscape.natural", "0xa0e080", GRASS),
    ("feature:landscape.natural.landcover", "0x207020", FOREST),
    ("feature:poi", "0xf0a0f0", LOT_COM),
    ("feature:poi.government", "0xa0c0f0", LOT_PUB),
    ("feature:poi.school", "0xa0c0f0", LOT_PUB),
    ("feature:poi.medical", "0xa0c0f0", LOT_PUB),
    ("feature:poi.park", "0x60c060", PARK),
    ("feature:poi.sports_complex", "0x60c060", PARK),
    ("feature:road", "0x808080", ROAD),
    ("feature:transit.line", "0x600060", RAIL),
    ("feature:transit.station", "0x600060", RAIL),
    ("feature:water", "0x0040ff", WATER),
]


def _styles():
    s = ["feature:all|element:labels|visibility:off"]
    for sel, color, _ in PALETTE:
        s.append(f"{sel}|element:geometry|color:{color}")
    s.append("feature:road|element:geometry.stroke|visibility:off")
    s.append("feature:administrative|visibility:off")
    return s


def classify(img):
    rgb = np.asarray(img.convert("RGB"), dtype=np.int32)
    cols = np.array([[int(c[2:4], 16), int(c[4:6], 16), int(c[6:8], 16)] for _, c, _ in PALETTE])
    classes = np.array([k for _, _, k in PALETTE], dtype=np.uint8)
    d = ((rgb[:, :, None, :] - cols[None, None, :, :]) ** 2).sum(-1)
    return classes[d.argmin(-1)]


def render(frame, key=None):
    """Returns (source RGB image aligned to frame, class array)."""
    key = key or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        raise SystemExit("Google Maps を使うには環境変数 GOOGLE_MAPS_API_KEY を設定してください")
    # zoom whose (scaled) ground resolution is close to frame.mpp
    z = int(round(math.log2(156543.03392 * math.cos(math.radians(frame.lat)) / (SCALE * frame.mpp))))
    z = max(1, min(21, z))
    units_per_px = 1.0 / (256 * 2**z)             # mercator units per logical px
    inner = SIZE - 2 * MARGIN
    step = inner * units_per_px
    canvas_scale = SCALE / units_per_px            # canvas px per mercator unit
    cw = int(math.ceil((frame.x1 - frame.x0) * canvas_scale))
    ch = int(math.ceil((frame.y1 - frame.y0) * canvas_scale))
    canvas = Image.new("RGB", (cw, ch), (224, 224, 224))
    from ..geo import merc_to_lonlat

    nx = int(math.ceil((frame.x1 - frame.x0) / step))
    ny = int(math.ceil((frame.y1 - frame.y0) / step))
    for j in range(ny):
        for i in range(nx):
            cx = frame.x0 + (i + 0.5) * step
            cy = frame.y0 + (j + 0.5) * step
            lon, lat = merc_to_lonlat(cx, cy)
            params = {"center": f"{lat:.7f},{lon:.7f}", "zoom": z, "size": f"{SIZE}x{SIZE}",
                      "scale": SCALE, "maptype": "roadmap", "style": _styles(), "key": key}
            data = fetch(URL, params=params)
            im = Image.open(io.BytesIO(data)).convert("RGB")
            m = MARGIN * SCALE
            im = im.crop((m, m, im.width - m, im.height - m))
            canvas.paste(im, (int(round(i * step * canvas_scale)), int(round(j * step * canvas_scale))))
    src = canvas.resize((frame.W, frame.H), Image.NEAREST)
    return src, classify(src)
