import sys
import os
import json
import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta

ARCHIVE_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "positions.json")
OUTPUT_PATH        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp", "map.html")
EXTENDED_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp", "data_extended.json")

TAG_RENAME = {"Google Pixel 9": "My Phone"}

STATUS_BORDER = {
    "AGGREGATED":   "#38bdf8",
    "CROWDSOURCED": "#2563eb",
    "LAST_KNOWN":   "#bae6fd",
}
DEFAULT_BORDER = "#9ca3af"

STATUS_DESC = {
    "LAST_KNOWN":   "Last position reported directly by the tracker itself.",
    "CROWDSOURCED": "Position estimated from nearby Android devices that passively detected the tracker.",
    "AGGREGATED":   "Position computed by combining multiple recent crowd signals.",
}

TAG_COLORS = ["#facc15", "#22c55e", "#a78bfa", "#ec4899", "#f97316"]

# Static JS — real braces, no f-string escaping needed
_STATIC_JS = """
var _chartHovered=null;

function _fmtLocal(iso){
  var d=new Date(iso);
  return d.getFullYear()+'-'+(d.getMonth()+1).toString().padStart(2,'0')+'-'
    +d.getDate().toString().padStart(2,'0')+'T'
    +d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0');
}

function _mkIcon(meta,entry,isLast){
  var border=_SB[entry.status]||_DB,lc=isLast?'#ffffff':'#505050';
  var fs=meta.letter.length>1?'9px':'13px';
  return '<div style="width:28px;height:28px;border-radius:50%;background:'+meta.color
    +';border:3px solid '+border+';box-shadow:0 1px 4px rgba(0,0,0,.5)'
    +';display:flex;align-items:center;justify-content:center'
    +';font-weight:bold;font-size:'+fs+';color:'+lc+';font-family:sans-serif">'+meta.letter+'</div>';
}

function _mkPop(entry){
  var acc=entry.accuracy_m||0;
  var s='<b>'+entry.tag+'</b><br>Status: '+(entry.status||'')
    +'<br>Own report: '+entry.is_own_report
    +'<br>Location time: '+_fmtLocal(entry.location_time)
    +'<br>Polled at: '+(entry.polled_at?_fmtLocal(entry.polled_at):'?')
    +'<br>Accuracy: '+(acc?acc.toFixed(0)+' m':'?');
  if(entry.altitude_m)s+='<br>Altitude: '+entry.altitude_m+' m';
  return s;
}

function _isLast(me){return _lastByTag[me.tag]===me.t;}

function _highlightMarker(me){
  if(!me.v||!me.v._icon)return;
  var meta=_tagMeta[me.tag];if(!meta)return;
  var lc=_isLast(me)?'#ffffff':'#505050';
  var fs=meta.letter.length>1?'9px':'13px';
  me.v.setIcon(L.divIcon({html:'<div style="width:28px;height:28px;border-radius:50%;background:'+meta.color
    +';border:3px solid #fff;box-shadow:0 0 0 2px #06b6d4,0 1px 4px rgba(0,0,0,.5)'
    +';display:flex;align-items:center;justify-content:center'
    +';font-weight:bold;font-size:'+fs+';color:'+lc+';font-family:sans-serif">'+meta.letter+'</div>',
    iconSize:[28,28],iconAnchor:[14,14],className:''}));
}

function _restoreMarker(me){
  if(!me.v)return;
  var meta=_tagMeta[me.tag];if(!meta)return;
  me.v.setIcon(L.divIcon({html:_mkIcon(meta,{status:me.st},_isLast(me)),
    iconSize:[28,28],iconAnchor:[14,14],className:''}));
}

function toggleAccCircle(id,lat,lon,acc,color){
  if(_accCircles[id]){_map.removeLayer(_accCircles[id]);delete _accCircles[id];}
  else{_accCircles[id]=L.circle([lat,lon],{radius:acc,color:color,fillColor:color,fillOpacity:0.12,weight:1.5}).addTo(_map);}
}

function _addEntries(entries,lastByTag,isExt){
  var byTag={};
  entries.forEach(function(e){if(!byTag[e.tag])byTag[e.tag]=[];byTag[e.tag].push(e);});
  Object.keys(byTag).forEach(function(tag){
    var meta=_tagMeta[tag];if(!meta)return;
    var fg=window[meta.group];
    var sorted=byTag[tag].sort(function(a,b){return a.location_time<b.location_time?-1:1;});
    if(isExt){
      var oldest24h=null,oldest24hT=null;
      _markerEntries.forEach(function(me){
        if(me.tag===tag&&(oldest24hT===null||me.t<oldest24hT)){oldest24hT=me.t;oldest24h=me;}
      });
      var newestExt=sorted[sorted.length-1];
      if(oldest24h&&newestExt&&fg){
        var bs=L.polyline([[newestExt.lat,newestExt.lon],[oldest24h.lat,oldest24h.lon]],
          {color:'#6b7280',weight:1.5,opacity:0.6}).addTo(fg);
        _segEntries.push({v:bs,t:oldest24h.t,ta:newestExt.location_time,st:oldest24h.st,sta:newestExt.status||''});
      }
    }
    sorted.forEach(function(entry,idx){
      var isLast=!!(lastByTag&&lastByTag[tag]===entry.location_time);
      var acc=entry.accuracy_m||0;
      var mk=L.marker([entry.lat,entry.lon],{
        icon:L.divIcon({html:_mkIcon(meta,entry,isLast),iconSize:[28,28],iconAnchor:[14,14],className:''})
      }).bindPopup(_mkPop(entry),{maxWidth:220}).bindTooltip(meta.letter+' — '+_fmtLocal(entry.location_time));
      if(fg)mk.addTo(fg);
      var mid=(isExt?'ext_':'h24_')+tag+'_'+entry.location_time;
      if(acc){(function(mid,lat,lon,acc,color){
        mk.on('dblclick',function(e){toggleAccCircle(mid,lat,lon,acc,color);L.DomEvent.stopPropagation(e);});
        mk.on('mouseover',function(){
          if(mk._icon&&parseFloat(mk._icon.style.opacity)<1)return;
          if(_hoverCircle){_map.removeLayer(_hoverCircle);}
          _hoverCircle=L.circle([lat,lon],{radius:acc,color:color,fillOpacity:0,weight:1.5,dashArray:'4,4'}).addTo(_map);
        });
        mk.on('mouseout',function(){
          if(_hoverCircle){_map.removeLayer(_hoverCircle);_hoverCircle=null;}
        });
      })(mid,entry.lat,entry.lon,acc,meta.color);}
      _markerEntries.push({v:mk,id:mid,t:entry.location_time,lat:entry.lat,lon:entry.lon,acc:acc,tag:entry.tag,st:entry.status||''});
      if(idx>0){
        var prev=sorted[idx-1];
        if(fg){
          var sg=L.polyline([[prev.lat,prev.lon],[entry.lat,entry.lon]],{color:'#6b7280',weight:1.5,opacity:0.6}).addTo(fg);
          _segEntries.push({v:sg,t:entry.location_time,ta:prev.location_time,st:entry.status||'',sta:prev.status||''});
        }
      }
    });
  });
}

function _loadExt(cb){
  if(_extLoaded){if(cb)cb();return;}
  if(_extLoading)return;
  _extLoading=true;
  fetch('data_extended.json').then(function(r){return r.json();}).then(function(entries){
    _addEntries(entries,null,true);
    _extLoaded=true;_extLoading=false;if(cb)cb();
  }).catch(function(){_extLoading=false;if(cb)cb();});
}

function _applyArrows(){
  var svg=document.querySelector('.leaflet-overlay-pane svg');if(!svg)return;
  var defs=svg.querySelector('defs');
  if(!defs){defs=document.createElementNS('http://www.w3.org/2000/svg','defs');svg.insertBefore(defs,svg.firstChild);}
  if(!document.getElementById('stationaryHatch')){
    var _pat=document.createElementNS('http://www.w3.org/2000/svg','pattern');
    _pat.id='stationaryHatch';_pat.setAttribute('patternUnits','userSpaceOnUse');
    _pat.setAttribute('width','8');_pat.setAttribute('height','8');_pat.setAttribute('patternTransform','rotate(45)');
    var _ln=document.createElementNS('http://www.w3.org/2000/svg','line');
    _ln.setAttribute('x1','0');_ln.setAttribute('y1','0');_ln.setAttribute('x2','0');_ln.setAttribute('y2','8');
    _ln.setAttribute('stroke','#ec4899');_ln.setAttribute('stroke-width','2');_ln.setAttribute('opacity','0.55');
    _pat.appendChild(_ln);defs.appendChild(_pat);
  }
  if(!document.getElementById('tagArrow')){
    var _m=document.createElementNS('http://www.w3.org/2000/svg','marker');
    _m.id='tagArrow';_m.setAttribute('markerWidth','20');_m.setAttribute('markerHeight','12');
    _m.setAttribute('refX','34');_m.setAttribute('refY','6');
    _m.setAttribute('orient','auto');_m.setAttribute('markerUnits','userSpaceOnUse');
    var _p=document.createElementNS('http://www.w3.org/2000/svg','polygon');
    _p.setAttribute('points','0,0 20,6 0,12');_p.setAttribute('fill','#6b7280');_p.setAttribute('opacity','0.9');
    _m.appendChild(_p);defs.appendChild(_m);
  }
  _segEntries.forEach(function(s){if(s.v&&s.v._path)s.v._path.setAttribute('marker-end','url(#tagArrow)');});
}

function _updateCounts(){
  var cutoff=_currentMaxAge===null?null:new Date(Date.now()-_currentMaxAge*86400000);
  var counts={};
  _markerEntries.forEach(function(me){
    if(!counts[me.tag])counts[me.tag]=0;
    if((cutoff===null||new Date(me.t)>=cutoff)&&_statusEnabled[me.st]!==false)counts[me.tag]++;
  });
  Object.keys(counts).forEach(function(tag){
    var meta=_tagMeta[tag];
    var el=document.getElementById('count_'+(meta?meta.letter:tag.trim()[0].toUpperCase()));
    if(el)el.textContent=counts[tag]||'';
  });
}

function _applyVisibility(){
  if(_visLock)return;_visLock=true;
  var cutoff=_currentMaxAge===null?null:new Date(Date.now()-_currentMaxAge*86400000);
  _markerEntries.forEach(function(me){
    var mk=me.v;if(!mk||!mk._icon)return;
    var show=(cutoff===null||new Date(me.t)>=cutoff)&&_statusEnabled[me.st]!==false;
    mk._icon.style.display=show?'':'none';
    if(mk._shadow)mk._shadow.style.display=show?'':'none';
    if(!show&&_accCircles[me.id]){_accCircles[me.id].remove();delete _accCircles[me.id];}
  });
  _segEntries.forEach(function(se){
    var p=se.v;if(!p||!p._path)return;
    var tOk=cutoff===null||(new Date(se.ta)>=cutoff&&new Date(se.t)>=cutoff);
    var stOk=_statusEnabled[se.st]!==false&&_statusEnabled[se.sta]!==false;
    p._path.style.display=(tOk&&stOk&&_pathsVisible)?'':'none';
  });
  _applyArrows();_updateStationary();_updateCounts();try{_updateChart();}catch(e){console.error('chart',e);}_visLock=false;
}
window._applyVisibility=_applyVisibility;

function _updateStationary(){
  if(window._stationaryLayer){window._stationaryLayer.remove();window._stationaryLayer=null;}
  if(_currentMaxAge===null||(Math.abs(_currentMaxAge-1/24)>0.001&&Math.abs(_currentMaxAge-3/24)>0.001&&Math.abs(_currentMaxAge-8/24)>0.001&&Math.abs(_currentMaxAge-1)>0.001))return;
  var _is24h=Math.abs(_currentMaxAge-1)<0.001;
  var _minPts=_is24h?6:2,_maxOut=_is24h?3:2;
  var _byTag={};
  _markerEntries.forEach(function(me){
    var mk=me.v;if(!mk||!mk._icon||mk._icon.style.display==='none')return;
    if(!_byTag[me.tag])_byTag[me.tag]=[];
    _byTag[me.tag].push(me);
  });
  var _tags=Object.keys(_byTag);if(!_tags.length)return;
  var _R=6371000;
  function _gd(a,b,c,d){
    var dl=(c-a)*Math.PI/180,dn=(d-b)*Math.PI/180;
    var x=Math.sin(dl/2)*Math.sin(dl/2)+Math.cos(a*Math.PI/180)*Math.cos(c*Math.PI/180)*Math.sin(dn/2)*Math.sin(dn/2);
    return _R*2*Math.atan2(Math.sqrt(x),Math.sqrt(1-x));
  }
  _addingStationary=true;
  var _g=L.layerGroup().addTo(_map);
  _tags.forEach(function(tag){
    var _pts=_byTag[tag].filter(function(p){return p.acc>0&&p.acc<=_accThreshold;});
    if(_pts.length<_minPts)return;
    var _wLat=0,_wLon=0,_wSum=0;
    _pts.forEach(function(p){var w=1/(p.acc*p.acc);_wLat+=p.lat*w;_wLon+=p.lon*w;_wSum+=w;});
    var _eLat=_wLat/_wSum,_eLon=_wLon/_wSum,_eAcc=1/Math.sqrt(_wSum);
    var _inliers=_pts.filter(function(p){return _gd(_eLat,_eLon,p.lat,p.lon)<=500;});
    var _outlierCount=_pts.length-_inliers.length;
    if(_outlierCount>_maxOut||_inliers.length<_minPts)return;
    if(_outlierCount>0){
      _wLat=0;_wLon=0;_wSum=0;
      _inliers.forEach(function(p){var w=1/(p.acc*p.acc);_wLat+=p.lat*w;_wLon+=p.lon*w;_wSum+=w;});
      _eLat=_wLat/_wSum;_eLon=_wLon/_wSum;_eAcc=1/Math.sqrt(_wSum);
    }
    var _tipTxt='Stima '+tag+' \xb1'+_eAcc.toFixed(0)+'m ('+_inliers.length+' punti)';
    var _popTxt='<b>Centroide — '+tag+'</b><br>Punti usati: '+_inliers.length+'<br>Precisione: \xb1'+_eAcc.toFixed(0)+' m';
    var _circ=L.circle([_eLat,_eLon],{radius:_eAcc,color:'#ec4899',fillColor:'#fce7f3',
      fillOpacity:0.15,weight:2,dashArray:'4,4'})
      .bindTooltip(_tipTxt).addTo(_g);
    if(_circ._path){_circ._path.setAttribute('fill','url(#stationaryHatch)');_circ._path.setAttribute('fill-opacity','1');}
    var _cmeta=_tagMeta[tag];
    var _letter=_cmeta?_cmeta.letter:tag.trim()[0].toUpperCase();
    var _cfs=_letter.length>1?'9px':'12px';
    var _cm=L.marker([_eLat,_eLon],{
      pane:'centroidPane',
      icon:L.divIcon({html:'<div style="width:22px;height:22px;border-radius:50%;background:#ec4899;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:'+_cfs+';color:#fff;font-family:sans-serif">'+_letter+'</div>',iconSize:[22,22],iconAnchor:[11,11],className:''}),
      zIndexOffset:1000
    }).bindTooltip(_tipTxt).bindPopup(_popTxt).addTo(_g);
    _cm.on('dblclick',function(e){_cm.openPopup();L.DomEvent.stopPropagation(e);});
    _circ.on('dblclick',function(e){_cm.openPopup();L.DomEvent.stopPropagation(e);});
  });
  _addingStationary=false;
  window._stationaryLayer=_g;
}

window._cleanAccForTag=function(tagName){
  _markerEntries.forEach(function(me){
    if(me.tag===tagName&&_accCircles[me.id]){_accCircles[me.id].remove();delete _accCircles[me.id];}
  });
};

function showStatusTip(e,txt){
  var t=document.getElementById('status-tip');
  t.textContent=txt;t.style.display='block';
  t.style.left=(e.clientX+12)+'px';t.style.top=(e.clientY+12)+'px';
}
function hideStatusTip(){document.getElementById('status-tip').style.display='none';}

function toggleTag(circle){
  var layer=window[circle.dataset.jsvar];
  var letter=circle.id.replace('circle_','');
  var label=document.getElementById('label_'+letter);
  if(!layer)return;
  if(circle.dataset.active==='1'){
    if(window._cleanAccForTag)window._cleanAccForTag(circle.dataset.tag);
    _map.removeLayer(layer);
    // _applyVisibility intentionally NOT called: centroid stays visible after tag is hidden
    circle.dataset.active='0';circle.style.background='#d1d5db';circle.style.color='#9ca3af';
    if(label){label.style.opacity='0.4';label.style.textDecoration='line-through';}
  }else{
    _map.addLayer(layer);
    if(window._applyVisibility)window._applyVisibility();
    circle.dataset.active='1';circle.style.background=circle.dataset.color;circle.style.color='#fff';
    if(label){label.style.opacity='1';label.style.textDecoration='none';}
  }
}

window.toggleStatus=function(status,el){
  _statusEnabled[status]=!_statusEnabled[status];
  el.style.opacity=_statusEnabled[status]?'1':'0.35';
  el.style.textDecoration=_statusEnabled[status]?'none':'line-through';
  _applyVisibility();
};

window.setTimeFilter=function(days,btn){
  document.querySelectorAll('.tf-btn').forEach(function(b){
    b.style.background=b===btn?'#6b7280':'#e5e7eb';b.style.color=b===btn?'#fff':'#374151';
  });
  if(days===null||days>=3){_loadExt(function(){_currentMaxAge=days;_applyVisibility();});}
  else{_currentMaxAge=days;_applyVisibility();}
};

window.togglePaths=function(btn){
  _pathsVisible=!_pathsVisible;_applyVisibility();
  btn.style.background=_pathsVisible?'#6b7280':'#e5e7eb';btn.style.color=_pathsVisible?'#fff':'#374151';
};

function _updateChart(){
  if(typeof Chart==='undefined')return;
  var cutoff=_currentMaxAge===null?null:new Date(Date.now()-_currentMaxAge*86400000);
  var byTag={};
  _markerEntries.forEach(function(me){
    if(!me.acc)return;
    var timeOk=cutoff===null||new Date(me.t)>=cutoff;
    var statusOk=_statusEnabled[me.st]!==false;
    var meta=_tagMeta[me.tag];var fg=meta?window[meta.group]:null;
    var tagOn=fg&&_map.hasLayer(fg);
    if(!timeOk||!statusOk||!tagOn)return;
    if(!byTag[me.tag])byTag[me.tag]=[];
    byTag[me.tag].push({x:new Date(me.t).getTime(),y:me.acc});
  });
  var datasets=Object.keys(byTag).map(function(tag){
    var meta=_tagMeta[tag];
    var pts=byTag[tag].sort(function(a,b){return a.x-b.x;});
    return{label:tag,data:pts,borderColor:meta?meta.color:'#888',
      backgroundColor:meta?meta.color:'#888',
      pointRadius:3,pointHoverRadius:5,showLine:true,tension:0,borderWidth:1.5};
  });
  var xMin=cutoff?cutoff.getTime():null,xMax=Date.now();
  var _rx0=xMin!==null?xMin:xMax-86400000*30,_rx1=xMax+3600000;
  var refLines=[
    {label:'_ref10',data:[{x:_rx0,y:10},{x:_rx1,y:10}],
     borderColor:'#ec4899',borderDash:[5,4],borderWidth:1,
     pointRadius:0,pointHoverRadius:0,showLine:true,tension:0,fill:false},
    {label:'_ref50',data:[{x:_rx0,y:50},{x:_rx1,y:50}],
     borderColor:'#facc15',borderDash:[5,4],borderWidth:1,
     pointRadius:0,pointHoverRadius:0,showLine:true,tension:0,fill:false}
  ];
  var allDs=datasets.concat(refLines);
  if(!_accChart){
    var ctx=document.getElementById('acc-chart');if(!ctx)return;
    _accChart=new Chart(ctx,{type:'scatter',data:{datasets:allDs},options:{
      animation:false,responsive:true,maintainAspectRatio:false,
      onHover:function(evt,elements){
        if(!elements.length){if(_chartHovered){_restoreMarker(_chartHovered);_chartHovered=null;}return;}
        var el=elements[0];
        var ds=_accChart.data.datasets[el.datasetIndex];
        if(ds.label.startsWith('_ref'))return;
        var pt=ds.data[el.index];
        var target=null;
        _markerEntries.forEach(function(me){if(me.tag===ds.label&&new Date(me.t).getTime()===pt.x)target=me;});
        if(!target||target===_chartHovered)return;
        if(_chartHovered)_restoreMarker(_chartHovered);
        _chartHovered=target;_highlightMarker(target);
      },
      scales:{
        x:{type:'linear',min:xMin,max:xMax,
          ticks:{color:'#9ca3af',maxTicksLimit:7,callback:function(v){
            var d=new Date(v);
            return d.getHours().toString().padStart(2,'0')+':'+d.getMinutes().toString().padStart(2,'0');
          }},
          grid:{color:'#374151'}},
        y:{type:'logarithmic',title:{display:true,text:'acc (m)',color:'#9ca3af',font:{size:11}},
          min:1,ticks:{color:'#9ca3af',callback:function(v){return v;}},grid:{color:'#374151'}}
      },
      plugins:{
        legend:{display:false},
        tooltip:{filter:function(item){return !item.dataset.label.startsWith('_ref');},
          callbacks:{label:function(c){var m=_tagMeta[c.dataset.label];return (m?m.letter:c.dataset.label)+': '+c.parsed.y.toFixed(0)+' m';}}}
      }
    }});
  }else{
    _accChart.data.datasets=allDs;
    _accChart.options.scales.x.min=xMin;
    _accChart.options.scales.x.max=xMax;
    _accChart.update('none');
  }
}

window.setAccThreshold=function(val,btn){
  _accThreshold=val;
  document.querySelectorAll('.ac-btn').forEach(function(b){
    b.style.background=b===btn?'#6b7280':'#e5e7eb';b.style.color=b===btn?'#fff':'#374151';
  });
  _markerEntries.forEach(function(me){
    if(!me.v)return;
    me.v.setOpacity((me.acc>0&&me.acc>_accThreshold)?0.25:1);
  });
  _updateStationary();
};
"""


def assign_letters(all_tags: list) -> dict:
    by_first = {}
    for name in all_tags:
        c = name.strip()[0].upper()
        by_first.setdefault(c, []).append(name)
    result = {}
    for names in by_first.values():
        if len(names) == 1:
            result[names[0]] = names[0].strip()[0].upper()
        else:
            for name in names:
                result[name] = name.strip()[:2].upper()
    return result


def load_entries():
    if not os.path.exists(ARCHIVE_PATH):
        print(f"No archive found at {ARCHIVE_PATH}. Run poller.py first.")
        sys.exit(0)

    by_tag = defaultdict(list)
    with open(ARCHIVE_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            e["tag"] = TAG_RENAME.get(e["tag"], e["tag"])
            if "lat" in e and "lon" in e:
                by_tag[e["tag"]].append(e)

    return {tag: sorted(entries, key=lambda e: e["location_time"]) for tag, entries in by_tag.items()}


def split_entries(by_tag, cutoff_dt):
    cutoff_str = cutoff_dt.isoformat()
    within = defaultdict(list)
    extended = []
    for tag, entries in by_tag.items():
        for e in entries:
            if e["location_time"] >= cutoff_str:
                within[tag].append(e)
            else:
                extended.append(e)
    return dict(within), extended


def _build_legend(all_tags, tag_color, letter_map, last_polled_at=""):
    STATUS_DISPLAY = {"AGGREGATED": "Aggr", "CROWDSOURCED": "Crown", "LAST_KNOWN": "BT"}
    status_col = ""
    for status, color in STATUS_BORDER.items():
        desc = STATUS_DESC.get(status, "")
        active = status == "AGGREGATED"
        status_col += (
            f"<div id='st_{status}' data-active='{'1' if active else '0'}'"
            f" onclick='toggleStatus(\"{status}\",this)'"
            f" onmouseenter='showStatusTip(event,\"{desc}\")' onmouseleave='hideStatusTip()'"
            f" style='display:flex;align-items:center;gap:6px;margin-bottom:4px;"
            f"cursor:pointer;user-select:none;border-radius:4px;padding:2px 4px;"
            f"opacity:{'1' if active else '0.35'};text-decoration:{'none' if active else 'line-through'}'>"
            f"<span style='width:14px;height:14px;border-radius:50%;flex-shrink:0;"
            f"background:{color};display:inline-block'></span>"
            f"<span>{STATUS_DISPLAY.get(status, status)}</span></div>"
        )
    if last_polled_at:
        try:
            dt = datetime.fromisoformat(last_polled_at.replace("Z", "+00:00")).astimezone()
            poll_str = f"{dt.strftime('%d/%m/%Y')}<br>{dt.strftime('%H:%M')}"
        except Exception:
            poll_str = last_polled_at[:16]
        status_col += (
            f"<div id='last-poll-time' style='margin-top:6px;font-size:10px;color:#9ca3af;line-height:1.4'>"
            f"{poll_str}</div>"
        )
    else:
        status_col += (
            "<div id='last-poll-time' style='margin-top:6px;font-size:10px;color:#9ca3af;line-height:1.4'>"
            "—</div>"
        )
    status_col += (
        "<div style='margin-top:6px'>"
        "<span onclick='var p=document.getElementById(\"help-panel\");"
        "p.style.display=p.style.display===\"none\"?\"block\":\"none\"' "
        "style='display:inline-block;width:20px;height:20px;border-radius:50%;"
        "background:#6b7280;color:#fff;font-size:11px;font-weight:bold;"
        "text-align:center;line-height:20px;cursor:pointer;"
        "box-shadow:0 1px 4px rgba(0,0,0,.3);user-select:none'>?</span>"
        "</div>"
    )

    tags_col = ""
    for tag_name in all_tags:
        letter = letter_map[tag_name]
        fgv = f"_fg{all_tags.index(tag_name)}"
        lfs = "9px" if len(letter) > 1 else "12px"
        tags_col += (
            f"<div style='display:flex;align-items:center;gap:4px;margin-bottom:3px'>"
            f"<span id='circle_{letter}' data-jsvar='{fgv}' data-active='0'"
            f" data-color='{tag_color[tag_name]}' data-tag='{tag_name}'"
            f" onclick='toggleTag(this)' title='{tag_name}'"
            f" style='width:24px;height:24px;border-radius:50%;background:#d1d5db;"
            f"color:#9ca3af;font-weight:bold;font-size:{lfs};display:flex;"
            f"align-items:center;justify-content:center;cursor:pointer;user-select:none;flex-shrink:0'>"
            f"{letter}</span>"
            f"<span id='count_{letter}' style='margin-left:auto;color:#6b7280;font-size:11px;min-width:18px;text-align:right'></span>"
            f"</div>"
        )

    _btn_t = "border:none;border-radius:4px;padding:2px 0;cursor:pointer;font-size:11px;flex:1"
    ctrl_col = "<div style='display:flex;flex-direction:column;gap:3px'>"
    for row in [
        [("1/24","1h",False),("3/24","3h",True),("8/24","8h",False)],
        [("1","24h",False),("3","3d",False)],
        [("5","5d",False),("null","*",False)],
    ]:
        ctrl_col += "<div style='display:flex;gap:3px'>"
        for _d, _lbl, _act in row:
            bg = "#6b7280" if _act else "#e5e7eb"
            fc = "#fff" if _act else "#374151"
            ctrl_col += (
                f"<button class='tf-btn' onclick='setTimeFilter({_d},this)'"
                f" style='background:{bg};color:{fc};{_btn_t}'>{_lbl}</button>"
            )
        ctrl_col += "</div>"
    for i, row in enumerate([
        [("10","10m",False),("30","30m",False)],
        [("100","100m",False),("Infinity","∞",True)],
    ]):
        mt = "margin-top:7px;" if i == 0 else ""
        ctrl_col += f"<div style='display:flex;gap:3px;{mt}'>"
        for _val, _lbl, _active in row:
            bg = "#6b7280" if _active else "#e5e7eb"
            fc = "#fff" if _active else "#374151"
            ctrl_col += (
                f"<button class='ac-btn' onclick='setAccThreshold({_val},this)'"
                f" style='background:{bg};color:{fc};{_btn_t}'>{_lbl}</button>"
            )
        ctrl_col += "</div>"
    ctrl_col += (
        "<button id='btn-paths' onclick='togglePaths(this)'"
        " style='background:#e5e7eb;color:#374151;border:none;border-radius:4px;"
        "padding:2px 0;cursor:pointer;font-size:11px;width:100%;margin-top:4px'>vect</button>"
        "</div>"
    )

    help_panel = (
        "<div id='help-panel' style='display:none;position:fixed;bottom:290px;left:30px;"
        "z-index:1100;background:#1f2937;color:#f9fafb;font-size:11px;line-height:1.7;"
        "padding:14px 18px;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.4);"
        "font-family:sans-serif;max-width:320px'>"
        "<b style='font-size:12px'>Controls</b>"
        "<div style='margin-top:8px'>"
        "<b>Tags</b> (colored circles) — click to show/hide a tag on the map<br>"
        "<b>Aggr / Crown / BT</b> — click to filter markers by location status<br>"
        "<b>vect</b> — toggle path lines between consecutive points<br>"
        "<b>1h 3h 8h 24h 3d 5d *</b> — time window filter; 3d/5d/all load data on demand<br>"
        "</div>"
        "<div style='margin-top:8px'>"
        "<b>Marker</b> — click to open popup with detail (tag, time, accuracy, status)<br>"
        "<b>Marker dbl-click</b> — show/hide accuracy circle (radius = accuracy_m)<br>"
        "<b>Last marker</b> — white letter instead of grey = most recent fix for that tag<br>"
        "</div>"
        "<div style='margin-top:8px'>"
        "<b>Centroid</b> — dashed pink circle, drawn automatically when a tag is visible "
        "with ≥ 2 points in the selected window; weighted by 1/acc²; outliers > 500 m excluded; "
        "dbl-click the pink marker to see point count and precision<br>"
        "<b>acc ≤</b> — pre-filter points by accuracy before centroid computation; "
        "10m/30m/100m/∞ (default ∞ = no filter)<br>"
        "</div>"
        "<div style='margin-top:8px'>"
        "<b>Rotation</b> — drag the compass control or use two-finger rotate on touch<br>"
        "<b>Scale</b> — metric ruler in the bottom-right corner<br>"
        "</div>"
        "<div style='margin-top:10px;text-align:right'>"
        "<span onclick='document.getElementById(\"help-panel\").style.display=\"none\"' "
        "style='cursor:pointer;color:#93c5fd;font-size:11px'>close ✕</span>"
        "</div>"
        "</div>"
    )

    return (
        f"<div id='legend-panel' style='position:fixed;bottom:290px;left:30px;z-index:1000;background:#fff;"
        f"padding:10px 14px;border-radius:8px;box-shadow:0 2px 6px rgba(0,0,0,.3);"
        f"font-family:sans-serif;font-size:12px;display:flex;gap:14px'>"
        f"<div>{status_col}</div>"
        f"<div style='border-left:1px solid #e5e7eb;padding-left:14px'>{tags_col}</div>"
        f"<div style='border-left:1px solid #e5e7eb;padding-left:14px'>{ctrl_col}</div>"
        f"</div>"
        f"{help_panel}"
        f"<div id='status-tip' style='display:none;position:fixed;z-index:2000;"
        f"background:#1f2937;color:#f9fafb;font-size:11px;padding:5px 8px;"
        f"border-radius:5px;max-width:220px;pointer-events:none;line-height:1.4'></div>"
    )


_SSE_JS = """
(function(){
  var _sse=new EventSource('/events');
  _sse.addEventListener('update',function(ev){
    var incoming=JSON.parse(ev.data);
    var known=new Set(_markerEntries.map(function(m){return m.tag+'|'+m.t;}));
    var fresh=incoming.filter(function(x){return x.lat!==undefined&&!known.has(x.tag+'|'+x.location_time);});
    if(!fresh.length)return;
    var prevLast={};Object.keys(_lastByTag).forEach(function(t){prevLast[t]=_lastByTag[t];});
    fresh.forEach(function(x){
      if(!_lastByTag[x.tag]||x.location_time>_lastByTag[x.tag])_lastByTag[x.tag]=x.location_time;
    });
    Object.keys(prevLast).forEach(function(tag){
      if(_lastByTag[tag]!==prevLast[tag]){
        _markerEntries.forEach(function(me){
          if(me.tag===tag&&me.t===prevLast[tag]&&me.v&&me.v._icon){
            me.v.setIcon(L.divIcon({html:_mkIcon(_tagMeta[tag],{status:me.st},false),iconSize:[28,28],iconAnchor:[14,14],className:''}));
          }
        });
      }
    });
    _addEntries(fresh,_lastByTag,false);
    _applyVisibility();
    var polledAts=fresh.map(function(x){return x.polled_at||'';}).filter(Boolean);
    if(polledAts.length){
      var lp=polledAts.sort().pop();
      var el=document.getElementById('last-poll-time');
      if(el){
        var d=new Date(lp);
        el.innerHTML=d.toLocaleDateString('it-IT')+'<br>'+d.toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'});
      }
    }
  });
  _sse.onerror=function(){console.warn('[SSE] disconnected');};
})();
"""


def render_html(data_24h: dict, all_tags: list, tag_color: dict, live: bool = False) -> str:
    all_flat = [e for entries in data_24h.values() for e in entries]
    if not all_flat:
        print("No geo entries found in the last 24h.")
        sys.exit(0)

    center_lat = sum(e["lat"] for e in all_flat) / len(all_flat)
    center_lon = sum(e["lon"] for e in all_flat) / len(all_flat)

    last_by_tag = {
        tag: max(entries, key=lambda e: e["location_time"])["location_time"]
        for tag, entries in data_24h.items() if entries
    }

    entries_24h = []
    for tag in all_tags:
        entries_24h.extend(data_24h.get(tag, []))

    fg_init = "\n".join(f"var _fg{i}=L.featureGroup();" for i in range(len(all_tags)))

    letter_map = assign_letters(all_tags)
    tag_meta = {tag: {"color": tag_color[tag], "letter": letter_map[tag], "group": f"_fg{i}"}
                for i, tag in enumerate(all_tags)}

    compass_svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>"
        "<polygon points='12,3 15,13 12,11 9,13' fill='%23dc2626'/>"
        "<polygon points='12,21 15,11 12,13 9,11' fill='%236b7280'/>"
        "</svg>"
    )

    dynamic_js = (
        "var _map=L.map('map',{center:["
        + str(round(center_lat, 6)) + "," + str(round(center_lon, 6))
        + "],zoom:14,maxZoom:19,doubleClickZoom:false,rotate:true,"
        + "rotateControl:{closeOnZeroBearing:false},touchRotate:true,bearingSnap:0});\n"
        + "L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',"
        + "{attribution:'© <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a>',maxZoom:19}"
        + ").addTo(_map);\n"
        + fg_init + "\n"
        + "var _tagMeta=" + json.dumps(tag_meta, separators=(',', ':')) + ";\n"
        + "var _lastByTag=" + json.dumps(last_by_tag, separators=(',', ':')) + ";\n"
        + "var _raw24h=" + json.dumps(entries_24h, separators=(',', ':')) + ";\n"
        + "var _SB=" + json.dumps(STATUS_BORDER, separators=(',', ':')) + ";\n"
        + "var _DB='" + DEFAULT_BORDER + "';\n"
        + "var _markerEntries=[],_segEntries=[],_accCircles={},_hoverCircle=null,_accChart=null;\n"
        + "var _currentMaxAge=3/24,_pathsVisible=false,_visLock=false,_visTimer=null,_addingStationary=false;\n"
        + "var _accThreshold=Infinity;\n"
        + "var _statusEnabled={'LAST_KNOWN':false,'CROWDSOURCED':false,'AGGREGATED':true};\n"
        + "var _extLoaded=false,_extLoading=false;\n"
    )

    init_js = (
        "L.control.scale({imperial:false,position:'bottomright'}).addTo(_map);\n"
        + "var _centroidPane=_map.createPane('centroidPane');_centroidPane.style.zIndex=450;\n"
        + "_map.on('layeradd',function(e){"
        + "if(_addingStationary)return;"
        + "if(e.layer&&(e.layer instanceof L.Tooltip||e.layer instanceof L.Popup))return;"
        + "clearTimeout(_visTimer);"
        + "_visTimer=setTimeout(_applyVisibility,20);});\n"
        + "_addEntries(_raw24h,_lastByTag,false);\n"
        + "_applyVisibility();\n"
    )

    polled_vals = [e.get("polled_at", "") for e in all_flat if e.get("polled_at")]
    last_polled_at = max(polled_vals) if polled_vals else ""
    legend = _build_legend(all_tags, tag_color, letter_map, last_polled_at)

    return (
        "<!DOCTYPE html>\n<html>\n<head>\n"
        "<meta charset='utf-8'/>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'/>\n"
        "<title>Tag Map</title>\n"
        "<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.3/dist/leaflet.css'/>\n"
        "<script src='https://unpkg.com/leaflet@1.9.3/dist/leaflet.js'></script>\n"
        "<link rel='stylesheet' href='https://unpkg.com/leaflet-rotate@0.2.8/dist/leaflet-rotate-src.css'/>\n"
        "<script src='https://unpkg.com/leaflet-rotate@0.2.8/dist/leaflet-rotate-src.js'></script>\n"
        "<script src='https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js'></script>\n"
        "<style>\n"
        "html,body{margin:0;padding:0;height:100%;overflow:hidden;}\n"
        "#map{position:absolute;top:0;left:0;right:0;bottom:280px;}\n"
        "#chart-wrap{position:absolute;bottom:0;left:0;right:0;height:280px;"
        "background:#111827;box-sizing:border-box;padding:6px 12px;}\n"
        "#acc-chart{width:100%;height:100%;}\n"
        "@media(max-width:768px),(orientation:landscape) and (max-height:500px){"
        "#map{bottom:0!important;}"
        "#chart-wrap{display:none!important;}"
        "#legend-panel{bottom:20px!important;}"
        "#help-panel{bottom:50px!important;}"
        "}\n"
        f".leaflet-control-rotate-arrow{{background-image:url(\"data:image/svg+xml,{compass_svg}\");"
        "background-size:60% 60%;background-repeat:no-repeat;background-position:center;"
        "background-color:transparent;}\n"
        "</style>\n"
        "</head>\n<body>\n"
        "<div id='map'></div>\n"
        "<div id='chart-wrap'><canvas id='acc-chart'></canvas></div>\n"
        + legend + "\n"
        "<script>\n"
        + dynamic_js
        + _STATIC_JS
        + init_js
        + (_SSE_JS if live else "")
        + "</script>\n"
        "</body>\n</html>"
    )


def main():
    parser = argparse.ArgumentParser(description="Generate map from position archive")
    parser.add_argument("--latest", action="store_true",
                        help="Show only the most recent position per tag")
    parser.add_argument("--out", default=OUTPUT_PATH, help="Output HTML file path")
    args = parser.parse_args()

    by_tag_all = load_entries()

    _priority = set(TAG_RENAME.values())
    all_tags = sorted(by_tag_all.keys(), key=lambda n: (n not in _priority, n))
    tag_color = {name: TAG_COLORS[i % len(TAG_COLORS)] for i, name in enumerate(all_tags)}

    if args.latest:
        data_24h = {tag: [max(entries, key=lambda e: e["location_time"])]
                    for tag, entries in by_tag_all.items()}
        extended = []
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        data_24h, extended = split_entries(by_tag_all, cutoff)

    html = render_html(data_24h, all_tags, tag_color)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    if not args.latest:
        with open(EXTENDED_JSON_PATH, "w") as f:
            json.dump(extended, f, separators=(',', ':'))
        print(f"Extended data: {len(extended)} entries → {EXTENDED_JSON_PATH}")

    print(f"Map saved to: {args.out}")
    print(f"Open with: open {args.out}")


if __name__ == "__main__":
    main()
