"""Rota Tasarımı sekmesi için düzenlenebilir Leaflet haritası."""

from __future__ import annotations

import json
import os

from app.factory import FactoryDestination
from services.route_design import RouteDesignPoint


def render_route_designer_html(
    points: tuple[RouteDesignPoint, ...],
    destination: FactoryDestination,
    geometry: dict[str, object] | None = None,
) -> str:
    point_data = [
        {
            "lat": point.latitude,
            "lon": point.longitude,
            "label": point.name or point.address or f"Kontrol noktası {index}",
            "sourceIndex": index - 1,
        }
        for index, point in enumerate(points, start=1)
        if point.latitude is not None and point.longitude is not None
    ]
    destination_data = {
        "lat": destination.latitude,
        "lon": destination.longitude,
        "label": destination.name,
    }
    tile_url = os.getenv(
        "SERVIS_ATAMA_TILE_URL",
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    )
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{{height:100%;margin:0}}#fallback{{display:none;padding:18px}}</style>
</head><body><div id="map"></div><div id="fallback">Harita yüklenemedi. İnternet bağlantısını kontrol edin.</div>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const initialPoints={json.dumps(point_data, ensure_ascii=False)};
const destination={json.dumps(destination_data, ensure_ascii=False)};
const initialGeometry={json.dumps(geometry, ensure_ascii=False)};
let routeBridge=null;
if(typeof QWebChannel!=='undefined'&&typeof qt!=='undefined'){{
 new QWebChannel(qt.webChannelTransport,channel=>{{routeBridge=channel.objects.routeBridge;}});
}}
if(typeof L==='undefined'){{document.getElementById('map').style.display='none';document.getElementById('fallback').style.display='block';}}
else{{
 const map=L.map('map').setView([36.80,34.63],9);
 L.tileLayer({json.dumps(tile_url)},{{maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap katkıda bulunanlar</a>'}}).addTo(map);
 let markers=[]; let routeLine=null; let interactionMode='add';
 let drawing=false; let drawSamples=[]; let drawPreview=null;
 let notificationsEnabled=false;
 const endMarker=L.circleMarker([destination.lat,destination.lon],{{radius:10,color:'#c93636',fillOpacity:.9}}).bindPopup('<b>Bitiş / Fabrika</b><br>'+destination.label).addTo(map);
 function renumber(){{markers.forEach((entry,index)=>entry.marker.bindTooltip(String(index+1),{{permanent:true,direction:'top'}}).bindPopup('<b>'+(index===0?'Başlangıç':'Ara Nokta')+'</b><br>'+(entry.label||('Kontrol noktası '+(index+1)))));}}
 function currentPoints(){{
   const result=markers.map((x,index)=>({{latitude:x.marker.getLatLng().lat,longitude:x.marker.getLatLng().lng,name:x.label||('Harita noktası '+(index+1)),sourceIndex:x.sourceIndex}}));
   markers.forEach((entry,index)=>entry.sourceIndex=index); return result;
 }}
 function invalidateRoute(){{if(routeLine){{map.removeLayer(routeLine);routeLine=null;}}}}
 function notifyPointsChanged(){{
   if(notificationsEnabled&&routeBridge)routeBridge.pointsChanged(JSON.stringify(currentPoints()));
 }}
 function removePointAt(index,notifyChange=true){{
   if(index<0||index>=markers.length)return false;
   map.removeLayer(markers[index].marker); markers.splice(index,1); renumber();
   if(notifyChange){{invalidateRoute();notifyPointsChanged();}} return true;
 }}
 function addPoint(lat,lon,label,index,sourceIndex,notifyChange=true){{
   const marker=L.marker([lat,lon],{{draggable:true}}).addTo(map);
   const entry={{marker:marker,label:label||'',sourceIndex:Number.isInteger(sourceIndex)?sourceIndex:null}};
   if(index===undefined||index<0||index>markers.length)markers.push(entry);else markers.splice(index,0,entry);
   marker.on('dragend',()=>{{renumber();invalidateRoute();notifyPointsChanged();}});
   marker.on('click',event=>{{
     if(interactionMode==='delete'){{
       L.DomEvent.stopPropagation(event); removePointAt(markers.indexOf(entry));
     }}
   }});
   marker.on('contextmenu',event=>{{
     L.DomEvent.stopPropagation(event); removePointAt(markers.indexOf(entry));
   }});
   renumber(); endMarker.bringToFront();
   if(notifyChange){{invalidateRoute();notifyPointsChanged();}} return entry;
 }}
 initialPoints.forEach(p=>addPoint(p.lat,p.lon,p.label,undefined,p.sourceIndex,false));
 function setGeometry(value){{if(routeLine){{map.removeLayer(routeLine);routeLine=null}} if(value){{routeLine=L.geoJSON(value,{{style:{{color:'#2468c9',weight:5}}}}).addTo(map);}} endMarker.bringToFront(); fit();}}
 function fit(){{
   if(!markers.length&&!routeLine){{map.setView([36.80,34.63],9);return;}}
   const group=L.featureGroup(markers.map(x=>x.marker).concat([endMarker]));
   if(routeLine)group.addLayer(routeLine); const b=group.getBounds();
   if(b.isValid())map.fitBounds(b.pad(.12));
 }}
 function setMode(mode){{
   if(!['pan','add','draw','delete'].includes(mode))return interactionMode;
   interactionMode=mode;
   const cursors={{pan:'grab',add:'crosshair',draw:'crosshair',delete:'not-allowed'}};
   map.getContainer().style.cursor=cursors[mode];
   if(mode==='draw')map.dragging.disable();else map.dragging.enable();
   return interactionMode;
 }}
 function sampledDrawingPoints(samples){{
   if(!samples.length)return [];
   const kept=[samples[0]];
   for(let index=1;index<samples.length-1;index++){{
     const previous=map.latLngToContainerPoint(kept[kept.length-1]);
     const current=map.latLngToContainerPoint(samples[index]);
     if(previous.distanceTo(current)>=45)kept.push(samples[index]);
   }}
   const last=samples[samples.length-1];
   if(map.latLngToContainerPoint(kept[kept.length-1]).distanceTo(map.latLngToContainerPoint(last))>=12)kept.push(last);
   if(kept.length<=25)return kept;
   const reduced=[];
   for(let index=0;index<25;index++)reduced.push(kept[Math.round(index*(kept.length-1)/24)]);
   return reduced;
 }}
function finishDrawing(){{
   if(!drawing)return;
   drawing=false;
   const selected=sampledDrawingPoints(drawSamples);
   if(drawPreview){{map.removeLayer(drawPreview);drawPreview=null;}}
   drawSamples=[];
   selected.forEach(latlng=>addPoint(latlng.lat,latlng.lng,'Çizim noktası '+(markers.length+1),undefined,undefined,false));
   if(selected.length>=2&&routeBridge){{
     routeBridge.traceDrawn(JSON.stringify(markers.map((entry,index)=>({{
       latitude:entry.marker.getLatLng().lat,
       longitude:entry.marker.getLatLng().lng,
       name:entry.label||('Çizim noktası '+(index+1)),
       sourceIndex:entry.sourceIndex
     }}))));
   }}
}}
 setGeometry(initialGeometry); fit(); setMode('add'); notificationsEnabled=true;
 map.on('click',event=>{{if(interactionMode==='add')addPoint(event.latlng.lat,event.latlng.lng,'Harita noktası');}});
 map.on('mousedown',event=>{{
   if(interactionMode!=='draw'||(event.originalEvent&&event.originalEvent.button!==0))return;
   drawing=true; drawSamples=[event.latlng];
   drawPreview=L.polyline(drawSamples,{{color:'#1769aa',weight:4,dashArray:'7 6'}}).addTo(map);
 }});
 map.on('mousemove',event=>{{
   if(!drawing)return; drawSamples.push(event.latlng); drawPreview.setLatLngs(drawSamples);
 }});
 map.on('mouseup',finishDrawing);
 document.addEventListener('mouseup',finishDrawing);
 window.routeDesigner={{
   getPoints:currentPoints,
   removePoint:removePointAt,
   movePoint:(index,direction)=>{{const target=index+direction;if(index>=0&&target>=0&&index<markers.length&&target<markers.length){{const item=markers.splice(index,1)[0];markers.splice(target,0,item);renumber();}}}},
   insertAfter:(index,lat,lon,label)=>addPoint(lat,lon,label,index+1),
   setGeometry:setGeometry,
   setMode:setMode,
   getMode:()=>interactionMode,
   undoLast:()=>removePointAt(markers.length-1),
   clearPoints:()=>{{while(markers.length)removePointAt(markers.length-1,false);invalidateRoute();notifyPointsChanged();}},
   fit:fit,
   getView:()=>({{latitude:map.getCenter().lat,longitude:map.getCenter().lng,zoom:map.getZoom()}})
 }};
}}
</script></body></html>"""
