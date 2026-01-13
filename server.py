import json
import os
import httpx
import logging
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Form, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
import filters

# --- LOGGING SETUP ---
# We force the logger to flush immediately for better real-time debugging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("xtream-proxy")

app = FastAPI()

CACHE_FILE = "local_cache.json"
# Global variable to track status
JOB_STATUS = {"state": "IDLE", "message": "Ready", "last_run": None}

def load_config():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "config.json")
    with open(config_path, "r") as f:
        return json.load(f)

def save_config(new_config):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "config.json")
    with open(config_path, "w") as f:
        json.dump(new_config, f, indent=4)

def get_base_xtream_url():
    return load_config()["xtream"]["base_url"]

def get_xtream_url(action: str):
    config = load_config()
    base = config["xtream"]["base_url"]
    user = config["xtream"]["username"]
    pwd = config["xtream"]["password"]
    return f"{base}/player_api.php?username={user}&password={pwd}&action={action}"

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# -----------------------------------------------------------------------------
# BACKGROUND WORKER
# -----------------------------------------------------------------------------
async def perform_refresh():
    global JOB_STATUS
    JOB_STATUS["state"] = "RUNNING"
    JOB_STATUS["message"] = "Starting download..."
    
    tasks = {
        "live": ("get_live_streams", "get_live_categories"),
        "vod":  ("get_vod_streams", "get_vod_categories"),
        "series": ("get_series", "get_series_categories")
    }
    final_data = {}
    final_categories = {}
    
    try:
        async with httpx.AsyncClient(timeout=300.0) as client: # Increased timeout
            for key, (stream_action, cat_action) in tasks.items():
                logger.info(f"Downloading {key}...")
                JOB_STATUS["message"] = f"Downloading {key}..."
                
                # 1. Categories
                cat_resp = await client.get(get_xtream_url(cat_action))
                # Run CPU-heavy JSON parsing in a thread to prevent blocking
                raw_cats = await asyncio.to_thread(json.loads, cat_resp.content.decode('utf-8', errors='replace'))
                cat_map = {c['category_id']: c for c in raw_cats}
                
                # 2. Streams
                stream_resp = await client.get(get_xtream_url(stream_action))
                raw_streams = await asyncio.to_thread(json.loads, stream_resp.content.decode('utf-8', errors='replace'))
                
                # 3. Filtering
                logger.info(f"Filtering {key}...")
                JOB_STATUS["message"] = f"Filtering {key}..."
                
                for stream in raw_streams:
                    c_id = stream.get("category_id")
                    if c_id in cat_map:
                        stream["category_name"] = cat_map[c_id].get("category_name", "")
                    
                filtered_streams = filters.apply_filters(raw_streams, key)
                final_data[key] = filtered_streams
                
                kept_cat_ids = set(s.get("category_id") for s in filtered_streams)
                final_categories[key] = [c for c in raw_cats if c['category_id'] in kept_cat_ids]

        # Save to Disk
        JOB_STATUS["message"] = "Saving to disk..."
        final_cache = {
            "meta": { "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S") },
            "data": final_data,
            "categories": final_categories
        }
        
        await asyncio.to_thread(
            lambda: json.dump(final_cache, open(CACHE_FILE, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
        )
        
        logger.info("Refresh complete!")
        JOB_STATUS["state"] = "IDLE"
        JOB_STATUS["message"] = "Last run successful"
        JOB_STATUS["last_run"] = datetime.now().strftime("%H:%M:%S")

    except Exception as e:
        logger.error(f"Refresh failed: {e}")
        JOB_STATUS["state"] = "ERROR"
        JOB_STATUS["message"] = str(e)

# -----------------------------------------------------------------------------
# ENDPOINTS
# -----------------------------------------------------------------------------

@app.get("/refresh_cache")
async def refresh_cache_endpoint(background_tasks: BackgroundTasks):
    """
    Triggers the refresh in the background and returns immediately.
    """
    if JOB_STATUS["state"] == "RUNNING":
        return {"status": "Busy", "message": "Refresh already in progress"}
    
    background_tasks.add_task(perform_refresh)
    return {"status": "Started", "message": "Refresh started in background. Check /status for progress."}

@app.get("/status")
def get_status():
    """
    Check if the refresh is still running.
    """
    return JOB_STATUS


@app.get("/config", response_class=HTMLResponse)
def config_page():
    config = load_config()
    return f"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Xtream Proxy Config</title>
<style>
  body {{ font-family: sans-serif; margin: 0; padding: 1rem; background:#0b0c10; color:#eee; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 0.5rem; }}
  form {{ display: flex; flex-direction: column; gap: 0.75rem; max-width: 480px; margin-top: 1rem; }}
  label {{ font-size: 0.9rem; }}
  input, textarea {{ width: 100%; padding: 0.5rem; border-radius: 4px; border: 1px solid #333; background:#1f2833; color:#eee; box-sizing: border-box; }}
  button {{ padding: 0.6rem 1rem; border-radius: 4px; border: none; background:#45a29e; color:#fff; font-weight: 600; }}
  button:active {{ transform: scale(0.98); }}
  .row {{ display:flex; gap:0.5rem; }}
  .row > div {{ flex:1; }}
  .note {{ font-size:0.8rem; color:#aaa; }}
  .card {{ background:#141923; padding:1rem; border-radius:6px; margin-top:0.75rem; }}
</style>
</head>
<body>
  <h1>Xtream Proxy Config</h1>
  <div class="card">
    <div class="note">Edit values and tap <b>Save</b>. Filters are comma‑separated prefixes like <code>EN,UK,IE</code>.</div>
  </div>
  <form method="post" action="/update_config_form">
    <div>
      <label>Base URL</label>
      <input name="base_url" value="{config['xtream']['base_url']}" />
    </div>
    <div class="row">
      <div>
        <label>Username</label>
        <input name="username" value="{config['xtream']['username']}" />
      </div>
      <div>
        <label>Password</label>
        <input name="password" type="password" value="{config['xtream']['password']}" />
      </div>
    </div>
    <div class="card">
      <label>Live Filters (prefixes)</label>
      <input name="live_filters" value="{','.join(config['filters'].get('live', []))}" />
      <label>VOD Filters (prefixes)</label>
      <input name="vod_filters" value="{','.join(config['filters'].get('vod', []))}" />
      <label>Series Filters (prefixes)</label>
      <input name="series_filters" value="{','.join(config['filters'].get('series', []))}" />
    </div>
    <button type="submit">Save</button>
    <div style="display:flex;gap:0.5rem;margin-top:0.5rem;">
  <a href="/refresh_cache" style="flex:1;padding:0.6rem;border-radius:4px;background:#66fcf1;color:#0b0c10;text-align:center;text-decoration:none;font-weight:600;">Refresh Cache</a>
  <a href="/status" style="flex:1;padding:0.6rem;border-radius:4px;background:#1f2833;color:#eee;text-align:center;text-decoration:none;font-weight:600;">Status</a>
</div>
<div class="note" style="margin-top:0.75rem;">Tap <b>Refresh Cache</b> after saving to rebuild filtered lists.</div>
  </form>
</body>
</html>
    """

@app.post("/update_config_form")
def update_config_form(
    base_url: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    live_filters: str = Form(""),
    vod_filters: str = Form(""),
    series_filters: str = Form(""),
):
    config = load_config()
    config["xtream"]["base_url"] = base_url.strip()
    config["xtream"]["username"] = username.strip()
    config["xtream"]["password"] = password.strip()
    config["filters"]["live"] = [x.strip() for x in live_filters.split(",") if x.strip()]
    config["filters"]["vod"] = [x.strip() for x in vod_filters.split(",") if x.strip()]
    config["filters"]["series"] = [x.strip() for x in series_filters.split(",") if x.strip()]
    save_config(config)
    logger.info("Configuration updated via UI form")
    # Simple redirect back to the form
    return HTMLResponse(
        '<meta http-equiv="refresh" content="0;url=/config" />', status_code=200
    )

@app.get("/update_config")
def update_config(username: Optional[str] = None, password: Optional[str] = None, live_filters: Optional[str] = None):
    config = load_config()
    updated = False
    if username: 
        config["xtream"]["username"] = username
        updated = True
    if password: 
        config["xtream"]["password"] = password
        updated = True
    if live_filters:
        config["filters"]["live"] = [x.strip() for x in live_filters.split(",") if x.strip()]
        updated = True
        
    if updated:
        save_config(config)
        logger.info("Config updated")
        return {"status": "Updated", "config": config}
    return {"status": "No changes"}

# ... (Include your Redirects and Player API endpoints here exactly as before) ...
# I am omitting them for brevity, but keep the `player_api`, `redirect_live`, etc. from the previous successful version.
# Make sure to include them below!

# --- PASTE REDIRECTS AND PLAYER_API HERE FROM PREVIOUS STEP ---

@app.get("/live/{username}/{password}/{stream_id}.ts")
async def redirect_live(username: str, password: str, stream_id: str):
    real_url = f"{get_base_xtream_url()}/live/{username}/{password}/{stream_id}.ts"
    return RedirectResponse(real_url)

@app.get("/movie/{username}/{password}/{stream_id}.{ext}")
async def redirect_movie(username: str, password: str, stream_id: str, ext: str):
    real_url = f"{get_base_xtream_url()}/movie/{username}/{password}/{stream_id}.{ext}"
    return RedirectResponse(real_url)

@app.get("/series/{username}/{password}/{stream_id}.{ext}")
async def redirect_series(username: str, password: str, stream_id: str, ext: str):
    real_url = f"{get_base_xtream_url()}/series/{username}/{password}/{stream_id}.{ext}"
    return RedirectResponse(real_url)

@app.get("/player_api.php")
async def player_api(request: Request, username: str = None, password: str = None, action: Optional[str] = None, vod_id: Optional[int] = None, series_id: Optional[int] = None):   # ... (Paste the robust player_api code from the previous turn) ...
    config = load_config()
    if username != config["xtream"]["username"] or password != config["xtream"]["password"]:
        return JSONResponse(content={"user_info": {"auth": 0}}, status_code=401)
    if not action:
        host = request.url.hostname if request.url.hostname else "localhost"
        port = str(request.url.port) if request.url.port else "8000"
        return {
            "user_info": {"username": username, "password": password, "message": "Logged In", "auth": 1, "status": "Active", "exp_date": "1999999999", "created_at": "1600000000", "max_connections": "10", "allowed_output_formats": ["m3u8", "ts", "rtmp"]},
            "server_info": {"url": f"http://{host}", "port": port, "https_port": port, "server_protocol": "http", "rtmp_port": "8000", "timezone": "Europe/London", "timestamp_now": int(datetime.now().timestamp()), "time_now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "process": 1}
        }
    cache = load_cache()
    if not cache: return JSONResponse(content={"error": "Cache not built"}, status_code=503)
    data = cache.get("data", {})
    categories = cache.get("categories", {})

    # NEW: VOD info proxy
    if action == "get_vod_info" and vod_id is not None:
        # try to find in cached VOD list for quick basic info
        vod_list = data.get("vod", [])
        basic = next((v for v in vod_list if int(v.get("stream_id", -1)) == vod_id), None)
        # always fetch full metadata from upstream (title, description, backdrops, etc.)
        async with httpx.AsyncClient(timeout=60.0) as client:
            upstream = await client.get(get_xtream_url(f"get_vod_info&vod_id={vod_id}"))
            full_info = upstream.json()
        if basic:
            full_info.setdefault("basic_info", basic)
        return JSONResponse(full_info, media_type="application/json; charset=utf-8")

    # NEW: Series info proxy
    elif action == "get_series_info" and series_id is not None:
        series_list = data.get("series", [])
        basic = next((s for s in series_list if int(s.get("series_id", -1)) == series_id), None)
        async with httpx.AsyncClient(timeout=60.0) as client:
            upstream = await client.get(get_xtream_url(f"get_series_info&series_id={series_id}"))
            full_info = upstream.json()
        if basic:
            full_info.setdefault("basic_info", basic)
        return JSONResponse(full_info, media_type="application/json; charset=utf-8")
    
    elif action == "get_live_streams": return JSONResponse(data.get("live", []), media_type="application/json; charset=utf-8")
    elif action == "get_live_categories": return JSONResponse(categories.get("live", []), media_type="application/json; charset=utf-8")
    elif action == "get_vod_streams": return JSONResponse(data.get("vod", []), media_type="application/json; charset=utf-8")
    elif action == "get_vod_categories": return JSONResponse(categories.get("vod", []), media_type="application/json; charset=utf-8")
    elif action == "get_series": return JSONResponse(data.get("series", []), media_type="application/json; charset=utf-8")
    elif action == "get_series_categories": return JSONResponse(categories.get("series", []), media_type="application/json; charset=utf-8")
    return []

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
