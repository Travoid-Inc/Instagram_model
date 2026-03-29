"""
Travoid — Instagram Travel Map Generator
=========================================
Create beautiful travel route maps for Instagram.
Add Japan destinations, visualize the route on a stylish map,
and export a polished 1080×1350 image ready to post.
"""

from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go
from geopy.geocoders import Nominatim, Photon
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError
from staticmap import StaticMap, CircleMarker
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import math
import io
import os
import json
import urllib.request
import uuid
from typing import Optional, Tuple


# ─── Page Config ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="Travoid — Travel Map for Instagram",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Marker & Tile Constants ─────────────────────────────────────────
MARKER_BLUE = (59, 130, 246)       # Unified marker colour (RGB)
MARKER_BLUE_HEX = "#3b82f6"        # Same colour as hex

TILE_URLS = {
    "carto-darkmatter": "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
    "carto-positron": "https://a.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
}

# ─── CARTO Style URL → GL style JSON (for English map labels) ────────
_CARTO_STYLE_URLS = {
    "carto-darkmatter": "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
    "carto-positron": "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
}


@st.cache_data(ttl=86400, show_spinner=False)
def _english_carto_style(base: str) -> Optional[dict]:
    """Fetch CARTO GL style JSON and rewrite text-fields to prefer English.

    CARTO vector tiles contain a ``name:en`` property with English names.
    By default the style uses ``name`` (local language).  We replace every
    ``text-field`` expression with a coalesce that prefers English.
    """
    url = _CARTO_STYLE_URLS.get(base)
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "travoid/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            style = json.loads(resp.read())
    except Exception:
        return None

    en_expr = ["coalesce", ["get", "name:en"], ["get", "name_en"], ["get", "name"]]
    for layer in style.get("layers", []):
        layout = layer.get("layout", {})
        if "text-field" in layout:
            layout["text-field"] = en_expr
    return style


# ─── Theme Definitions ───────────────────────────────────────────────
THEMES = {
    "Dark Voyager": {
        "mapbox": "carto-darkmatter",
        "bg": (13, 17, 23),
        "text": (240, 246, 252),
        "sub": (139, 148, 158),
        "line": "rgba(255,255,255,0.30)",
        "accent_a": (255, 107, 107),
        "accent_b": (78, 205, 196),
    },
    "Minimal Light": {
        "mapbox": "carto-positron",
        "bg": (250, 250, 252),
        "text": (33, 37, 41),
        "sub": (108, 117, 125),
        "line": "rgba(33,37,41,0.22)",
        "accent_a": (255, 89, 94),
        "accent_b": (25, 130, 196),
    },
    "Midnight Indigo": {
        "mapbox": "carto-darkmatter",
        "bg": (15, 12, 41),
        "text": (220, 225, 255),
        "sub": (120, 125, 180),
        "line": "rgba(130,140,255,0.30)",
        "accent_a": (129, 140, 248),
        "accent_b": (244, 114, 182),
    },
}

IG_W, IG_H = 1080, 1350  # Instagram portrait format


def _ensure_location_ids(locations: list) -> None:
    for loc in locations:
        if "id" not in loc:
            loc["id"] = uuid.uuid4().hex[:10]


def _sync_route_display_names_from_widgets(locations: list) -> None:
    """Apply Route Details text_input values before sidebar so labels stay in sync."""
    for loc in locations:
        wk = f"route_disp_{loc['id']}"
        if wk not in st.session_state:
            continue
        new = st.session_state[wk]
        if new != loc.get("name", ""):
            loc["name"] = new
            st.session_state.gen_image = None


# ─── Session State ────────────────────────────────────────────────────
if "locations" not in st.session_state:
    st.session_state.locations = []
if "gen_image" not in st.session_state:
    st.session_state.gen_image = None

_ensure_location_ids(st.session_state.locations)
_sync_route_display_names_from_widgets(st.session_state.locations)

# ─── CSS ──────────────────────────────────────────────────────────────
st.markdown(
    """<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;900&display=swap');
.block-container{padding-top:1.5rem}
.hero{
    font-family:'Inter',sans-serif;font-size:2.6rem;font-weight:900;
    letter-spacing:-1px;
    background:linear-gradient(135deg,#FF6B6B,#4ECDC4);
    -webkit-background-clip:text;-webkit-text-fill-color:transparent;
    text-align:center;line-height:1.15;margin-bottom:0
}
.hero-sub{
    text-align:center;color:#8B949E;font-size:1rem;
    margin-top:2px;font-family:'Inter',sans-serif
}
</style>""",
    unsafe_allow_html=True,
)


# ══════════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════


@st.cache_data(ttl=86400, show_spinner=False)
def geocode(name: str) -> Optional[dict]:
    """Geocode a location name → lat / lon.

    Primary: Photon (Komoot) — works reliably from cloud environments.
    Fallback: Nominatim — in case Photon is temporarily unavailable.
    """
    queries = [f"{name}, Japan", name]

    # ── Primary: Photon (no IP-based blocking) ──
    try:
        photon = Photon(
            user_agent="travoid_instagram_map_generator/1.0",
            timeout=15,
        )
        for q in queries:
            try:
                r = photon.geocode(q, language="en")
                if r:
                    return {"lat": r.latitude, "lon": r.longitude, "addr": r.address}
            except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError):
                continue
    except Exception:
        pass

    # ── Fallback: Nominatim ──
    import time as _time
    try:
        nominatim = Nominatim(
            user_agent="travoid_instagram_map_generator/1.0 (contact: travoid.inc@gmail.com)",
            timeout=15,
        )
        for q in queries:
            try:
                _time.sleep(1.1)
                r = nominatim.geocode(q, language="en")
                if r:
                    return {"lat": r.latitude, "lon": r.longitude, "addr": r.address}
            except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError):
                continue
    except Exception:
        pass

    return None


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    """Linear interpolation between two RGB tuples."""
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _zoom(lats: list, lons: list, padding: float = 1.5) -> float:
    """Auto-calculate a Mapbox zoom level that fits all points with padding."""
    if len(lats) <= 1:
        return 11
    dlat = max(max(lats) - min(lats), 0.01) * padding
    dlon = max(max(lons) - min(lons), 0.01) * padding
    return max(3, min(14, min(math.log2(180 / dlat), math.log2(360 / dlon)) - 0.3))


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a clean font — tries system paths, then falls back to default."""
    paths = [
        # macOS
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        # Linux (common)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    # Pillow ≥ 10 supports `size` kwarg on load_default
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _font_sf_ns(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """SF Compact / SF NS for Instagram route list — heavy faces for legibility."""
    paths = [
        "/System/Library/Fonts/SFCompact.ttf",
        "/System/Library/Fonts/SFNSRounded.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return _font(size, bold=bold)


# ─── Coordinate Conversion ────────────────────────────────────────────

def _to_tile(lat: float, lon: float, zoom: float) -> Tuple[float, float]:
    """Convert lat/lon to tile coordinates at the given zoom level."""
    zs = 2 ** zoom
    x = (lon + 180) / 360 * zs
    la_r = math.radians(lat)
    y = (1 - math.log(math.tan(la_r) + 1 / math.cos(la_r)) / math.pi) / 2 * zs
    return x, y


def _latlng_to_px(lat: float, lon: float, clat: float, clon: float,
                  zoom: float, w: int, h: int, tile_size: int = 256) -> Tuple[int, int]:
    """Convert lat/lon → pixel coordinates on a static-map image.

    tile_size=256 for standard Web Mercator tiles (staticmap),
    tile_size=512 for Mapbox GL (plotly interactive).
    """
    wx, wy = _to_tile(lat, lon, zoom)
    cx, cy = _to_tile(clat, clon, zoom)
    return (
        int(round((wx - cx) * tile_size + w / 2)),
        int(round((wy - cy) * tile_size + h / 2)),
    )


def _export_viewport(lats: list, lons: list, n_locs: int,
                     img_w: int = 1080, img_h: int = 1350) -> Tuple[float, float, int]:
    """Compute optimised center & integer zoom for the exported image.

    Accounts for the title overlay at the top and location-list overlay
    at the bottom, so all markers land within the visible map area.
    """
    if len(lats) <= 1:
        return (lats[0] if lats else 36.5), (lons[0] if lons else 138.0), 11

    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    clat = (lat_min + lat_max) / 2
    clon = (lon_min + lon_max) / 2

    # Visible area after gradient overlays (pixels)
    title_px = 180        # title + subtitle + accent line
    # Bottom margin for route list + compact transparent logo strip
    list_px = max(160, 55 + n_locs * 42) + 58
    eff_h = img_h - title_px - list_px        # effective visible height
    eff_w = img_w - 60                         # small horizontal margin

    # Padded bounding box (30 % padding around the points)
    lat_range = max(lat_max - lat_min, 0.005) * 1.3
    lon_range = max(lon_max - lon_min, 0.005) * 1.3

    # Find the highest integer zoom where all points fit
    best_z = 3
    for z in range(14, 2, -1):
        # Pixel span for longitude
        lon_px = lon_range / 360 * 256 * (2 ** z)
        # Pixel span for latitude (Mercator — compute exactly)
        ty_top = _to_tile(clat + lat_range / 2, clon, z)[1]
        ty_bot = _to_tile(clat - lat_range / 2, clon, z)[1]
        lat_px = abs(ty_bot - ty_top) * 256
        if lon_px <= eff_w and lat_px <= eff_h:
            best_z = z
            break

    # Shift centre so markers land in the *visible* area (not image centre)
    vis_centre_y = title_px + eff_h / 2     # pixel centre of visible zone
    img_centre_y = img_h / 2                 # pixel centre of full image
    offset_px = img_centre_y - vis_centre_y  # positive → visible zone is above

    # Convert pixel offset → latitude offset via numerical derivative
    eps = 0.001
    _, py1 = _to_tile(clat, clon, best_z)
    _, py2 = _to_tile(clat + eps, clon, best_z)
    px_per_deg = abs(py2 - py1) * 256 / eps   # pixels per degree latitude
    if px_per_deg > 0:
        clat -= offset_px / px_per_deg         # shift south → content moves up

    return clat, clon, best_z


# ─── Plotly Map Builder ───────────────────────────────────────────────

def _build_fig(locs: list, th: dict) -> go.Figure:
    """Build an interactive Plotly Scattermap preview (blue markers)."""
    fig = go.Figure()
    # Prefer English-labelled CARTO style; fall back to built-in name
    map_style = _english_carto_style(th["mapbox"]) or th["mapbox"]

    if not locs:
        fig.update_layout(
            map=dict(style=map_style, center=dict(lat=36.5, lon=138.0), zoom=4),
            margin=dict(l=0, r=0, t=0, b=0),
            height=520,
        )
        return fig

    lats = [l["lat"] for l in locs]
    lons = [l["lon"] for l in locs]

    # Route line
    if len(locs) > 1:
        fig.add_trace(
            go.Scattermap(
                lat=lats, lon=lons, mode="lines",
                line=dict(width=2.5, color=th["line"]),
                hoverinfo="skip", showlegend=False,
            )
        )

    # Numbered markers — unified blue
    for i, loc in enumerate(locs):
        fig.add_trace(
            go.Scattermap(
                lat=[loc["lat"]], lon=[loc["lon"]],
                mode="markers+text",
                marker=dict(size=24, color=MARKER_BLUE_HEX, opacity=0.93),
                text=[str(i + 1)],
                textfont=dict(size=12, color="white",
                              family="Arial Black, Impact, sans-serif"),
                textposition="middle center",
                hovertext=f"{i + 1}. {loc['name']}",
                hoverinfo="text", showlegend=False,
            )
        )

    clat = sum(lats) / len(lats)
    clon = sum(lons) / len(lons)

    fig.update_layout(
        map=dict(style=map_style, center=dict(lat=clat, lon=clon),
                 zoom=_zoom(lats, lons)),
        margin=dict(l=0, r=0, t=0, b=0),
        height=520,
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# ─── Instagram Image Renderer ────────────────────────────────────────

def _prepare_bottom_logo(max_w: int = 320) -> Optional[Image.Image]:
    """Load TRAVOID PNG unchanged (e.g. white on transparent); trim to alpha bounds; resize."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "travoid_logo.png")
    if not os.path.isfile(path):
        return None
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:
        return None
    arr = np.asarray(im, dtype=np.uint8).copy()
    alpha = arr[..., 3]
    if not np.any(alpha > 12):
        return None
    ys, xs = np.where(alpha > 12)
    pad = 2
    y0 = max(int(ys.min()) - pad, 0)
    y1 = min(int(ys.max()) + pad + 1, im.height)
    x0 = max(int(xs.min()) - pad, 0)
    x1 = min(int(xs.max()) + pad + 1, im.width)
    arr = arr[y0:y1, x0:x1]
    w, h = arr.shape[:2]
    if w > max_w:
        nh = max(1, int(round(h * max_w / w)))
        resized = Image.fromarray(arr, "RGBA").resize((max_w, nh), Image.LANCZOS)
        arr = np.asarray(resized, dtype=np.uint8).copy()
    return Image.fromarray(arr, "RGBA")


def _draw_dashed_line(draw: ImageDraw.Draw, p1: tuple, p2: tuple,
                      color: tuple, width: int = 2, dash: int = 12, gap: int = 8):
    """Draw a dashed line between two points."""
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        return
    ux, uy = dx / length, dy / length
    pos = 0.0
    while pos < length:
        end = min(pos + dash, length)
        draw.line(
            [(x1 + ux * pos, y1 + uy * pos), (x1 + ux * end, y1 + uy * end)],
            fill=color,
            width=width,
        )
        pos = end + gap


def _render(locs: list, title: str, subtitle: str, th: dict) -> Image.Image:
    """Render an Instagram-ready 1080×1350 PNG with map, title & location list."""
    W, H = IG_W, IG_H
    bg = th["bg"]
    n = len(locs)
    is_dark = bg[0] < 128

    lats = [l["lat"] for l in locs]
    lons = [l["lon"] for l in locs]

    # ── 1. Optimised viewport for export ─────────────────────────────
    clat, clon, zoom = _export_viewport(lats, lons, n, W, H)

    # ── 2. Base map ────────────────────────────────────────────────────
    # Primary: Plotly + kaleido with English-labelled CARTO GL style.
    # Fallback: staticmap raster tiles (no-labels variant).
    # Mapbox GL uses 512 px tiles, so zoom - 1 gives the same viewport
    # as staticmap zoom with 256 px tiles.
    map_style = _english_carto_style(th["mapbox"]) or th["mapbox"]
    try:
        export_fig = go.Figure()
        if len(locs) > 1:
            export_fig.add_trace(go.Scattermap(
                lat=lats, lon=lons, mode="lines",
                line=dict(width=3, color=th["line"]),
                hoverinfo="skip", showlegend=False,
            ))
        export_fig.update_layout(
            map=dict(style=map_style,
                     center=dict(lat=clat, lon=clon),
                     zoom=zoom - 1),
            margin=dict(l=0, r=0, t=0, b=0),
            width=W, height=H,
            paper_bgcolor="rgba(0,0,0,0)",
        )
        raw = export_fig.to_image(format="png", width=W, height=H, scale=2)
        base = (
            Image.open(io.BytesIO(raw))
            .convert("RGBA")
            .resize((W, H), Image.LANCZOS)
        )
    except Exception:
        # Fallback: staticmap with no-labels tiles
        tile_url = TILE_URLS.get(th["mapbox"], list(TILE_URLS.values())[0])
        sm = StaticMap(W, H, url_template=tile_url, tile_size=256)
        bg_hex = "#{:02x}{:02x}{:02x}".format(*bg)
        sm.add_marker(CircleMarker((clon, clat), bg_hex, 1))
        try:
            base = sm.render(zoom=zoom, center=[clon, clat]).convert("RGBA")
        except Exception:
            base = Image.new("RGBA", (W, H), bg + (255,))

    canvas = base.copy()

    # ── 3. Gradient overlays (numpy-vectorised for speed) ────────────
    ov = np.zeros((H, W, 4), dtype=np.uint8)

    # Top fade (title area)
    top_h = 320
    ys_t = np.arange(top_h, dtype=np.float64).reshape(-1, 1)
    alpha_t = (245 * np.power(1 - ys_t / top_h, 1.7)).clip(0, 255).astype(np.uint8)
    ov[:top_h, :, 0] = bg[0]
    ov[:top_h, :, 1] = bg[1]
    ov[:top_h, :, 2] = bg[2]
    ov[:top_h, :, 3] = np.broadcast_to(alpha_t, (top_h, W))

    # Bottom fade (location-list area)
    bot_h = max(400, 200 + n * 52)
    bot_h = min(bot_h, H - top_h - 40)
    ys_b = np.arange(bot_h, dtype=np.float64).reshape(-1, 1)
    alpha_b = (250 * np.power(ys_b / bot_h, 1.7)).clip(0, 255).astype(np.uint8)
    y0 = H - bot_h
    ov[y0:, :, 0] = bg[0]
    ov[y0:, :, 1] = bg[1]
    ov[y0:, :, 2] = bg[2]
    ov[y0:, :, 3] = np.broadcast_to(alpha_b, (bot_h, W))

    canvas = Image.alpha_composite(canvas, Image.fromarray(ov, "RGBA"))
    draw = ImageDraw.Draw(canvas)

    # ── 4. Pillow-drawn map markers & dashed route line ──────────────
    marker_r = 18
    ft_map_num = _font(18, bold=True)

    # Pixel positions (tile_size=256 to match staticmap)
    pxs = [_latlng_to_px(loc["lat"], loc["lon"], clat, clon, zoom, W, H, 256)
           for loc in locs]

    # Dashed connecting lines
    line_col = (255, 255, 255, 100) if is_dark else (40, 40, 50, 80)
    for i in range(len(pxs) - 1):
        _draw_dashed_line(draw, pxs[i], pxs[i + 1], line_col, width=2, dash=10, gap=6)

    # Numbered markers only (names appear in the bottom list, not on the map)
    for i, (px, py) in enumerate(pxs):
        border_col = (255, 255, 255, 200) if is_dark else (40, 40, 50, 160)
        draw.ellipse(
            [px - marker_r - 3, py - marker_r - 3,
             px + marker_r + 3, py + marker_r + 3],
            fill=None, outline=border_col, width=2,
        )
        draw.ellipse(
            [px - marker_r, py - marker_r, px + marker_r, py + marker_r],
            fill=MARKER_BLUE,
        )
        ns = str(i + 1)
        nb = draw.textbbox((0, 0), ns, font=ft_map_num)
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]
        draw.text((px - nw // 2, py - nh // 2 - 1), ns,
                  fill=(255, 255, 255), font=ft_map_num)

    # ── 5. Title text ────────────────────────────────────────────────
    ttxt = title.upper()
    ft_sz = 52
    ft_t = _font(ft_sz, bold=True)
    bb = draw.textbbox((0, 0), ttxt, font=ft_t)
    while bb[2] - bb[0] > W - 100 and ft_sz > 30:
        ft_sz -= 2
        ft_t = _font(ft_sz, bold=True)
        bb = draw.textbbox((0, 0), ttxt, font=ft_t)

    tw = bb[2] - bb[0]
    draw.text(((W - tw) // 2, 55), ttxt, fill=th["text"], font=ft_t)

    if subtitle:
        ft_s = _font(22)
        bb2 = draw.textbbox((0, 0), subtitle, font=ft_s)
        draw.text(((W - bb2[2] + bb2[0]) // 2, 55 + ft_sz + 12), subtitle,
                  fill=th["sub"], font=ft_s)

    # ── 6. Accent gradient line ──────────────────────────────────────
    line_y = 55 + ft_sz + (46 if subtitle else 18)
    sa, ea = th["accent_a"], th["accent_b"]
    al = np.zeros((3, W, 4), dtype=np.uint8)
    t_arr = np.linspace(0, 1, W)
    for ch in range(3):
        al[:, :, ch] = (sa[ch] * (1 - t_arr) + ea[ch] * t_arr).astype(np.uint8)
    al[:, :, 3] = 210
    canvas.alpha_composite(Image.fromarray(al, "RGBA"), (0, line_y))
    draw = ImageDraw.Draw(canvas)

    # ── 7. Location list at bottom ───────────────────────────────────
    logo_max = max(220, min(360, int(W * 0.34)))
    logo_im = _prepare_bottom_logo(max_w=logo_max)
    logo_margin_b = 8
    logo_reserve = (logo_im.size[1] + logo_margin_b + 14) if logo_im is not None else 0

    if n <= 8:
        item_h, lf_sz, nf_sz, circ_r = 54, 27, 20, 19
    elif n <= 14:
        item_h, lf_sz, nf_sz, circ_r = 44, 23, 18, 17
    else:
        item_h, lf_sz, nf_sz, circ_r = 38, 20, 16, 15

    ft_l = _font_sf_ns(lf_sz, bold=True)
    ft_n = _font_sf_ns(nf_sz, bold=True)
    list_y = H - 55 - n * item_h - logo_reserve
    list_y = max(list_y, line_y + 48)

    for i, loc in enumerate(locs):
        y = list_y + i * item_h
        cx = 60
        cy = y + item_h // 2 - 2

        draw.ellipse([cx - circ_r, cy - circ_r, cx + circ_r, cy + circ_r],
                     fill=MARKER_BLUE)

        ns = str(i + 1)
        nb = draw.textbbox((0, 0), ns, font=ft_n)
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]
        draw.text((cx - nw // 2, cy - nh // 2 - 1), ns,
                  fill=(255, 255, 255), font=ft_n)

        draw.text((cx + circ_r + 16, y + (item_h - lf_sz) // 2 - 2),
                  loc["name"], fill=th["text"], font=ft_l)

    # ── 8. TRAVOID logo (bottom centre — pixel-accurate from asset) ───────────────
    if logo_im is not None:
        lw, lh = logo_im.size
        lx = (W - lw) // 2
        ly = H - lh - logo_margin_b
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer.paste(logo_im, (lx, ly), logo_im)
        canvas = Image.alpha_composite(canvas, layer)

    # ── 9. Subtle border ─────────────────────────────────────────────
    bdr = tuple(min(c + 30, 255) for c in bg)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, W - 1, H - 1], outline=bdr, width=2)

    return canvas.convert("RGB")


# ══════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("## ✈️ Travoid")
    st.caption("Create stunning travel route maps for Instagram.")
    st.divider()

    # ── Map Settings ──
    map_title = st.text_input("Map title", "My Japan Journey")
    map_sub = st.text_input(
        "Subtitle *(optional)*", value="", placeholder="e.g., Golden Route · 7 Days"
    )
    sel_theme = st.selectbox("Theme", list(THEMES.keys()))
    st.divider()

    # ── Add Location ──
    st.markdown("**Add a stop**")
    q = st.text_input(
        "Location",
        placeholder="Tokyo, Fushimi Inari, Hakone …",
        label_visibility="collapsed",
    )
    add_btn = st.button("➕  Add to route", use_container_width=True, type="primary")

    if add_btn and q.strip():
        with st.spinner(f"Searching *{q.strip()}* …"):
            result = geocode(q.strip())
        if result:
            st.session_state.locations.append(
                {
                    "id": uuid.uuid4().hex[:10],
                    "name": q.strip(),
                    "lat": result["lat"],
                    "lon": result["lon"],
                    "addr": result["addr"],
                }
            )
            st.session_state.gen_image = None
            st.rerun()
        else:
            st.error(f"Could not find **{q}**. Try a more specific name.")

    # ── Location List ──
    if st.session_state.locations:
        st.divider()
        st.markdown(f"**Route** · {len(st.session_state.locations)} stops")

        for i, loc in enumerate(st.session_state.locations):
            cols = st.columns([5, 1, 1, 1])
            cols[0].markdown(f"`{i + 1}` {loc['name']}")

            # Move up
            if i > 0 and cols[1].button("↑", key=f"u{i}"):
                ls = st.session_state.locations
                ls[i], ls[i - 1] = ls[i - 1], ls[i]
                st.session_state.gen_image = None
                st.rerun()

            # Move down
            if i < len(st.session_state.locations) - 1 and cols[2].button("↓", key=f"d{i}"):
                ls = st.session_state.locations
                ls[i], ls[i + 1] = ls[i + 1], ls[i]
                st.session_state.gen_image = None
                st.rerun()

            # Delete
            if cols[3].button("✕", key=f"x{i}"):
                st.session_state.locations.pop(i)
                st.session_state.gen_image = None
                st.rerun()

        if st.button("🗑  Clear all", use_container_width=True):
            st.session_state.locations.clear()
            st.session_state.gen_image = None
            st.rerun()


# ══════════════════════════════════════════════════════════════════════
#  MAIN CONTENT
# ══════════════════════════════════════════════════════════════════════

st.markdown('<p class="hero">Travoid</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="hero-sub">Design stunning travel route maps — ready for Instagram.</p>',
    unsafe_allow_html=True,
)
st.markdown("")

theme = THEMES[sel_theme]

if not st.session_state.locations:
    st.info("👈  Add locations in the sidebar to build your travel route map.")
    st.stop()

# ── Interactive Map Preview ──────────────────────────────────────────
fig_preview = _build_fig(st.session_state.locations, theme)
st.plotly_chart(
    fig_preview,
    use_container_width=True,
    config={"displayModeBar": False},
)

# ── Location Summary ─────────────────────────────────────────────────
with st.expander("📍 Route Details", expanded=False):
    st.caption("Edit display names below. They update the map hover labels, sidebar, and the exported image.")
    for i, loc in enumerate(st.session_state.locations):
        st.markdown(
            f"<small style='color:#8B949E'>**Address** · {loc['addr']}</small>",
            unsafe_allow_html=True,
        )
        wk = f"route_disp_{loc['id']}"
        if wk not in st.session_state:
            st.session_state[wk] = loc["name"]
        st.text_input(
            f"{i + 1}. Display name",
            key=wk,
            help="Shown on the preview map, route list, and Instagram export.",
        )
        if i < len(st.session_state.locations) - 1:
            st.divider()

# ── Generate & Download ─────────────────────────────────────────────
st.divider()
_, col_mid, _ = st.columns([1, 2, 1])

with col_mid:
    gen_btn = st.button(
        "🎨  Generate Instagram Image",
        use_container_width=True,
        type="primary",
    )

if gen_btn:
    with st.spinner("Rendering your travel map …"):
        img = _render(
            st.session_state.locations,
            map_title,
            map_sub or "",
            theme,
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        st.session_state.gen_image = buf.getvalue()

if st.session_state.gen_image:
    st.markdown("---")
    _, col_img, _ = st.columns([1, 3, 1])
    with col_img:
        st.image(
            st.session_state.gen_image,
            caption="Preview — 1080 × 1350 (Instagram Portrait)",
            use_container_width=True,
        )
        safe_title = map_title.lower().replace(" ", "_").replace("/", "-")
        st.download_button(
            "📥  Download for Instagram",
            data=st.session_state.gen_image,
            file_name=f"travoid_{safe_title}.png",
            mime="image/png",
            use_container_width=True,
            type="primary",
        )
