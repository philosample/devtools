#!/usr/bin/env bash
# RustDesk OSS / Colima unified macOS setup
# Requires macOS, regular user (not sudo). No router/firewall/privacy bypass.
set -Eeuo pipefail

APP=${0##*/}
CMD=${1:-help}
if (($#)); then shift; fi
HOST=''; KEY=''; BASE="${RUSTDESK_HOME:-$HOME/Services/rustdesk}"
INSTALL_BREW=0; AUTOSTART=0; NO_TEST=0; NO_CLIENT=0; INSTALL_SERVICE=0; CLIENT_AUTOSTART=0; PREVENT_SLEEP=0
CPU=''; MEMORY=''; DO_UPGRADE=0
IMAGE='rustdesk/rustdesk-server:latest'; PROJECT='rustdesk-oss'

log(){ printf '\n==> %s\n' "$*"; }
warn(){ printf 'WARNING: %s\n' "$*" >&2; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
trap 'warn "Command failed at line $LINENO; no existing Colima VM or RustDesk identity was intentionally deleted."' ERR
usage(){ cat <<'EOF'
RustDesk all-in-one macOS installer (Apple Silicon / Intel)

  ./rustdesk-all-in-one-macos.sh setup [--host HOST] [--key PUBLIC_KEY] [--install-service] [--client-autostart] [--prevent-sleep]
  ./rustdesk-all-in-one-macos.sh doctor [--host HOST]
  ./rustdesk-all-in-one-macos.sh readiness [--host HOST] [--key PUBLIC_KEY]
  ./rustdesk-all-in-one-macos.sh docker [--autostart] [--cpu N --memory N] [--no-test] [--install-homebrew]
  ./rustdesk-all-in-one-macos.sh client [--host HOST] [--key PUBLIC_KEY] [--no-client]
  ./rustdesk-all-in-one-macos.sh server --host HOST [--autostart] [--install-homebrew] [--cpu N --memory N]
  ./rustdesk-all-in-one-macos.sh status
  ./rustdesk-all-in-one-macos.sh check --host HOST
  ./rustdesk-all-in-one-macos.sh key
  ./rustdesk-all-in-one-macos.sh backup
  ./rustdesk-all-in-one-macos.sh update
  ./rustdesk-all-in-one-macos.sh stop
  ./rustdesk-all-in-one-macos.sh help

Options: --install-service requests the RustDesk macOS helper; --client-autostart adds a Login Item.
         --prevent-sleep disables idle system sleep on AC power (sudo approval required).
         readiness writes a JSON report to ~/Library/Application Support/rustdesk-setup/readiness.json.
No option bypasses macOS privacy consent or sets an unattended password silently.

Workflow:
  THIS MAC: ./rustdesk-all-in-one-macos.sh setup
  WITH SERVER: ./rustdesk-all-in-one-macos.sh setup --host SERVER_HOST --key PUBLIC_KEY
  MAC MINI: ./rustdesk-all-in-one-macos.sh server --host SERVER_HOST --autostart

--autostart is USER-LOGIN autostart, NOT pre-login boot availability.
Server data stored at ~/Services/rustdesk by default (RUSTDESK_HOME overrides).
Router ports: TCP 21115-21117, UDP 21116. DDNS / forwarding remain manual.
The server command enables Colima's experimental gRPC forwarder for UDP; it restarts a running Colima profile only when unrelated containers are not active.
Don't run with sudo. Does not uninstall Docker Desktop or delete any Docker data.
EOF
}
while (($#)); do
  case "$1" in
    --host|--key|--cpu|--memory)
      (($# >= 2)) || die "$1 requires a value"
      case "$1" in
        --host) HOST=$2;; --key) KEY=$2;; --cpu) CPU=$2;; --memory) MEMORY=$2;;
      esac; shift 2;;
    --autostart) AUTOSTART=1; shift;;
    --install-homebrew) INSTALL_BREW=1; shift;;
    --no-test) NO_TEST=1; shift;;
    --no-client) NO_CLIENT=1; shift;;
    --install-service) INSTALL_SERVICE=1; shift;;
    --client-autostart) CLIENT_AUTOSTART=1; shift;;
    --prevent-sleep) PREVENT_SLEEP=1; shift;;
    *) die "Unknown option: $1 (try help)";;
  esac
done
need_mac(){ [[ $(uname -s) == Darwin ]] || die 'This script targets macOS only'; [[ $EUID -ne 0 ]] || die 'Run as your normal user, not with sudo'; }
validate_host(){
  [[ -n $HOST ]] || die 'Missing --host HOST'
  [[ ${#HOST} -le 253 && $HOST =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ && $HOST != *..* && $HOST != *. ]] || die 'Invalid host (use DNS name or IPv4; no scheme/port)'
}
find_brew(){
  command -v brew >/dev/null 2>&1 && return 0
  local b
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x $b ]]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}
ensure_brew(){
  if ! find_brew; then
    ((INSTALL_BREW)) || die 'Homebrew missing; install from brew.sh or pass --install-homebrew'
    log 'Running official Homebrew installer (interactive)'
    /bin/bash -c "$(/usr/bin/curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    find_brew || die 'Open a new Terminal session and rerun (Homebrew not found in PATH)'
  fi
  local prefix; prefix=$(brew --prefix)
  export PATH="$prefix/bin:$prefix/sbin:$PATH"
}
# Docker CLI inherits credentials from ~/.docker/config.json. A previous Docker Desktop
# installation can leave an unusable "desktop" helper, breaking public image pulls.
repair_docker_config(){
  local cfg="$HOME/.docker/config.json"
  [[ -f $cfg ]] || return 0
  command -v python3 >/dev/null 2>&1 || { warn 'python3 absent; cannot inspect Docker config'; return 0; }
  python3 - "$cfg" <<'PY'
import json, os, shutil, sys, datetime
p=sys.argv[1]
try:
    with open(p) as f: d=json.load(f)
except (ValueError, OSError) as e:
    print('WARNING: Docker config not modified:', e); sys.exit(0)
if not isinstance(d,dict):
    print('WARNING: Docker config is not an object; unchanged'); sys.exit(0)
changed=False
if d.get('credsStore')=='desktop' and not shutil.which('docker-credential-desktop'):
    d.pop('credsStore'); changed=True
for k,v in list(d.get('credHelpers',{}).items()):
    if v=='desktop' and not shutil.which('docker-credential-desktop'):
        d['credHelpers'].pop(k); changed=True
if changed:
    if not d.get('credHelpers'): d.pop('credHelpers',None)
    backup=p+'.backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    shutil.copy2(p,backup)
    tmp=p+'.tmp.'+str(os.getpid())
    with open(tmp,'w') as f: json.dump(d,f,indent=2); f.write('\n')
    os.chmod(tmp,0o600)
    os.replace(tmp,p)
    print('Removed missing Docker Desktop credential helper; backup:',backup)
else: print('Docker credential helper configuration OK')
PY
}
# Use the official Homebrew plugin directory. Preserve Docker Desktop symlinks,
# but replace *only* broken/obsolete Docker Desktop symlinks after backing them up.
configure_plugins(){
  local dir="$HOME/.docker/cli-plugins" prefix plugin target current stamp
  mkdir -p "$dir"
  prefix=$(brew --prefix)
  for plugin in docker-compose docker-buildx; do
    target="$prefix/opt/$plugin/bin/$plugin"
    [[ -x $target ]] || die "Missing Homebrew plugin: $target"
    current="$dir/$plugin"
    if [[ -L $current ]]; then
      if [[ ! -e $current || $(readlink "$current") == /Applications/Docker.app/* ]]; then
        stamp=$(date +%Y%m%d%H%M%S)
        mv "$current" "${current}.pre-colima-${stamp}"
        ln -s "$target" "$current"
        log "Using Homebrew $plugin plugin (previous symlink backed up)"
      fi
    elif [[ ! -e $current ]]; then
      ln -s "$target" "$current"
    else
      warn "Existing regular plugin at $current preserved"
    fi
  done
  # CLI v29 supports this config key. Avoid changing existing config for plugin
  # discovery unless actually needed (symlinks normally suffice).
}
install_docker(){
  ensure_brew
  local f
  for f in colima docker docker-compose docker-buildx; do
    if brew list --formula "$f" >/dev/null 2>&1; then
      log "$f already installed"
    else
      log "Installing $f"
      brew install "$f"
    fi
  done
  repair_docker_config
  configure_plugins
  docker compose version
  docker buildx version
}
start_colima(){
  local args=(--runtime docker) forwarder='ssh' other_containers
  [[ $CMD != server ]] || args+=(--port-forwarder grpc)
  [[ -z $CPU ]] || args+=(--cpus "$CPU")
  [[ -z $MEMORY ]] || args+=(--memory "$MEMORY")
  [[ -z $CPU || $CPU =~ ^[1-9][0-9]*$ ]] || die '--cpu must be positive integer'
  [[ -z $MEMORY || $MEMORY =~ ^[1-9][0-9]*$ ]] || die '--memory must be positive integer (GiB)'
  if colima status >/dev/null 2>&1; then
    if [[ $CMD == server ]]; then
      forwarder=$(sed -nE 's/^portForwarder:[[:space:]]*([[:alnum:]]+).*$/\1/p' "$HOME/.colima/default/colima.yaml" 2>/dev/null | head -1)
      forwarder=${forwarder:-ssh}
      if [[ $forwarder != grpc ]]; then
        other_containers=$(docker --context colima ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}' | awk -v project="$PROJECT" '$2 != project { print $1 }')
        [[ -z $other_containers ]] || die "RustDesk needs UDP forwarding, which Colima's '$forwarder' mode does not provide. Stop unrelated Colima containers before rerunning; left running: ${other_containers//$'\n'/, }"
        warn "Colima's gRPC port forwarder is experimental; RustDesk needs it to carry UDP 21116 for ID registration. Restarting Colima (only RustDesk containers are running)."
        colima stop
        colima start "${args[@]}"
      else
        log 'Colima gRPC forwarding is already enabled for RustDesk UDP 21116'
      fi
    else
      log 'Colima already running; preserving current VM settings'
    fi
  else
    if [[ $CMD == server ]]; then
      log 'Starting Colima with experimental gRPC forwarding for RustDesk UDP 21116'
    else
      log 'Starting Colima Docker runtime'
    fi
    colima start "${args[@]}"
  fi
  docker context inspect colima >/dev/null 2>&1 || die 'Docker context "colima" unavailable'
  docker --context colima info --format 'Docker server: {{.ServerVersion}} | OS: {{.OperatingSystem}}'
  if ((AUTOSTART)); then
    log 'Configuring Colima to start at this macOS user login'
    brew services start colima
    warn 'Login startup is not pre-login boot startup. After a power outage, login may be needed.'
  fi
}
test_docker(){
  ((NO_TEST)) && { log 'Skipping hello-world container test'; return 0; }
  log 'Running hello-world image pull and container execution'
  docker --context colima run --rm hello-world
}
docker_setup(){
  need_mac; install_docker; start_colima; test_docker
  log 'Docker/Colima ready. Explicit context: docker --context colima ...'
  warn 'Default Docker context unchanged to avoid interfering with Docker Desktop workflows.'
}
compose(){ (cd "$BASE" && docker --context colima compose -p "$PROJECT" "$@"); }
need_deploy(){
  [[ -f $BASE/compose.yaml && -f $BASE/.env ]] || die "No RustDesk deployment at $BASE"
  command -v docker >/dev/null 2>&1 || die 'Docker missing'
  docker --context colima info >/dev/null 2>&1 || die 'Colima not running; start with colima start'
}
write_deploy(){
  mkdir -p "$BASE/data" "$BASE/backups"
  chmod 700 "$BASE" "$BASE/data" "$BASE/backups"
  if [[ -f $BASE/.env ]]; then
    local old
    old=$(sed -n 's/^RUSTDESK_HOST=//p' "$BASE/.env" | head -1)
    if [[ $old != "$HOST" ]]; then
      local backup="$BASE/.env.before-host-change-$(date +%Y%m%dT%H%M%S)"
      cp -p "$BASE/.env" "$backup"
      python3 - "$BASE/.env" "$HOST" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
host = sys.argv[2]
lines = path.read_text().splitlines()
for index, line in enumerate(lines):
    if line.startswith("RUSTDESK_HOST="):
        lines[index] = "RUSTDESK_HOST=" + host
        break
else:
    lines.insert(0, "RUSTDESK_HOST=" + host)
path.write_text("\n".join(lines) + "\n")
PY
      chmod 600 "$BASE/.env"
      log "Updated advertised host from '$old' to '$HOST'; prior settings saved to $backup"
    fi
  else
    printf 'RUSTDESK_HOST=%s\nRUSTDESK_IMAGE=%s\n' "$HOST" "$IMAGE" > "$BASE/.env"
    chmod 600 "$BASE/.env"
  fi
  if [[ ! -f $BASE/compose.yaml ]]; then
    cat > "$BASE/compose.yaml" <<'YAML'
services:
  hbbr:
    image: ${RUSTDESK_IMAGE}
    command: ["hbbr"]
    ports:
      - "21117:21117/tcp"
    volumes:
      - ./data:/root
    restart: unless-stopped
  hbbs:
    image: ${RUSTDESK_IMAGE}
    command: ["hbbs", "-r", "${RUSTDESK_HOST}:21117"]
    ports:
      - "21115:21115/tcp"
      - "21116:21116/tcp"
      - "21116:21116/udp"
    volumes:
      - ./data:/root
    depends_on:
      - hbbr
    restart: unless-stopped
YAML
    chmod 600 "$BASE/compose.yaml"
  fi
  if [[ ! -f $BASE/.gitignore ]]; then
    printf 'data/\nbackups/\n.env\n' > "$BASE/.gitignore"
  fi
}
check_local_ports(){
  local p listeners
  for p in 21115 21116 21117; do
    listeners=$(lsof -nP -iTCP:"$p" -sTCP:LISTEN 2>/dev/null || true)
    [[ -z $listeners ]] || warn "TCP $p already has listener (may be existing RustDesk): $listeners"
  done
}
pin_image(){
  # Pin only fresh latest deployments; an existing pin is not replaced here.
  local old digest
  old=$(sed -n 's/^RUSTDESK_IMAGE=//p' "$BASE/.env" | head -1)
  [[ $old == "$IMAGE" ]] || { log "Keeping existing RustDesk image pin: $old"; return 0; }
  docker --context colima pull "$IMAGE"
  digest=$(docker --context colima image inspect "$IMAGE" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)
  if [[ $digest == *@sha256:* ]]; then
    python3 - "$BASE/.env" "$digest" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); text=p.read_text()
p.write_text(text.replace('RUSTDESK_IMAGE=rustdesk/rustdesk-server:latest','RUSTDESK_IMAGE='+sys.argv[2]))
PY
    chmod 600 "$BASE/.env"
    log "Pinned RustDesk server image: $digest"
  else warn 'Unable to pin image digest; using latest tag until manually updated'; fi
}
public_key(){
  if [[ -s "$BASE/data/id_ed25519.pub" ]]; then
    printf '\nSERVER PUBLIC KEY (safe to copy):\n'
    cat "$BASE/data/id_ed25519.pub"; printf '\n'
  else
    warn 'Public key not present; check server logs'
  fi
}
network(){
  local iface lan pub
  iface=$(route -n get default 2>/dev/null | awk '/interface:/{print $2;exit}' || true)
  lan=''; [[ -z $iface ]] || lan=$(ipconfig getifaddr "$iface" 2>/dev/null || true)
  pub=$(curl -4fsS --connect-timeout 3 --max-time 7 https://api.ipify.org 2>/dev/null || true)
  printf 'Interface: %s\nLAN IP: %s\nObserved public IPv4: %s\n' "${iface:-unknown}" "${lan:-unknown}" "${pub:-unknown}"
  [[ -z $HOST ]] || { printf 'Host: %s\n' "$HOST"; dscacheutil -q host -a name "$HOST" 2>/dev/null | head -10 || true; }
  printf 'For clients on the home LAN, no router forwarding or DDNS is needed.\n'
  printf 'For access away from home, use a VPN or separately configure external reachability.\n'
}
configure_server_client(){
  local helper="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rustdesk-client-macos.sh"
  [[ -x $helper ]] || die "Missing companion client setup script: $helper"
  log 'Configuring this Mac as a RustDesk client on its own self-hosted server'
  "$helper" setup --host "$HOST" --key "$(cat "$BASE/data/id_ed25519.pub")" --install-service --client-autostart
}
server_setup(){
  need_mac; validate_host
  docker_setup
  write_deploy
  check_local_ports
  pin_image
  log 'Checking Compose configuration'; compose config --quiet
  log 'Starting RustDesk OSS signaling and relay containers'; compose up -d
  log 'Waiting up to 45s for server public key'
  local i
  for ((i=0;i<45;i++)); do [[ -s $BASE/data/id_ed25519.pub ]] && break; sleep 1; done
  [[ -s $BASE/data/id_ed25519.pub ]] || { compose logs --tail=60; die 'RustDesk public key not generated'; }
  chmod 600 "$BASE/data/id_ed25519"
  chmod 644 "$BASE/data/id_ed25519.pub"
  compose ps
  if ((!NO_CLIENT)); then configure_server_client; else warn 'Native RustDesk client setup skipped by --no-client; this Mac will not be reachable as a desktop target until configured separately.'; fi
  public_key
  network
  cat <<EOF

RustDesk server deployed at: $BASE
ID Server: $HOST
Relay server: leave blank initially (advertised by hbbs)
API Server: leave blank (OSS)
Key: copy public key printed above

MANUAL:
1. In RustDesk Security, set a strong permanent password for unattended access.
2. Approve Accessibility and Screen & System Audio Recording in macOS Privacy & Security.
3. Confirm the Mac mini's displayed RustDesk ID, then connect to that ID from another client using the same server host and key.
4. Enable macOS user login after reboot for Colima --autostart; this is NOT pre-login service.
5. For access away from home, use a VPN or separately configure external reachability; LAN clients need no router forwarding.
EOF
}
client_setup(){
  need_mac
  ((NO_CLIENT)) && { log 'Client installation disabled by --no-client'; return 0; }
  ensure_brew
  if [[ -d /Applications/RustDesk.app ]]; then
    log 'RustDesk already installed at /Applications/RustDesk.app'
  elif brew list --cask rustdesk >/dev/null 2>&1; then
    log 'RustDesk cask already installed'
  else
    log 'Installing RustDesk macOS app'
    brew install --cask rustdesk
  fi
  open -a RustDesk || warn 'Could not open RustDesk automatically'
  configure_client_service
  configure_client_autostart
  if [[ -n $HOST ]]; then
    validate_host
    printf '\nConfigure RustDesk Settings > Network > ID/Relay Server:\n  ID Server: %s\n  Key: %s\n  Relay: blank initially\n  API: blank\n' "$HOST" "${KEY:-retrieve from server using the key command}"
    log 'Basic connectivity diagnostics'
    check_host
  fi
  cat <<'EOF'

MANUAL CLIENT STEPS:
- RustDesk > Settings > Network > ID/Relay Server: enter server address + public key.
- For unattended access: use RustDesk security settings and a strong permanent password.
- In System Settings > Privacy & Security: approve Accessibility and Screen & System Audio Recording.
- Enable the RustDesk service if offered, and verify it works after logout/reboot.
- FileVault pre-boot unlock remains a separate limitation.
EOF
  open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility' 2>/dev/null || true
}

# Optional supported app flag. May trigger an administrator authorization prompt.
# Never silently elevate privileges or modify macOS TCC databases.
configure_client_service(){
  if ((INSTALL_SERVICE)); then
    local bin='/Applications/RustDesk.app/Contents/MacOS/RustDesk'
    [[ -x $bin ]] || { warn 'RustDesk executable unavailable; install its service through the app UI'; return 0; }
    log 'Requesting RustDesk service installation (may require macOS approval)'
    if ! "$bin" --install-service; then
      warn 'Service installation unsuccessful. Open RustDesk > Settings > General/Security and install its service manually.'
    fi
  fi
}
configure_client_autostart(){
  if ((CLIENT_AUTOSTART)); then
    log 'Requesting RustDesk login item through System Events'
    # System Events may refuse automation, especially on newer macOS versions.
    # We never equate a successful command with verified login persistence.
    if ! osascript <<'OSA'
tell application "System Events"
  if not (exists login item "RustDesk") then
    make login item at end with properties {name:"RustDesk", path:"/Applications/RustDesk.app", hidden:false}
  end if
end tell
OSA
    then
      warn 'Login item could not be added automatically. Use System Settings > General > Login Items & Extensions.'
    fi
  fi
}

# macOS does not expose a reliable public CLI to inspect RustDesk's TCC
# permissions or permanent password. Do not invent a PASS for either.
login_item_status(){
  local result
  result=$(osascript -e 'tell application "System Events" to get name of every login item' 2>/dev/null) || {
    echo unknown; return
  }
  if [[ ",$result," == *",RustDesk,"* || ",$result," == *", RustDesk,"* ]]; then
    echo present
  else
    echo absent
  fi
}
service_status(){
  local results
  results=$( (launchctl list 2>/dev/null; sudo -n launchctl print system 2>/dev/null) | grep -i rustdesk || true)
  if [[ -n $results ]]; then
    echo detected
  elif [[ -n $(find /Library/LaunchDaemons "$HOME/Library/LaunchAgents" -maxdepth 1 -iname '*rustdesk*' -print 2>/dev/null || true) ]]; then
    echo configured_not_verified
  else
    echo unverified
  fi
}
ac_sleep_status(){
  pmset -g custom 2>/dev/null | awk '
    /^AC Power:/{ac=1;next}
    /^[^[:space:]].*Power:/{ac=0}
    ac && $1=="sleep"{print $2;exit}
  '
}
configure_sleep(){
  if ((PREVENT_SLEEP)); then
    log 'Disabling idle system sleep on AC power (display sleep remains unchanged)'
    log 'macOS will request administrator authorization'
    sudo pmset -c sleep 0 || die 'Could not configure AC idle sleep; no success claimed'
    [[ $(ac_sleep_status) == 0 ]] || die 'AC idle sleep change could not be verified'
  fi
}
write_readiness_json(){
  local login_status service_state sleep_value report_dir report_file
  login_status=$(login_item_status)
  service_state=$(service_status)
  sleep_value=$(ac_sleep_status)
  report_dir="$HOME/Library/Application Support/rustdesk-setup"
  report_file="$report_dir/readiness.json"
  mkdir -p "$report_dir"
  python3 - "$report_file" "$HOST" "$login_status" "$service_state" "$sleep_value" "$KEY" <<'PY_JSON'
from pathlib import Path
import json, subprocess, sys, datetime
path, host, login, service, sleep, key = sys.argv[1:]
def run(*args):
    return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
app = Path('/Applications/RustDesk.app').exists()
process = run('pgrep','-if','RustDesk.app/Contents/MacOS|rustdesk')
report = {
  'generated_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
  'app_installed': app,
  'process_detected': process,
  'login_item': login,
  'service': service,
  'ac_idle_sleep_minutes': int(sleep) if sleep.isdigit() else None,
  'self_hosted_server': {'host_provided': bool(host), 'key_provided': bool(key)},
  'requires_manual_verification': [
    'Screen & System Audio Recording permission',
    'Accessibility permission',
    'RustDesk helper functionality after logout/reboot',
    'Permanent unattended-access password',
    'Correct server ID and matching public key saved inside RustDesk',
    'Successful remote session from a different network',
  ],
  'fully_verified': False,
}
path = Path(path)
path.write_text(json.dumps(report,indent=2)+'\n')
path.chmod(0o600)
print('Readiness JSON:',path)
PY_JSON
}
client_report(){
  log 'Local RustDesk readiness report'
  if [[ -d /Applications/RustDesk.app ]]; then echo 'PASS RustDesk app installed'; else echo 'FAIL RustDesk app missing'; fi
  if pgrep -if 'RustDesk.app/Contents/MacOS|rustdesk' >/dev/null 2>&1; then
    echo 'PASS RustDesk process appears active'
  else
    echo 'CHECK RustDesk process not detected'
  fi
  printf 'CHECK RustDesk launchd service: %s\n' "$(service_status)"
  printf 'CHECK RustDesk login item: %s\n' "$(login_item_status)"
  printf 'AC idle sleep (minutes): %s\n' "$(ac_sleep_status || true)"
  if [[ -n $HOST ]]; then
    echo "ID server intended: $HOST"
    [[ -n $KEY ]] && echo 'Public key supplied to script (not automatically entered into app)' || echo 'CHECK Obtain server public key'
  else
    echo 'PENDING Server address and key: Mac mini deployment not yet supplied'
  fi
  echo 'MANUAL: verify macOS screen/control consent, helper, permanent password,'
  echo '        saved server configuration and actual external remote session.'
  write_readiness_json
}
full_setup(){
  need_mac
  log 'All-in-one workstation setup: Docker/Colima then RustDesk client'
  docker_setup
  client_setup
  configure_sleep
  client_report
  log 'Setup complete: macOS consent/password and external-session verification remain manual.'
}

check_host(){
  validate_host
  local p
  for p in 21115 21116 21117; do
    if nc -G 4 -z "$HOST" "$p" >/dev/null 2>&1; then
      printf 'PASS TCP %s\n' "$p"
    else printf 'FAIL TCP %s\n' "$p"; fi
  done
  warn 'TCP checks alone do not verify UDP 21116, relay, or end-to-end operation. Test from an external network.'
}
doctor(){
  need_mac
  printf 'OS: %s | Architecture: %s\n' "$(sw_vers -productVersion)" "$(uname -m)"
  find_brew && brew --version | head -1 || warn 'Homebrew unavailable'
  for c in colima docker; do
    if command -v "$c" >/dev/null 2>&1; then "$c" version 2>/dev/null | head -3 || true; else warn "$c unavailable"; fi
  done
  if command -v docker >/dev/null 2>&1; then
    docker compose version || true
    docker buildx version || true
    docker --context colima info --format 'Docker server: {{.ServerVersion}}' 2>/dev/null || true
  fi
  [[ -f $HOME/.docker/config.json ]] && grep -n 'desktop' "$HOME/.docker/config.json" || true
  network
  printf '\nPower settings:\n'; pmset -g custom 2>/dev/null | grep -E ' sleep | displaysleep | powernap | womp ' || true
}
status(){
  need_mac
  colima status || true
  if [[ -f $BASE/compose.yaml ]]; then need_deploy; compose ps; else warn "No server deployment at $BASE"; fi
}
backup(){
  need_mac; need_deploy
  local stamp out was_running=0 failure=''
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  mkdir -p "$BASE/backups"; chmod 700 "$BASE/backups"
  out="$BASE/backups/rustdesk-$stamp.tar.gz"
  [[ ! -e $out ]] || die "Backup archive already exists: $out"
  if [[ -n $(compose ps --status running -q) ]]; then
    was_running=1
    log 'Stopping running RustDesk containers briefly for consistent backup'
    if ! compose stop; then
      compose start || warn 'RustDesk restart failed after unsuccessful stop; inspect status'
      die 'Could not stop RustDesk containers for backup'
    fi
  fi
  # Explicitly handle expected failures before function-local restart state
  # goes out of scope; an EXIT trap cannot safely refer to that local state.
  if ! (cd "$BASE" && tar -czf "$out" data .env compose.yaml); then
    failure='RustDesk backup archive creation failed'
  elif ! chmod 600 "$out"; then
    failure='RustDesk backup permissions could not be secured'
  fi
  if [[ -n $failure && -e $out ]]; then
    rm -f -- "$out" || warn "Could not remove incomplete backup: $out"
  fi
  if ((was_running)); then
    compose start || die 'RustDesk containers could not be restarted after backup; inspect status immediately'
  fi
  [[ -z $failure ]] || die "$failure; restart of previously running containers was attempted"
  log "Backup created: $out"
  warn 'Archive includes private RustDesk identity key. Encrypt and copy off the Mac mini.'
}

update(){
  need_mac; need_deploy
  log 'Taking a backup before updating'; backup
  local old digest
  old=$(sed -n 's/^RUSTDESK_IMAGE=//p' "$BASE/.env" | head -1)
  log 'Pulling newest official RustDesk OSS server release (latest tag)'
  docker --context colima pull "$IMAGE"
  digest=$(docker --context colima image inspect "$IMAGE" --format '{{index .RepoDigests 0}}' 2>/dev/null || true)
  [[ $digest == *@sha256:* ]] || die 'Unable to find new digest; keeping existing deployment'
  [[ $digest == "$old" ]] && { log 'Already at the current image digest'; return 0; }
  cp -p "$BASE/.env" "$BASE/.env.before-update-$(date +%Y%m%d%H%M%S)"
  python3 - "$BASE/.env" "$digest" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); lines=p.read_text().splitlines()
p.write_text('\n'.join('RUSTDESK_IMAGE='+sys.argv[2] if x.startswith('RUSTDESK_IMAGE=') else x for x in lines)+'\n')
PY
  chmod 600 "$BASE/.env"
  compose up -d
  log 'Update applied; check logs, then test an actual RustDesk connection'
  compose ps
}
stop_server(){ need_mac; need_deploy; compose stop; }

case "$CMD" in
  doctor) doctor;;
  docker) docker_setup;;
  client) client_setup; configure_sleep; client_report;;
  readiness) need_mac; client_report;;
  setup) full_setup;;
  server) server_setup;;
  status) status;;
  check) need_mac; check_host;;
  key) need_mac; public_key;;
  backup) backup;;
  update) update;;
  stop) stop_server;;
  help|-h|--help) usage;;
  *) usage; exit 1;;
esac
