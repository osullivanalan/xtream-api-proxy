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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("xtream-proxy")

app = FastAPI()

CACHE_FILE = "local_cache.json"

# --- GLOBAL IN-MEMORY CACHE ---
# We keep data in memory to avoid reading disk on every request
IN_MEMORY_CACHE = None
# We build a fast lookup index {id: item} for VOD and Series
SEARCH_INDEX = {
    "vod": {},
    "series": {}
}

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

# --- OPTIMIZED CACHE LOADING ---
def load_cache_to_memory():
    """Loads JSON from disk into global variables and builds search indexes."""
    global IN_MEMORY_CACHE, SEARCH_INDEX
    if not os.path.exists(CACHE_FILE):
        logger.info("No cache file found on disk.")
        return

    try:
        logger.info("Loading cache from disk into memory...")
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            IN_MEMORY_CACHE = data
            
            # Build Fast Lookup Index for VOD
            # stream_id might be string or int in JSON, we index by int for safety
            vods = data.get("data", {}).get("vod", [])
            SEARCH_INDEX["vod"] = {int(v.get("stream_id")): v for v in vods if v.get("stream_id")}
            
            # Build Fast Lookup Index for Series
            series = data.get("data", {}).get("series", [])
            SEARCH_INDEX["series"] = {int(s.get("series_id")): s for s in series if s.get("series_id")}
            
            logger.info(f"Cache loaded. Indexed {len(SEARCH_INDEX['vod'])} VODs and {len(SEARCH_INDEX['series'])} Series.")
    except Exception as e:
        logger.error(f"Failed to load cache: {e}")

# Load cache on startup
@app.on_event("startup")
async def startup_event():
    load_cache_to_memory()

# -----------------------------------------------------------------------------
# BACKGROUND WORKER
# -----------------------------------------------------------------------------
async def perform_refresh():
    global JOB_STATUS
    JOB_STATUS["state"] = "RUNNING"
    JOB_STATUS["message"] = "Starting download..."
    
    tasks = {
        "live": ("get_live_streams", "get_live_categories"),
        "vod": ("get_vod_streams", "get_vod_categories"),
        "series": ("get_series", "get_series_categories")
    }

    final_data = {}
    final_categories = {}

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            for key, (stream_action, cat_action) in tasks.items():
                logger.info(f"Downloading {key}...")
                JOB_STATUS["message"] = f"Downloading {key}..."

                # 1. Categories
                cat_resp = await client.get(get_xtream_url(cat_action))
                raw_cats = await asyncio.to_thread(json.loads, cat_resp.content.decode('utf-8', errors='replace'))
                cat_map = {c['category_id']: c for c in raw_cats}

                # 2. Streams
                stream_resp = await client.get(get_xtream_url(stream_action))
                raw_streams = await asyncio.to_thread(json.loads, stream_resp.content.decode('utf-8', errors='replace'))

                # 3. Filtering
                logger.info(f"Filtering {key}...")
                JOB_STATUS["message"] = f"Filtering {key}..."
                
                # Enrich streams with category names BEFORE filtering
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
            "meta": {
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            },
            "data": final_data,
            "categories": final_categories
        }

        await asyncio.to_thread(
            lambda: json.dump(final_cache, open(CACHE_FILE, "w", encoding="utf-8"), indent=4, ensure_ascii=False)
        )
        
        # RELOAD MEMORY
        load_cache_to_memory()

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

@app.get("/player_api.php")
async def player_api(request: Request, username: str = None, password: str = None, action: Optional[str] = None, vod_id: Optional[int] = None, series_id: Optional[int] = None):
    # --- AUTH CHECK ---
    config = load_config()
    if username != config["xtream"]["username"] or password != config["xtream"]["password"]:
        return JSONResponse(content={"user_info": {"auth": 0}}, status_code=401)

    # --- LOGIN RESPONSE ---
    if not action:
        host = request.url.hostname if request.url.hostname else "localhost"
        port = str(request.url.port) if request.url.port else "8000"
        return {
            "user_info": {
                "username": username,
                "password": password,
                "message": "Logged In",
                "auth": 1,
                "status": "Active",
                "exp_date": "1999999999",
                "created_at": "1600000000",
                "max_connections": "10",
                "allowed_output_formats": ["m3u8", "ts", "rtmp"]
            },
            "server_info": {
                "url": f"http://{host}",
                "port": port,
                "https_port": port,
                "server_protocol": "http",
                "rtmp_port": "8000",
                "timezone": "Europe/London",
                "timestamp_now": int(datetime.now().timestamp()),
                "time_now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "process": 1
            }
        }

    # --- USE IN-MEMORY CACHE ---
    # If cache isn't loaded yet, try loading it
    if IN_MEMORY_CACHE is None:
        load_cache_to_memory()
    
    if IN_MEMORY_CACHE is None:
         return JSONResponse(content={"error": "Cache not built"}, status_code=503)

    data = IN_MEMORY_CACHE.get("data", {})
    categories = IN_MEMORY_CACHE.get("categories", {})

    # --- OPTIMIZED ACTIONS ---

    if action == "get_vod_info" and vod_id is not None:
        # INSTANT LOOKUP (O(1)) instead of loop (O(N))
        basic = SEARCH_INDEX["vod"].get(vod_id)
        
        # We still fetch upstream for extended metadata (Cast, Director, Plot)
        # But now we don't block the Pi CPU searching for the basic info
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                upstream = await client.get(get_xtream_url(f"get_vod_info&vod_id={vod_id}"))
                full_info = upstream.json()
            except Exception:
                # If upstream fails, at least return basic info if we have it
                full_info = {"info": {}, "movie_data": {}}

        if basic:
            full_info.setdefault("movie_data", {})
            # Merge basic info if upstream missing it
            for k, v in basic.items():
                if k not in full_info["movie_data"] or not full_info["movie_data"][k]:
                     full_info["movie_data"][k] = v
            
        return JSONResponse(full_info, media_type="application/json; charset=utf-8")

    elif action == "get_series_info" and series_id is not None:
        # INSTANT LOOKUP (O(1))
        basic = SEARCH_INDEX["series"].get(series_id)

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                upstream = await client.get(get_xtream_url(f"get_series_info&series_id={series_id}"))
                full_info = upstream.json()
            except Exception:
                full_info = {"info": {}, "episodes": {}}

        if basic:
            full_info.setdefault("info", {})
            # Merge basic info
            for k, v in basic.items():
                if k not in full_info["info"] or not full_info["info"][k]:
                     full_info["info"][k] = v

        return JSONResponse(full_info, media_type="application/json; charset=utf-8")

    elif action == "get_live_streams":
        return JSONResponse(data.get("live", []), media_type="application/json; charset=utf-8")
    elif action == "get_live_categories":
        return JSONResponse(categories.get("live", []), media_type="application/json; charset=utf-8")
    elif action == "get_vod_streams":
        return JSONResponse(data.get("vod", []), media_type="application/json; charset=utf-8")
    elif action == "get_vod_categories":
        return JSONResponse(categories.get("vod", []), media_type="application/json; charset=utf-8")
    elif action == "get_series":
        return JSONResponse(data.get("series", []), media_type="application/json; charset=utf-8")
    elif action == "get_series_categories":
        return JSONResponse(categories.get("series", []), media_type="application/json; charset=utf-8")

    return JSONResponse([])



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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
