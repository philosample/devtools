# RustDesk macOS setup (client and self-hosted Mac mini)

The standalone installer is [`scripts/rustdesk-all-in-one-macos.sh`](../scripts/rustdesk-all-in-one-macos.sh). It installs and verifies Colima, Docker CLI, Compose and Buildx, repairs missing Docker Desktop credential-helper references when detected, and prepares a native RustDesk client. The same script can deploy the free OSS `hbbs` and `hbbr` server containers on an always-on Mac mini.

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
3. When the private server is available, in **Settings → Network → ID/Relay Server**, enter its hostname/IP and the server's `id_ed25519.pub` public key. Leave relay and API fields empty initially for the OSS setup. A `--host`/`--key` argument prints configuration guidance and tests ports; it **does not silently save protected RustDesk settings**.
4. Verify control from a second computer over a separate network, then after logout and reboot. FileVault pre-boot unlock is not bypassed.

If only client installation is required, run:

```sh
./scripts/rustdesk-all-in-one-macos.sh client --install-service --client-autostart
```

The `--prevent-sleep` option is opt-in. Inspect `pmset -g custom` before making power policy changes, particularly on laptops.

## Always-on Mac mini: server

On the Mac mini, run:

```sh
./scripts/rustdesk-all-in-one-macos.sh server --host YOUR_DDNS_HOST --autostart
./scripts/rustdesk-all-in-one-macos.sh key
./scripts/rustdesk-all-in-one-macos.sh status
```

The server stores Compose configuration and private identity keys under `~/Services/rustdesk` (override with `RUSTDESK_HOME`). Keep its private `id_ed25519` confidential and **off Git**; distribute only the public key.

Reserve the Mac mini's LAN IP in your router, then forward **TCP 21115, 21116, 21117 and UDP 21116** to it. Use DDNS if the external IP changes. Compare the router WAN IP to the observed public IPv4 to detect CGNAT/double NAT; this design needs external reachability. Test from outside the LAN, not only through a router that may or may not provide NAT loopback.

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

`check` checks TCP connectivity only. A full acceptance test must include UDP signaling, actual desktop control, unattended password authentication, and both direct and relayed connections.

### Troubleshooting and limits

- A `docker-credential-desktop` missing error usually indicates stale Docker Desktop credentials in `~/.docker/config.json`; the installer backs up and corrects missing-helper references rather than deleting Docker data.
- Use `docker --context colima ...` to avoid depending on the system's default Docker context.
- `readiness` reports service/Login Item detection and AC sleep, but it cannot prove macOS TCC permission grants, permanent password correctness, ID server trust, or external reachability.
- For server logs: `cd ~/Services/rustdesk && docker --context colima compose -p rustdesk-oss logs --tail=100`.
- Don't put server private keys, passwords, local readiness reports, or personal machine configuration into this repository.
