"""Endpoints and paths for the FFBE game admin app."""

HOST = "10.129.1.139"
USER = "plasmatixultra"
CONTAINER = "ffbe"
IMAGE = "ffbe-ios-server:local"

SAVE_HOST = "/home/plasmatixultra/exvius/server/state/campaign.json"
SAVE_CONTAINER = "/srv/ffbe/server-root/state/campaign.json"
BACKUP_DIR = "/home/plasmatixultra/exvius/server/state/backups"

STATUS_URL_CMD = "docker exec ffbe python3 /app/ffbe.py status"
