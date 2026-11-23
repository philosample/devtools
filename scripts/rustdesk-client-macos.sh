#!/usr/bin/env bash
# Configure a native macOS RustDesk client for a RustDesk Server OSS instance.
set -Eeuo pipefail

APP=${0##*/}
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
  ./rustdesk-client-macos.sh help

Options:
  --install-service   Install and verify RustDesk's macOS background helper.
  --client-autostart  Add RustDesk as a user Login Item.
  --skip-update       Keep the installed RustDesk app version.

The script installs/updates the official Homebrew RustDesk cask, backs up the
existing client config, imports the OSS ID server and public key, then opens
RustDesk. It does not set a permanent password or grant macOS privacy access.
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
    setup) shift;;
    *) die "Unknown option: $1 (try help)";;
  esac
done
[[ $(uname -s) == Darwin ]] || die 'This script targets macOS only'
[[ $EUID -ne 0 ]] || die 'Run as your normal user, not with sudo'
[[ -n $HOST && ${#HOST} -le 253 && $HOST =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ && $HOST != *..* && $HOST != *. ]] || die 'Provide --host as a DNS name or IPv4 address, without a scheme or port'
[[ -n $KEY && $KEY =~ ^[A-Za-z0-9+/=]+$ ]] || die 'Provide --key with the contents of id_ed25519.pub'

find_brew(){
  command -v brew >/dev/null 2>&1 && return 0
  local b
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x $b ]]; then eval "$("$b" shellenv)"; return 0; fi
  done
  return 1
}
find_brew || die 'Homebrew is required; install it from brew.sh, then rerun'

if [[ -e /Applications/RustDesk.app ]] && pgrep -f '^/Applications/RustDesk.app/Contents/MacOS/RustDesk([[:space:]]|$)' >/dev/null 2>&1; then
  die 'Quit RustDesk first so its running process cannot overwrite imported settings'
fi

if ((SKIP_UPDATE)); then
  brew list --cask rustdesk >/dev/null 2>&1 || die 'RustDesk is not installed; omit --skip-update to install it'
else
  log 'Installing or updating RustDesk from the Homebrew cask'
  if brew list --cask rustdesk >/dev/null 2>&1; then
    brew upgrade --cask rustdesk
  elif [[ -d /Applications/RustDesk.app ]]; then
    brew install --cask --force rustdesk
  else
    brew install --cask rustdesk
  fi
fi

APP_PATH='/Applications/RustDesk.app'
[[ -d $APP_PATH ]] || die "RustDesk app not found at $APP_PATH"
BIN="$APP_PATH/Contents/MacOS/RustDesk"
[[ -x $BIN ]] || die "RustDesk executable not found at $BIN"

CONFIG_DIR="$HOME/Library/Preferences/com.carriez.RustDesk"
SUPPORT_DIR="$HOME/Library/Application Support/rustdesk-setup"
CONFIG="$CONFIG_DIR/RustDesk2.toml"
LEGACY_CONFIG="$CONFIG_DIR/RustDesk.toml"
mkdir -p "$CONFIG_DIR" "$SUPPORT_DIR/backups"
STAMP=$(date +%Y%m%dT%H%M%S)-$$
BACKUP_DIR=''
if [[ -f $CONFIG || -f $LEGACY_CONFIG ]]; then
  BACKUP_DIR="$SUPPORT_DIR/backups/$STAMP"
  mkdir -p "$BACKUP_DIR"
fi

if ((!INSTALL_SERVICE)); then
  die 'Client setup needs RustDesk service access to save network settings; rerun with --install-service'
fi
log 'Authorizing RustDesk service installation; macOS may request your administrator password'
sudo -v
if [[ -n $BACKUP_DIR ]]; then
  [[ ! -f $CONFIG ]] || { sudo cat "$CONFIG" > "$BACKUP_DIR/RustDesk2.toml"; chmod 600 "$BACKUP_DIR/RustDesk2.toml"; }
  [[ ! -f $LEGACY_CONFIG ]] || { sudo cat "$LEGACY_CONFIG" > "$BACKUP_DIR/RustDesk.toml"; chmod 600 "$BACKUP_DIR/RustDesk.toml"; }
  log "Saved existing RustDesk settings to $BACKUP_DIR"
fi

# Older versions of this helper ran the option CLI through sudo, which could
# create a root-owned config. Repair that file before RustDesk's user service
# reads it so the normal user's instance can persist its own configuration.
if [[ -e $CONFIG ]]; then
  sudo chown "$(id -un):$(id -gn)" "$CONFIG"
  chmod 600 "$CONFIG"
fi

log 'Requesting RustDesk service installation or repair'
"$BIN" --install-service || die 'RustDesk service installation failed or was cancelled'
SERVICE_PLIST='/Library/LaunchAgents/com.carriez.RustDesk_server.plist'
SERVICE_LABEL='com.carriez.RustDesk_server'
USER_UID=$(id -u)
SERVICE_READY=0
for _ in {1..60}; do
  if [[ -f $SERVICE_PLIST ]] && launchctl print "gui/$USER_UID/$SERVICE_LABEL" >/dev/null 2>&1; then
    SERVICE_READY=1
    break
  fi
  sleep 1
done
((SERVICE_READY)) || die 'RustDesk user service did not become active. Approve its macOS administrator prompt, then rerun; no client settings were changed.'

log 'Starting the RustDesk app for this user'
open -a "$APP_PATH"
APP_READY=0
for _ in {1..30}; do
  if pgrep -f '^/Applications/RustDesk.app/Contents/MacOS/RustDesk([[:space:]]|$)' >/dev/null 2>&1; then
    APP_READY=1
    break
  fi
  sleep 1
done
((APP_READY)) || die 'RustDesk did not start for the logged-in user; no client settings were changed.'

# Keep this CLI in the logged-in user's context so RustDesk uses the user's
# service/config instead of root's fallback configuration.
OLD_HOST=$("$BIN" --option custom-rendezvous-server)
OLD_RELAY=$("$BIN" --option relay-server)
OLD_API=$("$BIN" --option api-server)
OLD_KEY=$("$BIN" --option key)
set_option(){ "$BIN" --option "$1" "$2" >/dev/null; }
restore_options(){
  set_option custom-rendezvous-server "$OLD_HOST"
  set_option relay-server "$OLD_RELAY"
  set_option api-server "$OLD_API"
  set_option key "$OLD_KEY"
}
if ! set_option custom-rendezvous-server "$HOST" || ! set_option relay-server '' || ! set_option api-server '' || ! set_option key "$KEY"; then
  restore_options
  die 'RustDesk could not save the server settings; previous values were restored'
fi
if [[ $("$BIN" --option custom-rendezvous-server) != "$HOST" || $("$BIN" --option relay-server) != '' || $("$BIN" --option api-server) != '' || $("$BIN" --option key) != "$KEY" ]]; then
  restore_options
  die 'RustDesk did not confirm the self-hosted server settings; previous values were restored'
fi
if [[ ! -f $CONFIG ]]; then
  restore_options
  die 'RustDesk reported success but did not create the logged-in user config; previous values were restored where possible'
fi
if [[ $(stat -f '%u' "$CONFIG") != "$USER_UID" ]]; then
  restore_options
  die 'RustDesk config is not owned by the logged-in user; previous values were restored'
fi
chmod 600 "$CONFIG"
log "Saved RustDesk ID server $HOST and verified its public key"

if ((CLIENT_AUTOSTART)); then
  log 'Adding RustDesk to this user account Login Items'
  osascript <<'OSA' || warn 'Could not add Login Item automatically; add RustDesk under System Settings → General → Login Items'
tell application "System Events"
  if not (exists login item "RustDesk") then
    make login item at end with properties {name:"RustDesk", path:"/Applications/RustDesk.app", hidden:false}
  end if
end tell
OSA
fi

log 'Opening RustDesk'
open -a "$APP_PATH"
cat <<'DONE'

Server settings saved. In RustDesk, finish the user-controlled steps:
  • Grant Screen & System Audio Recording and Accessibility in System Settings → Privacy & Security.
  • Configure a strong permanent password only if unattended access is needed.
  • Test a connection on the same LAN. For use away from home, this LAN address
    needs a VPN or a separately planned external-access setup.
DONE
