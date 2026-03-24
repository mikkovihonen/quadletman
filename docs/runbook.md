# Runbook

This document covers first-time setup, day-to-day operations, and common troubleshooting
steps for a quadletman installation.

## After Installation

### 1. Verify the service is running

```bash
sudo systemctl status quadletman
```

If it is not running, start it:

```bash
sudo systemctl enable --now quadletman
```

### 2. Open the web UI

Navigate to `http://<host>:8080` in a browser.

Log in with an **OS user account** that belongs to the `sudo` or `wheel` group. quadletman
uses PAM — no separate password is needed. The same credentials you use for `sudo` work here.

> If you are running quadletman behind a reverse proxy over HTTPS, set
> `QUADLETMAN_SECURE_COOKIES=true` in `/etc/quadletman/quadletman.env` and restart the
> service so session cookies get the `Secure` flag.

### 3. Create your first compartment

A **compartment** is a named, isolated group of containers. Each compartment gets its own
Linux system user and its own Podman environment.

1. On the dashboard click **New compartment**.
2. Enter a short ID (lowercase letters, digits, hyphens — e.g. `my-app`).
3. Click **Create**. quadletman creates the `qm-my-app` system user, initialises Podman
   storage, and enables `loginctl linger` so the unit persists across reboots.

### 4. Add a container

1. Open the compartment you just created.
2. Click **Add container**.
3. Fill in at minimum:
   - **Name** — the unit file name (e.g. `web`)
   - **Image** — a full OCI image reference (e.g. `docker.io/library/nginx:latest`)
4. Click **Save**. The Quadlet `.container` unit file is written immediately.

### 5. Start the container

Click **Start** in the compartment view. quadletman calls `systemctl --user daemon-reload`
followed by `systemctl --user start` for each unit in the compartment.

The container status appears in the compartment panel. Click **Logs** to tail the journal
output live.

---

## Configuration

Configuration is loaded from environment variables with the `QUADLETMAN_` prefix.

| Variable | Default | Description |
|---|---|---|
| `QUADLETMAN_PORT` | `8080` | Listening port (ignored when `QUADLETMAN_UNIX_SOCKET` is set) |
| `QUADLETMAN_HOST` | `0.0.0.0` | Listening address (ignored when `QUADLETMAN_UNIX_SOCKET` is set) |
| `QUADLETMAN_UNIX_SOCKET` | *(empty)* | Absolute path to a Unix domain socket; when set, the app binds to the socket instead of a TCP port |
| `QUADLETMAN_LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `QUADLETMAN_DB_PATH` | `/var/lib/quadletman/quadletman.db` | SQLite database path |
| `QUADLETMAN_VOLUMES_BASE` | `/var/lib/quadletman/volumes` | Volume storage root |
| `QUADLETMAN_ALLOWED_GROUPS` | `["sudo","wheel"]` | OS groups permitted to log in |
| `QUADLETMAN_SECURE_COOKIES` | `false` | Set `true` when serving over HTTPS |
| `QUADLETMAN_SERVICE_USER_PREFIX` | `qm-` | Prefix for compartment Linux system users |
| `QUADLETMAN_PROCESS_MONITOR_INTERVAL` | `60` | Seconds between process monitor checks |
| `QUADLETMAN_CONNECTION_MONITOR_INTERVAL` | `60` | Seconds between connection monitor checks |
| `QUADLETMAN_CAPTURE_TIME_WAIT` | `false` | Include TIME_WAIT connections in the connection monitor. Enable on slirp4netns to capture short-lived inbound connections (see [Connection monitor notes](development.md#platform-notes)) |
| `QUADLETMAN_AGENT_SOCKET` | `/run/quadletman/agent.sock` | Unix socket path for per-user monitoring agents (non-root mode only) |
| `QUADLETMAN_TEST_AUTH_USER` | *(empty)* | **Never set in production** — bypasses PAM auth entirely; exists solely for Playwright E2E tests |

### Persisting configuration

When installed via the RPM or DEB package, the canonical place to set these variables is
the environment file read by the systemd unit:

```
/etc/quadletman/quadletman.env
```

The file uses simple `KEY=VALUE` syntax (no `export`, no quoting needed for plain values):

```bash
# /etc/quadletman/quadletman.env

QUADLETMAN_PORT=8080
QUADLETMAN_LOG_LEVEL=INFO
QUADLETMAN_SECURE_COOKIES=true
```

Create or edit the file, then apply the changes:

```bash
sudo systemctl restart quadletman
sudo systemctl status quadletman   # confirm it started cleanly
```

> The env file is not created automatically. Create it the first time you need to override
> a default:
> ```bash
> sudo mkdir -p /etc/quadletman
> sudo touch /etc/quadletman/quadletman.env
> sudo chmod 640 /etc/quadletman/quadletman.env
> ```

### Changing configuration on a running instance

All settings are read once at startup. **A restart is always required** — there is no
hot-reload. The correct procedure depends on how disruptive the change is:

#### Non-disruptive changes (log level, allowed groups, secure cookies)

These affect only new requests after restart. Connected users are dropped and must log in
again.

```bash
sudo nano /etc/quadletman/quadletman.env   # make your change
sudo systemctl restart quadletman
```

#### Port or bind address change

The service will listen on the new address after restart. If you are also behind a
firewall, update the firewall rule **before** restarting so there is no gap:

```bash
# Example: move from port 8080 to 9090
sudo firewall-cmd --permanent --remove-port=8080/tcp
sudo firewall-cmd --permanent --add-port=9090/tcp
sudo firewall-cmd --reload
# Then update the env file and restart
sudo systemctl restart quadletman
```

If you are behind a reverse proxy, update the `proxy_pass` target in the proxy config and
reload the proxy **before** restarting quadletman so requests do not fail during the
transition.

#### Database path or volumes base change

These settings affect where quadletman reads and writes persistent data. **Changing them
without moving the data first will cause data loss or a startup failure.**

1. **Stop the service** before touching any paths:
   ```bash
   sudo systemctl stop quadletman
   ```
2. **Move the data** to the new location:
   ```bash
   sudo mv /var/lib/quadletman/quadletman.db /new/path/quadletman.db
   # or for volumes base:
   sudo mv /var/lib/quadletman/volumes /new/path/volumes
   ```
3. If the new path is outside `/var/lib/quadletman/`, **restore SELinux contexts**:
   ```bash
   sudo restorecon -Rv /new/path/
   ```
4. Update the env file with the new path, then start the service:
   ```bash
   sudo systemctl start quadletman
   sudo systemctl status quadletman
   ```

---

## Firewall

The service listens on port 8080 by default. If the host runs `firewalld`:

```bash
sudo firewall-cmd --permanent --add-port=8080/tcp
sudo firewall-cmd --reload
```

For a reverse proxy setup, open 80/443 instead and keep 8080 closed externally.

---

## SSH Tunnel Access (Unix Socket Mode)

For maximum isolation, quadletman can bind to a Unix domain socket instead of a TCP port.
No TCP port is opened, so no other service on the host can reach the application over the
network.  The socket is owned by the `quadletman` user and group-readable by `sudo`/`wheel` members
(mode `0660`), so access requires both group membership and an SSH tunnel that forwards to
the socket.

### Enable Unix socket mode

Add to `/etc/quadletman/quadletman.env`:

```bash
QUADLETMAN_UNIX_SOCKET=/run/quadletman/quadletman.sock
```

The `RuntimeDirectory=quadletman` directive in the systemd unit ensures `/run/quadletman/`
is created automatically with the correct ownership.  During startup quadletman sets the
socket group to the first group from `QUADLETMAN_ALLOWED_GROUPS` that exists on the system
(`sudo` on Debian/Ubuntu, `wheel` on RHEL/Fedora) and sets mode `0660`, so any user in
those groups can connect through an SSH tunnel without requiring root.

After changing the env file:

```bash
sudo systemctl restart quadletman
sudo systemctl status quadletman
ls -la /run/quadletman/quadletman.sock   # should show srw-rw---- quadletman sudo (or wheel)
```

### Connect via SSH tunnel

**Single hop** (your machine → target host):

```bash
ssh -L 8080:/run/quadletman/quadletman.sock user@targethost
```

Then open `http://localhost:8080` in your browser.

**Double hop** (your machine → jump host → target host):

```bash
ssh -J jumphost -L 8080:/run/quadletman/quadletman.sock user@targethost
```

The `-L local_port:remote_socket_path` syntax is `StreamLocalForward` — SSH forwards your
local TCP port directly to the Unix socket on the remote end.  No TCP port is opened on the
target host at all.

### Persistent tunnel with `~/.ssh/config`

```
Host quadletman
    HostName targethost
    User     myuser
    ProxyJump jumphost          # omit if no jump host needed
    LocalForward 8080 /run/quadletman/quadletman.sock
    ExitOnForwardFailure yes
    ServerAliveInterval 30
```

Then connect with:

```bash
ssh -fN quadletman        # -f: background, -N: no remote command
```

---

## Reverse Proxy (HTTPS)

Running behind nginx or Caddy is recommended for production. Example nginx snippet:

```nginx
server {
    listen 443 ssl;
    server_name quadletman.example.com;

    ssl_certificate     /etc/ssl/certs/quadletman.crt;
    ssl_certificate_key /etc/ssl/private/quadletman.key;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # WebSocket support (live logs + terminal)
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

After adding HTTPS, set `QUADLETMAN_SECURE_COOKIES=true` in the env file and restart.

---

## Day-to-Day Operations

### View application logs

```bash
sudo journalctl -u quadletman -f
```

For host-mutation audit events only:

```bash
sudo journalctl -u quadletman | grep 'quadletman.host'
```

### Back up the database

The dashboard has a **Download DB backup** link (top-right menu) that streams a live
SQLite backup. For automated backups, copy or snapshot:

```
/var/lib/quadletman/quadletman.db
```

### Restart all containers in a compartment

Open the compartment in the UI and click **Restart all**, or via CLI:

```bash
COMPARTMENT=my-app
UID=$(id -u qm-$COMPARTMENT)
sudo -u qm-$COMPARTMENT \
  env XDG_RUNTIME_DIR=/run/user/$UID \
      DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID/bus \
  systemctl --user restart '*'
```

### Pull the latest image for a container

In the compartment view, open the container, click **Pull image**, then **Restart**.

### Add a registry login

If your container image is on a private registry, open the compartment → **Registry
logins** and enter the registry URL, username, and password. Credentials are stored in the
compartment root's `~/.config/containers/auth.json` and persist across reboots.

---

## Troubleshooting

### Login fails ("Forbidden" or 401)

- Confirm the OS user is in the `sudo` or `wheel` group:
  ```bash
  groups <username>
  ```
- Confirm PAM is working:
  ```bash
  sudo journalctl -u quadletman | grep -i pam
  ```
- If the allowed groups were changed via `QUADLETMAN_ALLOWED_GROUPS`, restart the service.

### Container will not start

1. Check the unit status:
   ```bash
   COMPARTMENT=my-app
   UID=$(id -u qm-$COMPARTMENT)
   sudo -u qm-$COMPARTMENT \
     env XDG_RUNTIME_DIR=/run/user/$UID \
         DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID/bus \
     systemctl --user status '<container-name>.service'
   ```
2. Check the journal for the unit:
   ```bash
   sudo -u qm-$COMPARTMENT \
     env XDG_RUNTIME_DIR=/run/user/$UID \
         DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID/bus \
     journalctl --user -u '<container-name>.service' -n 50
   ```
3. Common causes:
   - Image not pulled yet — click **Pull image** in the UI first.
   - Port already in use — check for conflicts with `ss -tlnp`.
   - Missing secret — verify all referenced secrets exist in the compartment's **Secrets** tab.

### `loginctl linger` is not active (containers lost after reboot)

```bash
sudo loginctl enable-linger qm-<compartment-id>
```

quadletman enables this automatically on compartment creation, but it can be inadvertently
disabled. The compartment **Status** panel shows the linger state.

### Quadlet unit file not picked up by systemd

After editing a unit file outside the UI, reload the daemon:

```bash
COMPARTMENT=my-app
UID=$(id -u qm-$COMPARTMENT)
sudo -u qm-$COMPARTMENT \
  env XDG_RUNTIME_DIR=/run/user/$UID \
      DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID/bus \
  systemctl --user daemon-reload
```

The UI **Sync** button does this automatically.

### SELinux denials (containers cannot read volumes)

quadletman labels volumes with `container_file_t` on creation. If a volume was created
outside the UI or the context was lost, relabel it:

```bash
sudo restorecon -Rv /var/lib/quadletman/volumes/<compartment-id>/<volume-name>/
```

Or use the **Relabel** button in the volume detail view.

### "unsupported key" errors in container unit files

Your Podman version is older than required for a feature you have configured. The
compartment **Status** panel shows the detected Podman version. Either:
- Upgrade Podman, or
- Remove the unsupported field from the container definition in the UI.

---

## Upgrading

Build the new package and install it over the existing one — see
**[docs/packaging.md — Upgrading](packaging.md#upgrading)** for the exact commands.

The service applies any pending database migrations automatically on startup.

---

## Uninstalling

### RPM

```bash
sudo dnf remove quadletman
```

### DEB

```bash
sudo apt remove quadletman
```

Data in `/var/lib/quadletman/` and the `qm-*` system users are **not** removed
automatically. To clean up completely:

```bash
# Remove all compartment users (adjust the list as needed)
for user in $(getent passwd | awk -F: '$1 ~ /^qm-/ {print $1}'); do
  sudo userdel -r "$user"
done

# Remove application data
sudo rm -rf /var/lib/quadletman/
```
