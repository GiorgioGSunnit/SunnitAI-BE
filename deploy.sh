#!/usr/bin/env bash
# =============================================================================
# SunnitAI-BE — Server-Side Deploy Script (systemd, no Docker)
#
# Runs ON THE SERVER after release.sh has uploaded the source code.
# Do not run this manually from your Mac — use release.sh instead.
#
# What it does:
#   1. Checks system requirements (Python 3.11, apt deps)
#   2. Backs up the current source for rollback
#   3. Creates / updates a virtualenv and installs dependencies
#   4. Ensures the spaCy Italian model is present
#   5. Writes systemd unit files for both services
#   6. Reloads systemd and (re)starts both services
#   7. Verifies both services are running
# =============================================================================

set -euo pipefail

APP_DIR="/opt/sunnitai-be"
VENV="$APP_DIR/venv"
BACKUP_DIR="$APP_DIR/../sunnitai-be-previous"
PORT_FUNCTIONS=7071
PORT_VMAI=2025
LOG_FILE="/var/log/sunnitai-be-deploy.log"

PYTHONAPP_PATH="$APP_DIR/src:$APP_DIR/src/be/src"
SVC_FUNCTIONS="sunnitai-functions"
SVC_VMAI="sunnitai-vmai"

log()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
ok()   { echo "[$(date '+%H:%M:%S')] ✅ $*" | tee -a "$LOG_FILE"; }
fail() { echo "[$(date '+%H:%M:%S')] ❌ [ERROR] $*" | tee -a "$LOG_FILE"; exit 1; }

log "=== SunnitAI-BE — Server Deploy (systemd) ==="
log "Directory: $APP_DIR"

# -------------------------------------------------------
# 1. System checks
# -------------------------------------------------------
log ""
log "[1/7] System checks..."

[ -f "$APP_DIR/.env" ]             || fail ".env not found in $APP_DIR — deployment aborted."
[ -f "$APP_DIR/requirements.txt" ] || fail "requirements.txt not found — was the upload successful?"

# Python 3.11
if ! command -v python3.11 &>/dev/null; then
    log "  Python 3.11 not found — installing..."
    apt-get update -qq

    # Try the default repos first (works on Debian 12 / Ubuntu 22.04+)
    if apt-get install -y -qq python3.11 python3.11-venv python3.11-dev 2>/dev/null; then
        log "  Installed python3.11 from default repos"
    else
        DISTRO=$(grep -oP '(?<=^ID=).+' /etc/os-release | tr -d '"')
        if [ "$DISTRO" = "ubuntu" ]; then
            log "  Falling back to deadsnakes PPA (Ubuntu)..."
            apt-get install -y -qq software-properties-common
            add-apt-repository -y ppa:deadsnakes/ppa
            apt-get update -qq
            apt-get install -y -qq python3.11 python3.11-venv python3.11-dev
        elif [ "$DISTRO" = "debian" ]; then
            log "  Building Python 3.11 from source (Debian — one-time, ~5 min)..."
            apt-get install -y -qq \
                build-essential zlib1g-dev libncurses5-dev libgdbm-dev \
                libnss3-dev libssl-dev libsqlite3-dev libreadline-dev libffi-dev wget
            wget -q https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz -P /tmp
            tar -xf /tmp/Python-3.11.9.tgz -C /tmp
            cd /tmp/Python-3.11.9
            ./configure --enable-optimizations --quiet
            make -j"$(nproc)" 2>&1 | tail -5
            make altinstall
            cd - > /dev/null
            rm -rf /tmp/Python-3.11.9 /tmp/Python-3.11.9.tgz
            # altinstall provides python3.11 but not python3.11-venv as a package
            # the venv module is built-in when compiled from source
        else
            fail "Python 3.11 is not available in the default repos on this OS ($DISTRO). Install it manually then re-run release.sh."
        fi
    fi
fi
ok "Python: $(python3.11 --version)"

# System libs needed by PDF rendering (same as Dockerfile)
log "  Checking system libraries..."
dpkg -l libglib2.0-0 libgl1 curl &>/dev/null || \
    apt-get install -y -qq libglib2.0-0 libgl1 curl
ok "System libraries present"

# systemd available
command -v systemctl &>/dev/null || fail "systemd not found — this script requires a systemd-based OS."
ok "systemd available"

# -------------------------------------------------------
# 2. Backup current source (enables rollback)
# -------------------------------------------------------
log ""
log "[2/7] Backing up current source..."

if [ -d "$APP_DIR/src" ]; then
    rm -rf "$BACKUP_DIR"
    cp -r "$APP_DIR/src" "$BACKUP_DIR"
    ok "Previous source backed up to $BACKUP_DIR"
else
    log "  No existing source found — this is a fresh deploy"
fi

# -------------------------------------------------------
# 3. Virtualenv + pip install
# -------------------------------------------------------
log ""
log "[3/7] Setting up Python virtualenv..."

if [ ! -d "$VENV" ]; then
    python3.11 -m venv "$VENV"
    ok "Virtualenv created at $VENV"
else
    ok "Virtualenv already exists — reusing"
fi

log "  Installing/updating dependencies (this may take a few minutes on first run)..."
"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet
"$VENV/bin/pip" install -r "$APP_DIR/src/be/requirement_extration/requirements.txt" --quiet
"$VENV/bin/pip" install scikit-learn --quiet
ok "Dependencies installed"

# -------------------------------------------------------
# 4. spaCy Italian model
# -------------------------------------------------------
log ""
log "[4/7] Checking spaCy model (it_core_news_lg)..."

if ! "$VENV/bin/python" -c "import spacy; spacy.load('it_core_news_lg')" &>/dev/null; then
    log "  Downloading it_core_news_lg (~600 MB) — this only happens once..."
    "$VENV/bin/python" -m spacy download it_core_news_lg 2>&1 | tee -a "$LOG_FILE"
    ok "spaCy model downloaded"
else
    ok "spaCy model already present"
fi

# -------------------------------------------------------
# 5. Write systemd unit files
# -------------------------------------------------------
log ""
log "[5/7] Writing systemd unit files..."

cat > /etc/systemd/system/${SVC_FUNCTIONS}.service << EOF
[Unit]
Description=SunnitAI Functions API (port ${PORT_FUNCTIONS})
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}/src/be/azure-durable-function
Environment=PYTHONPATH=${PYTHONAPP_PATH}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV}/bin/uvicorn main:app \
    --host 0.0.0.0 \
    --port ${PORT_FUNCTIONS} \
    --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SVC_FUNCTIONS}

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/${SVC_VMAI}.service << EOF
[Unit]
Description=SunnitAI VMAI API (port ${PORT_VMAI})
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}/src/be/requirement_extration
Environment=PYTHONPATH=${PYTHONAPP_PATH}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV}/bin/uvicorn call_fast_api:app \
    --host 0.0.0.0 \
    --port ${PORT_VMAI} \
    --log-level info
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SVC_VMAI}

[Install]
WantedBy=multi-user.target
EOF

ok "Unit files written"

# -------------------------------------------------------
# 6. Reload systemd and restart services
# -------------------------------------------------------
log ""
log "[6/7] Restarting services..."

systemctl daemon-reload

for svc in "$SVC_FUNCTIONS" "$SVC_VMAI"; do
    systemctl enable "$svc" --quiet
    systemctl restart "$svc"
    ok "$svc restarted"
done

# -------------------------------------------------------
# 7. Verify both services are running
# -------------------------------------------------------
log ""
log "[7/7] Verifying services..."
sleep 6

ALL_OK=true
for svc in "$SVC_FUNCTIONS" "$SVC_VMAI"; do
    STATUS=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "active" ]; then
        ok "$svc is active"
    else
        log "  ❌ $svc status: $STATUS"
        log "  Last 30 log lines:"
        journalctl -u "$svc" --no-pager -n 30 2>&1 | tee -a "$LOG_FILE" || true
        ALL_OK=false
    fi
done

[ "$ALL_OK" = true ] || fail "One or more services failed to start. Check logs above."

# -------------------------------------------------------
# Summary
# -------------------------------------------------------
log ""
log "==========================================="
log "  DEPLOY COMPLETE"
log "==========================================="
log ""
log "  Functions API:  http://localhost:${PORT_FUNCTIONS}"
log "  VMAI API:       http://localhost:${PORT_VMAI}"
log "  Full log:       ${LOG_FILE}"
log ""
log "  Manage:"
log "    Logs (live):  journalctl -u ${SVC_FUNCTIONS} -f"
log "                  journalctl -u ${SVC_VMAI} -f"
log "    Status:       systemctl status ${SVC_FUNCTIONS} ${SVC_VMAI}"
log "    Stop:         systemctl stop ${SVC_FUNCTIONS} ${SVC_VMAI}"
log "    Restart:      systemctl restart ${SVC_FUNCTIONS} ${SVC_VMAI}"
log ""
log "  Rollback (if needed):"
log "    systemctl stop ${SVC_FUNCTIONS} ${SVC_VMAI}"
log "    rm -rf ${APP_DIR}/src && cp -r ${BACKUP_DIR} ${APP_DIR}/src"
log "    systemctl start ${SVC_FUNCTIONS} ${SVC_VMAI}"
log ""