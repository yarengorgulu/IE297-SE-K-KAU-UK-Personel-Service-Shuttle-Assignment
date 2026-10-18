"""Personel seçimi ve atama sonucunun Leaflet haritası."""

from __future__ import annotations

import json
import os

from services.assignment_preview import AssignmentPreviewResult, PersonnelOption


def render_assignment_preview_html(
    result: AssignmentPreviewResult | None,
    *,
    personnel: tuple[PersonnelOption, ...] = (),
) -> str:
    """Seçili personelleri; varsa uygun/atanmış sonucu rota ile birlikte gösterir."""
    tile_url = os.getenv(
        "SERVIS_ATAMA_TILE_URL",
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    )
    people = [
        {
            "lat": item.latitude,
            "lon": item.longitude,
            "label": f"{item.personnel_number} - {item.name}",
            "address": item.address_label,
        }
        for item in personnel
        if item.latitude is not None and item.longitude is not None
    ]
    route = None
    pickup = None
    route_label = None
    if (
        result is not None
        and result.status in {"UYGUN", "ATANDI"}
        and result.route_geometry is not None
        and result.personnel_latitude is not None
        and result.personnel_longitude is not None
    ):
        people = [
            {
                "lat": result.personnel_latitude,
                "lon": result.personnel_longitude,
                "label": f"{result.personnel_number} - {result.personnel_name}",
                "address": "Atama sonucu seçili personel",
            }
        ]
        route = result.route_geometry
        route_label = f"{result.service_code} / {result.route_name}"
        if result.pickup_latitude is not None and result.pickup_longitude is not None:
            pickup = {
                "lat": result.pickup_latitude,
                "lon": result.pickup_longitude,
                "label": f"Önerilen biniş / {result.distance_meters or 0:.0f} m",
            }
    payload = {
        "people": people,
        "route": route,
        "pickup": pickup,
        "routeLabel": route_label,
    }
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body,#map{{height:100%;margin:0;font-family:sans-serif}}
#fallback{{display:none;padding:26px;color:#5c6675;text-align:center}}
.legend,.hint{{background:white;padding:7px 9px;border-radius:4px;box-shadow:0 1px 5px #888}}
.hint{{color:#44505f;max-width:280px}}
</style></head><body><div id="map"></div><div id="fallback">Harita yüklenemedi. İnternet bağlantısını kontrol edin.</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={data_json};
if(typeof L==='undefined'){{
 document.getElementById('map').style.display='none';document.getElementById('fallback').style.display='block';
}}else{{
 const map=L.map('map').setView([36.80,34.63],10);
 L.tileLayer({json.dumps(tile_url)},{{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap katkıda bulunanlar</a>'}}).addTo(map);
 const bounds=[];
 data.people.forEach(person=>{{
   const point=[person.lat,person.lon]; bounds.push(point);
   L.circleMarker(point,{{radius:9,color:'#111',fillColor:'#111',fillOpacity:.9}})
     .bindPopup('<b>Personel</b><br>'+person.label+'<br>'+person.address).addTo(map);
 }});
 let routeLayer=null;
 if(data.route){{
   routeLayer=L.geoJSON(data.route,{{style:{{color:'#1769aa',weight:6}}}})
     .bindPopup('<b>Servis Rotası</b><br>'+data.routeLabel).addTo(map);
 }}
 if(data.pickup&&data.people.length){{
   const pickup=[data.pickup.lat,data.pickup.lon]; bounds.push(pickup);
   L.circleMarker(pickup,{{radius:9,color:'#18864b',fillColor:'#23a060',fillOpacity:.9}})
     .bindPopup('<b>'+data.pickup.label+'</b>').addTo(map);
   L.polyline([[data.people[0].lat,data.people[0].lon],pickup],{{color:'#d97706',weight:3,dashArray:'7 6'}}).addTo(map);
 }}
 if(routeLayer&&routeLayer.getBounds().isValid()){{
   const routeBounds=routeLayer.getBounds(); bounds.forEach(point=>routeBounds.extend(point)); map.fitBounds(routeBounds.pad(.12));
 }}else if(bounds.length){{map.fitBounds(bounds,{{padding:[35,35],maxZoom:15}});}}
 if(!bounds.length&&!routeLayer){{
   const hint=L.control({{position:'topright'}});hint.onAdd=()=>{{const div=L.DomUtil.create('div','hint');div.innerHTML='Personel listesinden bir kişi seçtiğinizde konumu burada gösterilir.';return div;}};hint.addTo(map);
 }}
 const legend=L.control({{position:'bottomleft'}});legend.onAdd=()=>{{const div=L.DomUtil.create('div','legend');div.innerHTML='● Personel &nbsp; <span style="color:#18864b">● Önerilen biniş</span> &nbsp; <span style="color:#1769aa">━ Servis rotası</span>';return div;}};legend.addTo(map);
}}
</script></body></html>"""
