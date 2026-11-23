# RustDesk macOS setup (client and self-hosted Mac mini)

The standalone server installer is [`scripts/rustdesk-all-in-one-macos.sh`](../scripts/rustdesk-all-in-one-macos.sh). The focused [`scripts/rustdesk-client-macos.sh`](../scripts/rustdesk-client-macos.sh) installs or updates the native RustDesk app when safe, opens it, and verifies saved server settings read-only. RustDesk Network settings are entered by the user in the app; outbound connections do not require a background service. The all-in-one script deploys the free OSS `hbbs` and `hbbr` containers and configures the Mac mini's native RustDesk client to use that server.

The CLI is independent of `python3 -m codex_devtools` and is **opt-in**. Do not run with `sudo`; individual operations such as `--prevent-sleep` prompt for authorization.

## Prerequisites

macOS on Apple Silicon or Intel, Homebrew (or opt into `--install-homebrew`), access to the internet for package and image downloads, and a logged-in user account. Existing Colima VM settings and RustDesk server keys are preserved. Docker commands explicitly target the `colima` context rather than modifying the default context.

## Current Mac: client + unattended host

From the repository root:

```sh
chmod +x scripts/rustdesk-all-in-one-macos.sh
./scripts/rustdesk-all-in-one-macos.sh setup --install-service --client-autostart --prevent-sleep
./scripts/rustdesk-all-in-one-macos.sh readiness
```

`setup` verifies Docker (including a `hello-world` container), launches or installs RustDesk, requests its helper and Login Item, optionally disables **AC idle system sleep** (not display sleep), and writes a readiness report to:

`~/Library/Application Support/rustdesk-setup/readiness.json`

On this Mac the original test recorded RustDesk installed and running, a detected launchd service, a present Login Item and AC idle sleep set to 0. Those diagnostics are observations, not proof of complete unattended access.

Complete/verify these protected steps manually in the RustDesk application and macOS UI:

1. In **System Settings → Privacy & Security**, grant RustDesk Accessibility and Screen & System Audio Recording; Input Monitoring only if required.
2. In RustDesk **Settings → Security**, configure a strong permanent unattended-access password and verify the installed helper/service.
3. When the private server is available, in **Settings → Network → ID/Relay Server**, enter its hostname/IP and the server's `id_ed25519.pub` public key. Leave relay and API fields empty initially for the OSS setup. The focused client helper no longer writes these values by CLI. Save them through RustDesk's Network settings and use the helper's read-only `verify` command to check the local config; test an actual connection to confirm.
4. Verify control from a second computer over a separate network, then after logout and reboot. FileVault pre-boot unlock is not bypassed.

For a **client connecting out to another machine**, use the focused helper.
It does **not** need Docker/Colima, a background service, admin privileges, or a
permanent unattended-access password:

```sh
./scripts/rustdesk-client-macos.sh setup --host 192.168.1.167 --key 'PASTE_PUBLIC_KEY_FROM_SERVER'
```

Read the public key from `id_ed25519.pub` on the Mac mini and replace the
placeholder; **never** copy or share the private `id_ed25519` file. On an
already-running client, the script preserves the application instead of
upgrading it. It opens RustDesk, checks the current user's TOML settings without
modifying them, and prints any missing setup steps.

In the RustDesk app, open **Settings → Network → Unlock Network Settings**,
enter the ID Server and public Key, leave Relay and API blank for this OSS
deployment, then save. To verify saved values without altering them:

```sh
./scripts/rustdesk-client-macos.sh verify --host 192.168.1.167 --key 'PASTE_PUBLIC_KEY_FROM_SERVER'
```

Verification requires Python 3.11+ for its standard-library TOML parser. It
only compares local `RustDesk2.toml` values, not whether the service or
network connection works; connect to the Mac mini's actual RustDesk ID for
acceptance. The `setup` command can successfully prepare the app while
marking Network configuration **PENDING**; it does not claim the GUI values
were saved.

For **incoming unattended access into this Mac**, additionally install the
RustDesk background service through **Settings → General → Service Control →
Install** and approve the macOS administrator prompt. The optional
`--install-service` flag **requires/validates** that service and exits with
status 2 and instructions if it is not active; it does not install it itself.
The optional `--client-autostart` flag adds a user Login Item. This separation
is necessary because RustDesk 1.5.3's macOS `--install-service` CLI is only a
check for existing plists, while the `--option` CLI requires an installed,
privileged context and can silently fail. The helper deliberately avoids both
commands and preserves existing settings.

The all-in-one `client --install-service` path now also verifies an active
service rather than treating the no-op RustDesk CLI as installation. For
outbound-only use, omit that flag.

macOS privacy permissions and the unattended-access password are still
user-controlled steps. The home-LAN address works locally; use a suitable VPN
to reach it from outside the home without exposing server ports.

The `--prevent-sleep` option is opt-in. Inspect `pmset -g custom` before making power policy changes, particularly on laptops.

## Always-on Mac mini: server

Keep both scripts from the repository checkout together on the Mac mini; the server script invokes the companion client helper automatically:

```sh
./scripts/rustdesk-all-in-one-macos.sh server --host 192.168.1.167 --autostart
./scripts/rustdesk-all-in-one-macos.sh key
./scripts/rustdesk-all-in-one-macos.sh status
```

`server` starts the containers and prepares the native RustDesk app, but the macOS GUI must install and authorize the unattended-access service before the host setup is complete. The helper refuses to claim a working host service before that verification; configure ID Server and public Key in RustDesk's Network settings. Complete any macOS administrator prompts. The Mac's RustDesk ID is the target ID to enter on phones and other computers; `192.168.1.167` is the server address that each client uses in Network settings.

RustDesk ID registration requires UDP 21116. The default Colima SSH forwarder carries TCP only, so the `server` command starts Colima with its gRPC forwarder, which carries TCP and UDP. Colima marks gRPC forwarding experimental. If Colima is already running with another forwarder, the installer restarts it only when no unrelated containers are running; if other containers are active, it reports their names and stops before interrupting them. The separate `docker` and `setup` commands preserve the current Colima forwarding mode.

The server stores Compose configuration and private identity keys under `~/Services/rustdesk` (override with `RUSTDESK_HOME`). Keep its private `id_ed25519` confidential and **off Git**; distribute only the public key.

Reserve the Mac mini's LAN IP in your router. For clients only on the home LAN, no port forwarding or DDNS is needed. For clients connecting from outside, forward **TCP 21115, 21116, 21117 and UDP 21116** to it and use DDNS if the public IP changes. Compare the router WAN IP to the observed public IPv4 to detect CGNAT/double NAT; external access requires a reachable public path. Test from outside the LAN, not only through a router that may or may not provide NAT loopback. A VPN into the home network is another option that avoids exposing RustDesk ports directly.

`--autostart` enables Colima on **macOS user login**, not at pre-login boot. After an unattended reboot, the server may remain unavailable until that user session starts. Prevent server sleep and test recovery from a full restart. For zero-login startup requirements, this user-session-based arrangement is not sufficient without a separate supported service design.

## Operations

```sh
./scripts/rustdesk-all-in-one-macos.sh doctor
./scripts/rustdesk-all-in-one-macos.sh readiness
./scripts/rustdesk-all-in-one-macos.sh check --host YOUR_DDNS_HOST
./scripts/rustdesk-all-in-one-macos.sh status
./scripts/rustdesk-all-in-one-macos.sh key
./scripts/rustdesk-all-in-one-macos.sh backup
./scripts/rustdesk-all-in-one-macos.sh update
./scripts/rustdesk-all-in-one-macos.sh stop
```

The `backup` command pauses running server containers for a consistent snapshot, then restores them; it does not intentionally start an already stopped deployment. Backup archives contain the **private** server identity key: encrypt and keep an off-device copy. `update` backs up first, downloads a new official server image digest, updates the pinned image, and requires a subsequent real-world session test.

`check` checks TCP connectivity only. Confirm that the Mac's RustDesk app reaches **Ready** and test a client ID lookup to verify UDP registration. A full acceptance test must include UDP signaling, actual desktop control, unattended password authentication, and both direct and relayed connections.

### Troubleshooting and limits

- A `docker-credential-desktop` missing error usually indicates stale Docker Desktop credentials in `~/.docker/config.json`; the installer backs up and corrects missing-helper references rather than deleting Docker data.
- Use `docker --context colima ...` to avoid depending on the system's default Docker context.
- If a manual setup uses Colima's SSH forwarder, RustDesk's UDP ID registration will fail even when its TCP ports appear open. Use `colima start --port-forwarder grpc` after stopping the profile; gRPC forwarding is experimental and restarting Colima interrupts its running containers.
- `readiness` reports service/Login Item detection and AC sleep, but it cannot prove macOS TCC permission grants, permanent password correctness, ID server trust, or external reachability.
- For server logs: `cd ~/Services/rustdesk && docker --context colima compose -p rustdesk-oss logs --tail=100`.
- Don't put server private keys, passwords, local readiness reports, or personal machine configuration into this repository.
