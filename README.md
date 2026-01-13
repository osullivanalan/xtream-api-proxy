# Xtream Proxy (Raspberry Pi)

This project is a local Xtream Codes–compatible proxy that runs on a Raspberry Pi. It filters Live TV, VOD, and Series lists, serves them from a local cache, and forwards actual stream playback to the original provider. Useful to declutter the verbose cateogries provided by services which are unwieldy to manage in client applications and often slow them down.

## Features

- **Xtream-compatible API**: Works with IPTV clients that support Xtream Codes (e.g. TiviMate, IPTV Smarters).
- **Filtering by prefix**: Filter Live/VOD/Series categories using simple prefix rules configured in `config.json`.
- **Local caching**: Downloads and filters once, then serves from `local_cache.json` for fast reloads.
- **Stream redirect**: Playlist URLs point to the Pi, but streams are redirected to the provider for playback.
- **Config web UI**: Mobile-friendly `/config` page to edit provider URL, credentials, and filters.
- **Background refresh**: `/refresh_cache` runs in the background and exposes progress via `/status`.
- **Systemd service**: Runs automatically on boot and restarts on failure.
- **Basic logging**: Logs to systemd journal (`journalctl`) for debugging.

## Requirements

- Raspberry Pi with Python 3 installed.
- Network access to your IPTV provider’s Xtream endpoint.
- An IPTV client (e.g. TiviMate, IPTV Smarters) on the same LAN.

Python dependencies (also in `requirements.txt`):

```text
fastapi
uvicorn
httpx
```

## Install with:

```bash
pip3 install -r requirements.txt
```

or if using an externall managed environment

```bash
sudo apt install -y python3-pip python3-fastapi python3-uvicorn python3-httpx python3-python-multipart git
```

## Files

* server.py – FastAPI app, endpoints, background refresh, redirects, config UI.
* filters.py – Filtering logic for Live/VOD/Series by category prefix.
* config.json – Provider URL, credentials, and filters.
* local_cache.json – Generated cache containing filtered data and categories.
* requirements.txt – Python dependencies.
* README.md – This documentation.

## config.json

Example:

```json
{
  "xtream": {
    "base_url": "http://provider-url.example.com",
    "username": "YOUR_USERNAME",
    "password": "YOUR_PASSWORD"
  },
  "filters": {
    "live": ["IE", "EN", "UK"],
    "vod": ["EN", "TOP", "Netflix"],
    "series": ["EN", "Netflix", "Amazon"]
  }
}
```

* Filters are prefixes for category names (case-insensitive).
* Edit via the /config UI instead of manual file edits when possible.

## Running on a server (e.g local raspberry pi)
1. Setup project

```bash
ssh user@ip

mkdir -p ~/xtream-proxy
cd ~/xtream-proxy

# Copy in server.py, filters.py, config.json, requirements.txt, README.md
pip3 install -r requirements.txt
```

2. Test run manually

From ~/xtream-proxy:

```bash
python3 server.py
```

Visit from a browser on the LAN:

* http://ip:port/config – edit settings & filters.
* http://ip:port/refresh_cache – start background cache refresh.
* http://ip:port/status – see refresh state.

Press Ctrl+C to stop when finished testing.

### Systemd service (auto start + restart)

Create the service file:

```bash
sudo nano /etc/systemd/system/xtream-proxy.service
```

Content:

```text
[Unit]
Description=Xtream Proxy Service
After=network.target

[Service]
User=alan
WorkingDirectory=/home/alan/xtream-proxy
ExecStart=/usr/bin/python3 /home/alan/xtream-proxy/server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable xtream-proxy
sudo systemctl start xtream-proxy
sudo systemctl status xtream-proxy
```

### Logs (live tail):

```bash
journalctl -u xtream-proxy -f
```

### Web endpoints

All Xtream-style endpoints require username and password matching config.json.

### Management / UI

    GET /config
    Mobile-friendly HTML page to view and edit base URL, credentials, and filters.

    POST /update_config_form
    Form submit target for /config. Updates config.json.

    GET /refresh_cache
    Starts background refresh of Live/VOD/Series data and filters it into local_cache.json.

    GET /status
    Returns JSON with state and message for the refresh job.

### Xtream API (proxy)

    GET /player_api.php?username=U&password=P
    Login; returns user_info and server_info.

    GET /player_api.php?username=U&password=P&action=get_live_streams
    Filtered live streams from cache.

    GET /player_api.php?username=U&password=P&action=get_live_categories
    Filtered live categories from cache.

    GET /player_api.php?username=U&password=P&action=get_vod_streams
    Filtered VOD list.

    GET /player_api.php?username=U&password=P&action=get_vod_categories
    Filtered VOD categories.

    GET /player_api.php?username=U&password=P&action=get_series
    Filtered series list.

    GET /player_api.php?username=U&password=P&action=get_series_categories
    Filtered series categories.

    GET /player_api.php?username=U&password=P&action=get_vod_info&vod_id=ID
    VOD metadata: proxied from the provider, optionally enriched with basic info from cache.

    GET /player_api.php?username=U&password=P&action=get_series_info&series_id=ID
    Series metadata: proxied from the provider, optionally enriched with basic info from cache.

### Stream redirects

    GET /live/{username}/{password}/{stream_id}.ts
    Redirects to provider’s /live/... URL.

    GET /movie/{username}/{password}/{stream_id}.{ext}
    Redirects to provider’s /movie/... URL.

    GET /series/{username}/{password}/{stream_id}.{ext}
    Redirects to provider’s /series/... URL.

These redirects let the playlist point at the Pi while actual stream data still comes directly from the provider.
IPTV client configuration

In your IPTV app (Xtream Codes API mode):

* Server URL: http://1ip:port
* Username: same as config.json
* Password: same as config.json

*Do not append /player_api.php in the app; it will be added automatically.*

## Typical workflow

* Open http://ip:port/config on your phone.
* Adjust base URL, username, password, and filters. Save.
* Call http://ip:port/refresh_cache.
* Wait until /status reports idle/success.
* Refresh playlist in your IPTV app.

## Troubleshooting

    Login failed in client
    Ensure app username/password match config.json and that the service is running.

    No channels or categories
    Run /refresh_cache and check /status. Look at journalctl -u xtream-proxy -f for errors.

    Strange characters in category names
    The app and server both use UTF‑8; if your provider sends unusual symbols, they are passed through unmodified.

