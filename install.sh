#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "--- Starting Xtream Proxy Installation ---"

# 1. Get current user and directory to make the service file dynamic
CURRENT_USER=$(whoami)
WORK_DIR=$(pwd)
SERVICE_NAME="xtream-proxy"

echo "Detected User: $CURRENT_USER"
echo "Detected Directory: $WORK_DIR"

# 2. Install Python Dependencies
if [ -f "requirements.txt" ]; then
    echo "--- Installing/Updating Python Dependencies ---"
    #pip3 install -r requirements.txt
    sudo apt install -y python3-pip python3-fastapi python3-uvicorn python3-httpx python3-python-multipart git
else
    echo "Warning: requirements.txt not found!"
fi

# 3. Create the systemd service file content
echo "--- Creating Systemd Service File ---"
cat <<EOF > ${SERVICE_NAME}.service
[Unit]
Description=Xtream Proxy Service
After=network.target

[Service]
User=${CURRENT_USER}
WorkingDirectory=${WORK_DIR}
ExecStart=/usr/bin/python3 ${WORK_DIR}/server.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# 4. Install the service file (requires sudo)
echo "--- Installing Service to /etc/systemd/system/ ---"
sudo mv ${SERVICE_NAME}.service /etc/systemd/system/${SERVICE_NAME}.service

# 5. Reload, Enable, and Restart
echo "--- Reloading Systemd Daemon ---"
sudo systemctl daemon-reload

echo "--- Enabling Service on Boot ---"
sudo systemctl enable ${SERVICE_NAME}

echo "--- Restarting Service ---"
sudo systemctl restart ${SERVICE_NAME}

echo "--- Installation Complete! ---"
echo "Check status with: sudo systemctl status ${SERVICE_NAME}"
