#!/usr/bin/env bash
# Configure a native macOS RustDesk client for a RustDesk Server OSS instance.
set -Eeuo pipefail

APP=${0##*/}
CMD='setup'
HOST=''
KEY=''
INSTALL_SERVICE=0
CLIENT_AUTOSTART=0
SKIP_UPDATE=0

log(){ printf '\n==> %s\n' "$*"; }
warn(){ printf 'WARNING: %s\n' "$*" >&2; }
die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage(){ cat <<'HELP'
RustDesk macOS client setup (Apple Silicon / Intel)

  ./rustdesk-client-macos.sh setup --host LAN_IP_OR_DNS --key PUBLIC_KEY [options]
  ./rustdesk-client-macos.sh verify --host LAN_IP_OR_DNS --key PUBLIC_KEY
  ./rustdesk-client-macos.sh help

Options for setup:
  --install-service   Require the macOS background service (install it via
                      RustDesk > Settings > General > Service Control).
  --client-autostart  Add RustDesk as a user Login Item.
  --skip-update       Keep the installed RustDesk app version.

setup installs/opens the client safely, then provides user-controlled Network
configuration steps if needed. It never overwrites RustDesk2.toml or silently
changes server settings. verify reads the saved user config without modifying
it. An actual remote session is the final connection check.
Run as your normal user, not with sudo.
HELP
}
while (($#)); do
  case "$1" in
    --host|--key)
      (($# >= 2)) || die "$1 requires a value"
      if [[ $1 == --host ]]; then HOST=$2; else KEY=$2; fi
      shift 2;;
    --install-service) INSTALL_SERVICE=1; shift;;
    --client-autostart) CLIENT_AUTOSTART=1; shift;;
    --skip-update) SKIP_UPDATE=1; shift;;
    help|-h|--help) usage; exit 0;;
    setup) CMD='setup'; shift;;
    verify) CMD='verify'; shift;;
    *) die "Unknown option: $1 (try help)";;
  esac
done
[[ $(uname -s) == Darwin ]] || die 'This script targets macOS only'
[[ $EUID -ne 0 ]] || die 'Run as your normal user, not with sudo'
[[ -n $HOST && ${#HOST} -le 253 && $HOST =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ && $HOST != *..* && $HOST != *. ]] || die 'Provide --host as a DNS name or IPv4 address, without a scheme or port'
[[ -n $KEY && $KEY =~ ^[A-Za-z0-9+/=]+$ ]] || die 'Provide --key with the contents of id_ed25519.pub'
if [[ $CMD == verify ]] && ((INSTALL_SERVICE || CLIENT_AUTOSTART || SKIP_UPDATE)); then
  die 'verify is read-only and does not accept setup flags'
fi

APP_PATH='/Applications/RustDesk.app'
BIN="$APP_PATH/Contents/MacOS/RustDesk"
PATTERN='^/Applications/RustDesk[.]app/Contents/MacOS/RustDesk([[:space:]]|$)'
find_brew(){
  command -v brew >/dev/null 2>&1 && return 0
  local b
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x $b ]]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}
# Never replace the application while any RustDesk process is active, including
# the headless --server process that persists after the window is closed.
if [[ $CMD == setup ]]; then
  if [[ -d $APP_PATH ]] && pgrep -f "$PATTERN" >/dev/null 2>&1; then
    log 'RustDesk process is active; preserving its installed app version'
  elif ((SKIP_UPDATE)) && [[ -d $APP_PATH ]]; then
    log 'Keeping installed RustDesk version (--skip-update)'
  elif [[ -d $APP_PATH ]] && ! find_brew; then
    warn 'Homebrew unavailable; keeping existing RustDesk app'
  else
    find_brew || die 'Homebrew is required to install RustDesk; see brew.sh'
    if brew list --cask rustdesk >/dev/null 2>&1; then
      log 'Updating Homebrew-managed RustDesk app'
      brew upgrade --cask rustdesk
    elif [[ -d $APP_PATH ]]; then
      warn 'Existing app is not Homebrew-managed; preserving without overwrite'
    else
      log 'Installing the official RustDesk Homebrew cask'
      brew install --cask rustdesk
    fi
  fi
fi
[[ -x $BIN ]] || die "RustDesk executable not found at $BIN"

CONFIG="$HOME/Library/Preferences/com.carriez.RustDesk/RustDesk2.toml"
SERVICE_PLIST='/Library/LaunchAgents/com.carriez.RustDesk_server.plist'
SERVICE_DAEMON='/Library/LaunchDaemons/com.carriez.RustDesk_service.plist'
SERVICE_LABEL='com.carriez.RustDesk_server'

# RustDesk 1.5.3 on macOS: --install-service checks installation only; it does
# NOT create the launchd service. --option is root/installed gated and can
# silently fail. Do not use either CLI for client provisioning or write TOML.
verify_network(){
  command -v python3 >/dev/null 2>&1 || {
    warn 'Python 3.11+ is needed for read-only TOML verification'
    return 2
  }
  python3 - "$CONFIG" "$HOST" "$KEY" <<'PY'
import sys
from pathlib import Path
try:
    import tomllib
except ImportError:
    print("UNVERIFIED: Python 3.11+ is needed to inspect RustDesk2.toml")
    sys.exit(2)
path, expected_host, expected_key = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
try:
    with path.open("rb") as file:
        saved = tomllib.load(file)
except (OSError, ValueError) as exc:
    print(f"UNVERIFIED: Cannot read RustDesk2.toml ({type(exc).__name__})")
    sys.exit(2)
options = saved.get("options", {})
if not isinstance(options, dict):
    print("UNVERIFIED: RustDesk saved options are malformed")
    sys.exit(2)
if (
    options.get("custom-rendezvous-server") == expected_host
    and options.get("key") == expected_key
    and not options.get("relay-server")
    and not options.get("api-server")
):
    print("VERIFIED: saved ID server and public key match (file check only)")
    sys.exit(0)
print("PENDING: saved RustDesk Network settings differ from requested values")
sys.exit(1)
PY
}
service_ready(){
  [[ -f $SERVICE_PLIST && -f $SERVICE_DAEMON ]] &&
    launchctl print "gui/$(id -u)/$SERVICE_LABEL" >/dev/null 2>&1
}
network_steps(){
  cat <<EOF

Configure RustDesk with its supported user interface:
  RustDesk > Settings > Network > Unlock Network Settings
  ID Server: $HOST
  Key: $KEY
  Relay Server: leave blank
  API Server: leave blank
Save/Apply settings, then test an actual connection to your remote Mac.
EOF
}
if [[ $CMD == verify ]]; then
  verify_network
  exit $?
fi

log 'Opening RustDesk; existing server settings are not modified'
open -a "$APP_PATH" || die 'Could not open RustDesk; open it manually'
if ((CLIENT_AUTOSTART)); then
  log 'Adding RustDesk as a user Login Item'
  osascript <<'OSA' || warn 'Could not add Login Item; use System Settings > General > Login Items'
tell application "System Events"
  if not (exists login item "RustDesk") then
    make login item at end with properties {name:"RustDesk", path:"/Applications/RustDesk.app", hidden:false}
  end if
end tell
OSA
fi
if verify_network; then
  log 'Self-hosted server settings are already saved'
else
  warn 'Network settings are not verified; configure them in RustDesk'
  network_steps
  warn 'Run the verify command after saving Network settings'
fi
if ((INSTALL_SERVICE)); then
  if service_ready; then
    log 'RustDesk user background service detected'
  else
    cat <<'SERVICE'

ACTION REQUIRED: unattended access into this Mac was requested.
In RustDesk: Settings > General > Service Control > Install.
Approve the macOS administrator prompt and rerun setup afterwards.
On macOS, RustDesk --install-service is only an installation check.
Outbound-only connections do NOT require this background service.
SERVICE
    exit 2
  fi
fi
log 'RustDesk client app prepared; complete/verify a real connection in the UI'
