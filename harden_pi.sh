#!/usr/bin/env bash
#
# harden_pi.sh — apply the security fixes that need root or that touch runtime
# state on the production Raspberry Pi. Run it on the Pi, from the project
# directory, as the user that owns the files (normally "pi"):
#
#     cd /home/pi/tagPosition && bash harden_pi.sh
#     bash harden_pi.sh --dry-run     # show what would change, change nothing
#
# The script is idempotent: running it twice is harmless.
#
# What it does:
#   1. re-applies the submodule patches (needed after every git submodule update)
#   2. restricts the permissions of secrets.json, data/ and tmp/
#   3. makes systemd start the server with umask 0077, so new files stay private
#   4. adds a per-IP connection limit to nginx for the SSE endpoint
#   5. prints the manual steps that are left (TLS)
#
set -euo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE="tagmap"
NGINX_SITE="/etc/nginx/sites-enabled/tagmap"
SECRETS="$PROJECT_DIR/lib/GoogleFindMyTools/Auth/secrets.json"

step()  { printf '\n=== %s\n' "$*"; }
info()  { printf '    %s\n' "$*"; }
warn()  { printf '    WARNING: %s\n' "$*" >&2; }

run() {
    if [ "$DRY_RUN" = "1" ]; then
        printf '    [dry-run] %s\n' "$*"
    else
        printf '    %s\n' "$*"
        "$@"
    fi
}

cd "$PROJECT_DIR"

step "0. Environment"
info "project dir : $PROJECT_DIR"
info "user        : $(id -un)"
[ "$DRY_RUN" = "1" ] && info "mode        : dry run, nothing will be modified"

# ---------------------------------------------------------------------------
step "1. Submodule patches"
if [ ! -d .venv ]; then
    warn "no .venv in $PROJECT_DIR — skipping the patches"
else
    if [ "$DRY_RUN" = "1" ]; then
        info "[dry-run] .venv/bin/python patches/fcm_patch.py"
        info "[dry-run] .venv/bin/python patches/perms_patch.py"
    else
        .venv/bin/python patches/fcm_patch.py
        .venv/bin/python patches/perms_patch.py
    fi
fi

# ---------------------------------------------------------------------------
step "2. File permissions"
# Credentials: AAS token, FCM credentials, E2EE shared key.
if [ -f "$SECRETS" ]; then
    run chmod 600 "$SECRETS"
else
    warn "$SECRETS not found — run auth.py first"
fi

# Position history and generated pages: personal data.
for d in data tmp; do
    if [ -d "$d" ]; then
        run chmod 700 "$d"
        # shellcheck disable=SC2044
        while IFS= read -r -d '' f; do
            run chmod 600 "$f"
        done < <(find "$d" -maxdepth 1 -type f -print0)
    else
        warn "directory $d does not exist"
    fi
done

# ---------------------------------------------------------------------------
step "3. systemd: umask 0077 for the server"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.service.d"
DROPIN="$DROPIN_DIR/hardening.conf"
if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not available — skipping (are you running this on the Pi?)"
elif ! systemctl list-unit-files | grep -q "^${SERVICE}.service"; then
    warn "unit ${SERVICE}.service not found — skipping"
else
    if [ "$DRY_RUN" = "1" ]; then
        info "[dry-run] write $DROPIN with UMask=0077, ProtectSystem, PrivateTmp"
        info "[dry-run] systemctl daemon-reload && systemctl restart $SERVICE"
    else
        sudo mkdir -p "$DROPIN_DIR"
        sudo tee "$DROPIN" >/dev/null <<'UNIT'
[Service]
# Files created by the server (tmp/map.html, tmp/data_extended.json) contain
# position history: keep them readable by the owner only.
UMask=0077
# Basic sandboxing: the server only needs to read the project directory.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=-/home/pi/tagPosition/data -/home/pi/tagPosition/tmp
UNIT
        sudo systemctl daemon-reload
        sudo systemctl restart "$SERVICE"
        sleep 3
        if systemctl is-active "$SERVICE" >/dev/null; then
            info "$SERVICE is active"
        else
            # the sandboxing directives are the only risky part: roll them back
            # rather than leaving the map down
            warn "$SERVICE did not start with the hardening drop-in — rolling back"
            sudo rm -f "$DROPIN"
            sudo systemctl daemon-reload
            sudo systemctl restart "$SERVICE"
            sleep 3
            systemctl is-active "$SERVICE" >/dev/null \
                && warn "rolled back, $SERVICE is active again (umask NOT applied)" \
                || warn "$SERVICE is still down — check: systemctl status $SERVICE"
        fi
    fi
fi

# ---------------------------------------------------------------------------
step "4. nginx: per-IP connection limit"
# Each /events connection holds a thread in the server for its whole lifetime.
# server.py caps the total at MAX_SSE_CLIENTS; this caps a single client.
if ! command -v nginx >/dev/null 2>&1; then
    warn "nginx not installed — skipping"
elif [ ! -f "$NGINX_SITE" ]; then
    warn "$NGINX_SITE not found — skipping the nginx changes"
elif grep -q "tagmap_conn" "$NGINX_SITE"; then
    info "connection limit already configured"
else
    if [ "$DRY_RUN" = "1" ]; then
        info "[dry-run] write /etc/nginx/conf.d/tagmap_limits.conf (limit_conn_zone)"
        info "[dry-run] add 'limit_conn tagmap_conn 4;' to the location block of $NGINX_SITE"
        info "[dry-run] nginx -t && systemctl reload nginx"
    else
        sudo tee /etc/nginx/conf.d/tagmap_limits.conf >/dev/null <<'CONF'
limit_conn_zone $binary_remote_addr zone=tagmap_conn:1m;
CONF
        BACKUP="${NGINX_SITE}.bak.$(date +%Y%m%d%H%M%S)"
        sudo cp "$NGINX_SITE" "$BACKUP"
        info "backup: $BACKUP"
        sudo sed -i '0,/location \/ {/s//location \/ {\n        limit_conn tagmap_conn 4;/' "$NGINX_SITE"
        if sudo nginx -t; then
            sudo systemctl reload nginx
            info "nginx reloaded"
        else
            warn "nginx config test failed — restoring $BACKUP"
            sudo cp "$BACKUP" "$NGINX_SITE"
            sudo rm -f /etc/nginx/conf.d/tagmap_limits.conf
            sudo nginx -t && sudo systemctl reload nginx
        fi
    fi
fi

# ---------------------------------------------------------------------------
step "5. Verification"
if [ "$DRY_RUN" = "0" ]; then
    ls -ld data tmp 2>/dev/null || true
    [ -f "$SECRETS" ] && ls -l "$SECRETS"
    ls -l data 2>/dev/null | head -5 || true
fi

# ---------------------------------------------------------------------------
step "6. Left to do manually: TLS"
cat <<'TXT'
    The nginx reverse proxy listens on port 7880 in clear HTTP. Basic auth over
    HTTP sends the password in Base64 on every request, and the position data
    travels unencrypted. This is acceptable only on a trusted LAN.

    Pick one before reaching the map from outside the LAN:

    a) VPN / Tailscale — do not expose port 7880 at all, reach the Pi over the
       tunnel. Simplest option, no certificate to manage.

    b) Let's Encrypt — only if the Pi has a public DNS name:
           sudo apt install certbot python3-certbot-nginx
           sudo certbot --nginx -d map.example.org
       certbot rewrites the server block to listen on 443 with TLS and adds the
       redirect from port 80.

    c) Self-signed certificate — no public name needed, the browser shows a
       warning the first time:
           sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
             -keyout /etc/ssl/private/tagmap.key -out /etc/ssl/certs/tagmap.crt
       then in the server block:
           listen 7880 ssl;
           ssl_certificate     /etc/ssl/certs/tagmap.crt;
           ssl_certificate_key /etc/ssl/private/tagmap.key;

    Whichever you pick, re-run: sudo nginx -t && sudo systemctl reload nginx
TXT

printf '\nDone.\n'
