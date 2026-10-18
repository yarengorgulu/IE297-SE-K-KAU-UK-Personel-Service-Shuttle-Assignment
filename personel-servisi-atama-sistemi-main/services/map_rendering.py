"""Harita görünümü için güvenli Leaflet HTML üretimi."""

from __future__ import annotations

import html
import json
import os

from services.map_management import CoordinateTargetView, RouteMapData


def render_map_html(
    *,
    personnel: tuple[CoordinateTargetView, ...] = (),
    route: RouteMapData | None = None,
) -> str:
    markers: list[dict[str, object]] = []
    for person in personnel:
        if person.latitude is None or person.longitude is None:
            continue
        markers.append(
            {
                "lat": person.latitude,
                "lon": person.longitude,
                "label": html.escape(person.label),
                "kind": "personnel",
                "position": "Personel",
            }
        )
    if route is not None:
        active_points = [point for point in route.points if point.active]
        for point in active_points:
            if point.latitude is None or point.longitude is None:
                continue
            kind = "middle"
            if point.position_label.startswith("Başlangıç"):
                kind = "start"
            if point.position_label.startswith("Bitiş"):
                kind = "end"
            markers.append(
                {
                    "lat": point.latitude,
                    "lon": point.longitude,
                    "label": html.escape(
                        f"{point.sequence}. {point.label}"
                        + (f" / {point.district}" if point.district else "")
                        + (f" / {point.neighborhood}" if point.neighborhood else "")
                    ),
                    "kind": kind,
                    "position": point.position_label,
                }
            )
    marker_json = json.dumps(markers, ensure_ascii=False)
    geometry_json = json.dumps(
        route.geometry if route and route.geometry else None,
        ensure_ascii=False,
    )
    route_current = bool(route and route.route_is_current)
    tile_url = json.dumps(
        os.getenv(
            "SERVIS_ATAMA_TILE_URL",
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        )
    )
    return f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    html, body, #map {{ height: 100%; margin: 0; font-family: sans-serif; }}
    #fallback {{ display:none; padding:20px; color:#8a3b12; }}
    .legend {{ background:white; padding:7px 9px; border-radius:4px; box-shadow:0 1px 5px #888; }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="fallback">Harita yüklenemedi. İnternet bağlantısını kontrol edin.</div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const markers = {marker_json};
    const geometry = {geometry_json};
    const routeCurrent = {str(route_current).lower()};
    if (typeof L === 'undefined') {{
      document.getElementById('map').style.display = 'none';
      document.getElementById('fallback').style.display = 'block';
    }} else {{
      const map = L.map('map').setView([36.8, 34.63], 10);
      L.tileLayer({tile_url}, {{
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap katkıda bulunanlar</a>'
      }}).addTo(map);
      const colors = {{personnel:'#2478d4', start:'#18864b', middle:'#e79018', end:'#c93636'}};
      const bounds = [];
      markers.forEach(item => {{
        const point = [item.lat, item.lon];
        bounds.push(point);
        L.circleMarker(point, {{radius:9, color:colors[item.kind], fillOpacity:0.85}})
          .bindPopup('<b>' + item.position + '</b><br>' + item.label)
          .addTo(map);
      }});
      if (geometry) {{
        const line = L.geoJSON(geometry, {{
          style: {{color: routeCurrent ? '#2468c9' : '#777', weight:5, dashArray: routeCurrent ? null : '8 7'}}
        }}).addTo(map);
        const lineBounds = line.getBounds();
        if (lineBounds.isValid()) {{ map.fitBounds(lineBounds.pad(0.12)); }}
      }} else if (bounds.length) {{
        map.fitBounds(bounds, {{padding:[30,30], maxZoom:15}});
      }}
      const legend = L.control({{position:'bottomleft'}});
      legend.onAdd = function() {{
        const div = L.DomUtil.create('div', 'legend');
        div.innerHTML = '● Personel &nbsp; <span style="color:#18864b">● Başlangıç</span> &nbsp; '
          + '<span style="color:#e79018">● Ara</span> &nbsp; <span style="color:#c93636">● Seçil Kauçuk (Bitiş)</span>';
        return div;
      }};
      legend.addTo(map);
    }}
  </script>
</body>
</html>"""
