# Polaris

C2 server manager and real-time connection dashboard for the LandNav rogue device platform. Runs on the C2 server (the C2 server) and manages all server-side processes.

## Features

- **Process supervisor** — starts, stops, restarts, and monitors chisel server, iodined, and msfconsole as independent detached processes
- **Process independence** — managed processes survive Polaris restarts; Polaris re-attaches via PID files on startup
- **Chisel session tracking** — parses `/var/log/chisel.log` for active reverse tunnel sessions, maps server-side ports to registered devices
- **Service health** — monitors chisel server, iodined, and msfconsole process status
- **Device registry integration** — reads `device_registry.json` for hostname-to-device_id mapping
- **Clickable port links** — direct access to forwarded Vantage, Crucible, and Nessus instances per device
- **Real-time updates** — SocketIO pushes connection/disconnection events to the dashboard instantly
- **tmux integration** — msfconsole runs in a named tmux session (`polaris-msf`) for interactive access
- **Port conflict handling** — detects and resolves conflicts on chisel server port (kills stale processes, stops meshcentral)
- **UFW management** — auto-opens firewall ports for managed services
- **TLS auto-generation** — creates self-signed certs for chisel server if not configured

## Architecture

```
Polaris (c2-server:5900)
├── Flask + SocketIO dashboard
├── Process supervisor
│   ├── chisel server    (detached, PID file, /var/log/chisel.log)
│   ├── iodined          (detached, PID file, /var/log/iodined.log)
│   └── msfconsole       (tmux session 'polaris-msf', listener.rc)
├── Health monitor threads
│   ├── ChiselMonitor    (tail chisel.log, parse sessions)
│   ├── ServiceHealthCheck (pgrep)
│   ├── IodineMonitor    (Phase C)
│   └── MetasploitMonitor (file-based, Phase C)
└── Listener scripts (standalone, in repo)
    ├── listener.rc      (MSF resource script — handler + SOCKS)
    ├── portfwd.rc       (port forward commands)
    ├── auto_commands.txt (autoroute)
    └── prep_handler.py  (config sync)
```

## Requirements

- Python 3.10+
- Flask, Flask-SocketIO
- LandNav `config.yaml` and `device_registry.json`
- chisel, iodine, msfconsole (for managed processes)
- tmux (for interactive msfconsole access)

## Installation

```bash
cd /opt/pr0t31n/Polaris
python3 install.py
```

## Usage

```bash
python3 app.py
```

Dashboard available at `http://c2-server:5900`

### Service Control

From the dashboard or via API:

| Endpoint | Method | Action |
|---|---|---|
| `/api/status` | GET | Full status |
| `/api/service/<name>/start` | POST | Start a service |
| `/api/service/<name>/stop` | POST | Stop a service |
| `/api/service/<name>/restart` | POST | Restart a service |
| `/api/start-all` | POST | Start all enabled services |
| `/api/stop-all` | POST | Stop all services |

Service names: `chisel_server`, `iodined`, `msfconsole`

### Interactive Metasploit

```bash
tmux attach -t polaris-msf
```

Detach with `Ctrl+B, D` — msfconsole keeps running.

## Port Mapping

Each registered device gets sequential ports offset by device_id:

| Service  | Base Port | Device 0  | Device 1  |
|----------|-----------|-----------|-----------|
| Vantage  | 25100     | 25100     | 25101     |
| Crucible | 25500     | 25500     | 25501     |
| Nessus   | 28834     | 28834     | 28835     |
| SOCKS    | 1081      | 1081      | 1082      |

## Data Sources

| Source | What | How |
|--------|------|-----|
| `/var/log/chisel.log` | Active chisel sessions + ports | Tail + regex parse |
| `device_registry.json` | Hostname to device_id mapping | JSON read |
| `config.yaml` | C2 host, base ports, service config | YAML read |
| Process list | Service health (chisel/iodined/msf) | `pgrep` |
| `/var/run/landnav_sessions.json` | Metasploit sessions (Phase C) | JSON read |
| PID files (`/var/run/polaris_*.pid`) | Managed process state | PID read + kill -0 |

## Integration

- **LandNav** — reads the same `config.yaml` and `device_registry.json`
- **Arsenal** — registered for install/update management
- **tmux** — msfconsole runs in `polaris-msf` session
