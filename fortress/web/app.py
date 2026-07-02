"""FastAPI dashboard — live event feed, metrics, rules management."""

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fortress.core.camera_registry import CameraStatus

if TYPE_CHECKING:
    from fortress.core.event_bus import EventBus, Event
    from fortress.core.database import Database
    from fortress.core.metrics import Metrics

logger = logging.getLogger("fortress.web")

SNAPSHOT_DIR = Path.home() / ".fortress" / "snapshots"


def _find_camera_plugin():
    """Find camera plugin from app state."""
    # This will be set via app.state.camera_plugin after startup
    return getattr(_find_camera_plugin, '_instance', None)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Fortress V2</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0a0f;--surface:#12121a;--border:#1e1e2e;--accent:#00d4ff;--text:#e0e0e0;--dim:#666;--ok:#00ff88;--warn:#ffaa00;--err:#ff4444}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent)}
.header{padding:12px 20px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;background:var(--surface)}
.header h1{font-size:16px;color:var(--accent)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--dim)}
.dot.on{background:var(--ok);box-shadow:0 0 6px var(--ok)}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:12px 20px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px}
.card h3{font-size:11px;color:var(--dim);text-transform:uppercase;margin-bottom:4px}
.card .val{font-size:24px;font-weight:700;color:var(--accent)}
.section{padding:8px 20px}
.section h2{font-size:13px;color:var(--dim);margin-bottom:8px;display:flex;align-items:center;gap:8px}
.cam-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.cam{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}
.cam-head{padding:8px 12px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--border)}
.cam-head .name{font-size:12px;font-weight:600;color:var(--accent);flex:1}
.cam-head .badge{font-size:10px;padding:2px 6px;border-radius:4px;background:var(--ok);color:#000}
.cam-head .badge.off{background:var(--dim)}
.cam-head .badge.err{background:var(--err)}
.cam video,.cam img{width:100%;display:block;background:#000;min-height:180px}
.cam-foot{padding:6px 12px;font-size:11px;color:var(--dim);display:flex;justify-content:space-between}
.events{max-height:300px;overflow-y:auto;padding:0 20px}
.ev{padding:6px 12px;border-bottom:1px solid var(--border);font-size:12px;display:flex;gap:8px}
.ev .t{color:var(--dim);min-width:60px}
.ev .tp{color:var(--accent);font-weight:600;min-width:140px}
.ev.s1{border-left:3px solid var(--warn)}
.ev.s2{border-left:3px solid var(--err)}
.toolbar{padding:12px 20px;border-top:1px solid var(--border);background:var(--surface);display:flex;gap:8px;flex-wrap:wrap}
.toolbar button{background:var(--accent);color:#000;border:none;border-radius:6px;padding:8px 16px;cursor:pointer;font-size:12px;font-weight:600}
.toolbar button:hover{opacity:.85}
.toolbar button.secondary{background:var(--border);color:var(--text)}
.modal{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:100;align-items:center;justify-content:center}
.modal.show{display:flex}
.modal-box{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;width:400px;max-width:90vw}
.modal-box h2{color:var(--accent);margin-bottom:16px;font-size:16px}
.modal-box label{display:block;font-size:12px;color:var(--dim);margin-bottom:4px;margin-top:12px}
.modal-box input,.modal-box select{width:100%;padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px}
.modal-box .btns{margin-top:20px;display:flex;gap:8px;justify-content:flex-end}
.modal-box .btns button{padding:8px 20px;border:none;border-radius:6px;cursor:pointer;font-size:13px}
.btn-ok{background:var(--accent);color:#000}
.btn-cancel{background:var(--border);color:var(--text)}
</style>
</head>
<body>
<div class="header">
  <div class="dot" id="dot"></div>
  <h1>Fortress V2</h1>
  <span style="color:var(--dim);font-size:12px" id="uptime"></span>
</div>

<div class="metrics">
  <div class="card"><h3>Events</h3><div class="val" id="m-events">0</div></div>
  <div class="card"><h3>Actions</h3><div class="val" id="m-actions">0</div></div>
  <div class="card"><h3>Rules</h3><div class="val" id="m-rules">0%</div></div>
  <div class="card"><h3>LLM</h3><div class="val" id="m-latency">0ms</div></div>
</div>

<div class="section">
  <h2>Cameras <button onclick="showAddCam()" style="background:var(--accent);color:#000;border:none;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:11px">+ Add</button></h2>
  <div class="cam-grid" id="cams"></div>
</div>

<div class="section">
  <h2>Events</h2>
  <div class="events" id="evts"></div>
</div>

<div class="toolbar">
  <button onclick="sendTest()">Test Event</button>
  <button onclick="location.reload()">Refresh</button>
</div>

<!-- Add Camera Modal -->
<div class="modal" id="addCamModal">
  <div class="modal-box">
    <h2>Add Camera</h2>
    <label>Name</label>
    <input id="cam-name" placeholder="Front Door">
    <label>Type</label>
    <select id="cam-type">
      <option value="rtsp">RTSP (IP camera)</option>
      <option value="remote">Remote (WebSocket)</option>
    </select>
    <label>URL</label>
    <input id="cam-url" placeholder="rtsp://192.168.1.100:554/stream1">
    <div class="btns">
      <button class="btn-cancel" onclick="hideAddCam()">Cancel</button>
      <button class="btn-ok" onclick="addCam()">Add</button>
    </div>
  </div>
</div>

<script>
let T='';
const P=new URLSearchParams(location.search);
T=P.get('token')||'';

// Auto-fetch token if not in URL
if(!T){
  fetch('/api/auth/master').then(r=>r.json()).then(d=>{
    if(d.token){T=d.token;history.replaceState(null,'','?token='+T);init();}
  }).catch(()=>{document.body.innerHTML='<h2 style="color:#ff4444;padding:40px">Cannot connect to Fortress</h2>';});
}else{init();}

function init(){
  loadCams();
  loadEvents();
  connectWS();
  setInterval(loadCams,5000);
  setInterval(loadEvents,5000);
}

function api(u,o){return fetch(u+'?token='+T,o).then(r=>r.json());}

function connectWS(){
  const ws=new WebSocket('ws://'+location.host+'/ws?token='+encodeURIComponent(T));
  ws.onopen=()=>document.getElementById('dot').classList.add('on');
  ws.onclose=()=>{document.getElementById('dot').classList.remove('on');setTimeout(connectWS,2000)};
  ws.onmessage=e=>{
    const d=JSON.parse(e.data);
    if(d.type==='metrics'){
      document.getElementById('m-events').textContent=d.events;
      document.getElementById('m-actions').textContent=d.actions;
      document.getElementById('m-rules').textContent=d.rules_pct+'%';
      document.getElementById('m-latency').textContent=d.avg_latency_ms+'ms';
      document.getElementById('uptime').textContent=Math.round(d.uptime_seconds/60)+'m uptime';
    }
    if(d.type==='event')addEvt(d);
  };
}

function loadCams(){
  api('/api/cameras').then(d=>{
    const el=document.getElementById('cams');
    if(!d.cameras||!d.cameras.length){el.innerHTML='<div style="color:var(--dim);font-size:12px;padding:20px">No cameras. Click "+ Add" to connect one.</div>';return;}
    el.innerHTML='';
    d.cameras.forEach(c=>{
      const st=c.status==='online'?'on':c.status==='error'?'err':'';
      const card=document.createElement('div');
      card.className='cam';
      card.innerHTML=`<div class="cam-head"><span class="badge ${st}">${c.status}</span><span class="name">${c.name}</span><span style="color:var(--dim);font-size:10px">${c.type}</span></div><img id="img-${c.id}" src="/api/cameras/${c.id}/frame?token=${T}" onerror="this.style.background='#1a1a2e'" style="min-height:180px"><div class="cam-foot"><span>${c.resolution||'--'}</span><span>FPS: ${c.fps||0}</span></div>`;
      el.appendChild(card);
      // Auto-refresh frame
      setInterval(()=>{const img=document.getElementById('img-'+c.id);if(img)img.src='/api/cameras/'+c.id+'/frame?token='+T+'&_t='+Date.now();},1000);
    });
  });
}

function loadEvents(){
  api('/api/events?limit=20').then(d=>{
    const el=document.getElementById('evts');
    if(!d.events||!d.events.length){el.innerHTML='<div style="color:var(--dim);font-size:12px;padding:12px">No events yet</div>';return;}
    el.innerHTML='';
    d.events.forEach(e=>{
      const t=new Date(e.timestamp*1000).toLocaleTimeString();
      const s=e.severity>0?' s'+e.severity:'';
      el.innerHTML+=`<div class="ev${s}"><span class="t">${t}</span><span class="tp">${e.type}</span><span>${e.source}</span></div>`;
    });
  });
}

function addEvt(e){
  const el=document.getElementById('evts');
  const t=new Date(e.timestamp*1000).toLocaleTimeString();
  const s=e.severity>0?' s'+e.severity:'';
  el.innerHTML=`<div class="ev${s}"><span class="t">${t}</span><span class="tp">${e.type}</span><span>${e.source}</span></div>`+el.innerHTML;
  if(el.children.length>50)el.lastChild.remove();
}

function sendTest(){api('/api/test-event',{method:'POST'});}

function showAddCam(){document.getElementById('addCamModal').classList.add('show');}
function hideAddCam(){document.getElementById('addCamModal').classList.remove('show');}

function addCam(){
  const name=document.getElementById('cam-name').value||'Camera';
  const type=document.getElementById('cam-type').value;
  const url=document.getElementById('cam-url').value;
  api('/api/cameras/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,type,url})}).then(d=>{
    hideAddCam();
    loadCams();
    document.getElementById('cam-name').value='';
    document.getElementById('cam-url').value='';
  });
}
</script>
</body>
</html>"""


def create_dashboard_app(event_bus=None, db=None, metrics=None, auth=None) -> FastAPI:
    """Create FastAPI dashboard app with authentication."""
    app = FastAPI(title="Fortress V2 Dashboard")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _state = {"bus": event_bus, "db": db, "metrics": metrics, "auth": auth, "ws_clients": set()}

    # --- Auth dependency ---
    async def require_auth(request: Request):
        """Validate Bearer token for all API endpoints."""
        if not _state["auth"]:
            return  # No auth configured = open (dev mode)
        auth_header = request.headers.get("Authorization", "")
        token_param = request.query_params.get("token", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        elif token_param:
            token = token_param
        client_ip = request.client.host if request.client else ""
        if not _state["auth"].validate(token, client_ip):
            raise HTTPException(status_code=401, detail="Invalid or missing token")

    from fastapi import HTTPException

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return DASHBOARD_HTML

    @app.get("/api/status")
    async def status(auth=Depends(require_auth)):
        snap = _state["metrics"].snapshot() if _state["metrics"] else {}
        db_stats = await _state["db"].get_stats() if _state["db"] else {}
        return {"metrics": snap, "database": db_stats, "bus": _state["bus"].stats() if _state["bus"] else {}}

    @app.get("/api/events")
    async def list_events(limit: int = 50, auth=Depends(require_auth)):
        events = await _state["db"].get_recent_events(limit) if _state["db"] else []
        return {"events": events}

    @app.post("/api/test-event")
    async def test_event(auth=Depends(require_auth)):
        """Emit a test event for demo purposes."""
        from fortress.core.event_bus import Event
        event = Event(type="test.ping", source="dashboard", payload={"message": "test from UI"})
        if _state["bus"]:
            await _state["bus"].emit(event)
        return {"status": "ok"}

    @app.get("/api/snapshots")
    async def list_snapshots(auth=Depends(require_auth)):
        """List recent snapshots."""
        from fortress.plugins.camera import SNAPSHOT_DIR
        if not SNAPSHOT_DIR.exists():
            return {"snapshots": []}
        files = sorted(SNAPSHOT_DIR.glob("*.jpg"), key=lambda f: f.stat().st_mtime, reverse=True)
        return {"snapshots": [{"name": f.name, "size": f.stat().st_size, "time": f.stat().st_mtime} for f in files[:20]]}

    @app.get("/api/snapshots/{name}")
    async def get_snapshot(name: str, auth=Depends(require_auth)):
        """Get a snapshot image."""
        from fastapi.responses import FileResponse
        from fortress.plugins.camera import SNAPSHOT_DIR
        path = (SNAPSHOT_DIR / name).resolve()
        if not path.is_relative_to(SNAPSHOT_DIR.resolve()):
            return JSONResponse(status_code=403, detail="Path traversal denied")
        if not path.exists():
            return {"error": "Snapshot not found"}
        return FileResponse(path, media_type="image/jpeg")

    # --- Auth endpoints (no auth required for initial setup) ---
    @app.post("/api/auth/register")
    async def register_token(request: Request, auth=Depends(require_auth)):
        """Register a new camera token. Requires auth."""
        if not _state["auth"]:
            return JSONResponse(status_code=503, detail="Auth not configured")
        body = await request.json()
        name = body.get("name", "unnamed")
        expires_in = body.get("expires_in", 0)
        ip_whitelist = body.get("ip_whitelist", [])
        token = _state["auth"].create_token(name, expires_in, ip_whitelist)
        return {"token": token, "name": name}

    @app.get("/api/auth/tokens")
    async def list_tokens(auth=Depends(require_auth)):
        """List registered tokens. Requires auth."""
        if not _state["auth"]:
            return JSONResponse(status_code=503, detail="Auth not configured")
        return {"tokens": _state["auth"].list_tokens()}

    @app.get("/api/auth/master")
    async def get_master_token(auth=Depends(require_auth)):
        """Get master token. Requires existing auth."""
        if not _state["auth"]:
            return JSONResponse(status_code=503, detail="Auth not configured")
        return {"token": _state["auth"].master_token}

    # --- Camera API ---
    @app.get("/api/cameras")
    async def list_cameras(auth=Depends(require_auth)):
        """List all registered cameras."""
        cam_plugin = _find_camera_plugin() or getattr(app.state, 'camera_plugin', None)
        if not cam_plugin:
            return {"cameras": [], "error": "Camera plugin not running"}
        return {"cameras": cam_plugin.registry.list_all()}

    @app.post("/api/cameras/register")
    async def register_camera(request: Request, auth=Depends(require_auth)):
        """Register a new camera (RTSP or remote)."""
        from fortress.core.camera_registry import CameraType
        body = await request.json()
        name = body.get("name", "Unnamed Camera")
        camera_type = body.get("type", "rtsp")  # "rtsp" or "remote"
        url = body.get("url", "")

        cam_plugin = _find_camera_plugin() or getattr(app.state, 'camera_plugin', None)
        if not cam_plugin:
            return JSONResponse(status_code=503, detail="Camera plugin not running")

        ct = CameraType.RTSP if camera_type == "rtsp" else CameraType.REMOTE
        cam = cam_plugin.registry.register(name, ct, url)

        if ct == CameraType.RTSP:
            # Start capture loop for new RTSP camera
            task = asyncio.create_task(cam_plugin._camera_capture_loop(cam.id))
            cam_plugin._tasks[cam.id] = task

        return {
            "id": cam.id, "name": cam.name, "type": cam.camera_type.value,
            "token": cam.token, "url": cam.url,
        }

    @app.delete("/api/cameras/{camera_id}")
    async def remove_camera(camera_id: str, auth=Depends(require_auth)):
        """Remove a camera."""
        cam_plugin = _find_camera_plugin() or getattr(app.state, 'camera_plugin', None)
        if not cam_plugin:
            return JSONResponse(status_code=503, detail="Camera plugin not running")
        # Cancel the capture task first
        cam_plugin.cancel_camera_task(camera_id)
        if cam_plugin.registry.remove(camera_id):
            return {"status": "removed", "id": camera_id}
        return JSONResponse(status_code=404, detail="Camera not found")

    @app.get("/api/cameras/{camera_id}/frame")
    async def get_camera_frame(camera_id: str, auth=Depends(require_auth)):
        """Get latest JPEG frame from a camera."""
        from fastapi.responses import Response
        cam_plugin = _find_camera_plugin() or getattr(app.state, 'camera_plugin', None)
        if not cam_plugin:
            return JSONResponse(status_code=503, detail="Camera plugin not running")
        frame = cam_plugin.get_frame_for_client(camera_id)
        if frame is None:
            return JSONResponse(status_code=404, detail="No frame available")
        return Response(content=frame, media_type="image/jpeg")

    @app.get("/api/cameras/{camera_id}/snapshots")
    async def list_camera_snapshots(camera_id: str, auth=Depends(require_auth)):
        """List snapshots for a specific camera."""
        cam_dir = SNAPSHOT_DIR / camera_id
        if not cam_dir.exists():
            return {"snapshots": []}
        files = sorted(cam_dir.glob("*.jpg"), key=lambda f: f.stat().st_mtime, reverse=True)
        return {"snapshots": [{"name": f.name, "size": f.stat().st_size} for f in files[:30]]}

    # --- Camera WebSocket (remote cameras push frames here) ---
    @app.websocket("/camera/stream")
    async def camera_stream(ws: WebSocket):
        """Remote cameras push JPEG frames via WebSocket."""
        token = ws.query_params.get("token", "")
        if not _state["auth"]:
            await ws.close(code=4001, detail="Auth not configured")
            return

        cam_plugin = _find_camera_plugin() or getattr(app.state, 'camera_plugin', None)
        if not cam_plugin:
            await ws.close(code=4002, detail="Camera plugin not running")
            return

        # Validate camera token
        cam = cam_plugin.registry.validate_token(token)
        if not cam:
            await ws.close(code=4003, detail="Invalid camera token")
            return

        await ws.accept()
        cam_plugin.registry.update_status(cam.id, CameraStatus.ONLINE)
        logger.info(f"Remote camera connected: {cam.name}")

        try:
            while True:
                data = await ws.receive_bytes()
                cam_plugin.add_remote_frame(cam.id, data)
        except WebSocketDisconnect:
            cam_plugin.registry.update_status(cam.id, CameraStatus.OFFLINE)
            logger.info(f"Remote camera disconnected: {cam.name}")

    # --- Dashboard WebSocket ---
    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        # Authenticate: token in query param ?token=xxx
        token = ws.query_params.get("token", "")
        client_ip = ws.client.host if ws.client else ""
        if _state["auth"] and not _state["auth"].validate(token, client_ip):
            await ws.close(code=4001, reason="Unauthorized")
            return

        await ws.accept()
        _state["ws_clients"].add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            _state["ws_clients"].discard(ws)

    async def broadcast_event(event):
        """Broadcast event to all connected WebSocket clients."""
        msg = json.dumps({
            "type": "event",
            "id": event.id, "type": event.type, "source": event.source,
            "payload": event.payload, "severity": event.severity,
            "timestamp": event.timestamp,
        })
        dead = set()
        for client in _state["ws_clients"]:
            try:
                await client.send_text(msg)
            except Exception:
                dead.add(client)
        _state["ws_clients"] -= dead

        # Broadcast metrics
        if _state["metrics"]:
            snap = _state["metrics"].snapshot()
            snap["type"] = "metrics"
            metrics_msg = json.dumps(snap)
            for client in _state["ws_clients"]:
                try:
                    await client.send_text(metrics_msg)
                except Exception:
                    pass

    # Store broadcast function for external use
    app.state.broadcast_event = broadcast_event

    return app
