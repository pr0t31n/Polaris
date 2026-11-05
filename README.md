<details>
<summary><strong>FULL DISCLOSURE</strong> — AI Development & Legal Notice</summary>

---

### AI Development Disclosure

This tool is part of the **pr0t31n toolkit**, a collection of security testing and penetration testing automation tools. The toolkit was created through AI-assisted development, guided by the professional experience and domain expertise of a cybersecurity consultant and penetration tester. Ya, I named it after myself - but to be fair, AI is horrible at naming things.

**The pr0t31n toolkit exists to serve two purposes:**

1. **Automate repetitive penetration testing workflow steps.** Security assessments involve significant amounts of repetitive, procedural work — running sequential tool chains, parsing outputs, correlating findings across tools, and managing engagement data. Automating these steps frees testers to focus on the work that requires human judgment: manual testing, creative attack paths, analysis, and reporting.

2. **Demonstrate the benefits and failures of AI-assisted development.** This toolkit is a real-world case study in building production security tooling with AI assistance. It demonstrates both what AI-assisted development can accomplish and where it falls short. The quality of AI-generated code varies significantly — between context refreshes, the output can range from senior/principal-level software engineering to the work of a still-in-school intern. This inconsistency is itself part of the demonstration.

In the interest of transparency, every tool in the pr0t31n toolkit carries this disclosure. The creator believes that AI-assisted development should be acknowledged openly, not concealed. Users, reviewers, and anyone examining this code should know how it was built so they can evaluate it accordingly.

---

### Legal & Authorization Notice

> **⚠ WARNING: AUTHORIZED USE ONLY**

This tool is designed and intended **exclusively** for use in **authorized security testing engagements**. "Authorized" means **all** of the following conditions are met:

- **Explicit Written Permission.** You possess explicit, documented, written authorization from the owner or authorized representative of the target system(s), network(s), or application(s) to conduct security testing. Verbal agreements are not sufficient.
- **Defined Scope.** The authorization clearly defines the scope of testing, including which systems, networks, IP ranges, applications, and/or endpoints are included. You must not exceed the boundaries of this defined scope under any circumstances.
- **Defined Timeframe.** The authorization specifies the dates and times during which testing is permitted. Testing outside the authorized window is unauthorized testing.
- **Rules of Engagement.** You are operating under a set of rules of engagement (RoE) or equivalent governing document that establishes acceptable testing methods, escalation procedures, emergency contacts, and any restrictions or exclusions.
- **Legal Compliance.** Your testing complies with all applicable local, state, national, and international laws and regulations, including but not limited to the Computer Fraud and Abuse Act (CFAA), the General Data Protection Regulation (GDPR), and equivalent legislation in your jurisdiction.

**Prohibited uses** include but are not limited to: unauthorized access to or testing of any system; testing against systems without explicit written authorization; any activity that violates applicable laws; use for malicious purposes including data theft, extortion, sabotage, or disruption of services; distribution intended to facilitate unauthorized access; and use without a clear legal basis.

**THIS TOOL IS PROVIDED "AS IS," WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NONINFRINGEMENT.**

The creator(s) and contributor(s) accept no responsibility or liability for any damage, loss, legal consequence, or other harm resulting from the use or misuse of this tool — including damage to systems or data, legal penalties or prosecution, loss of data or revenue, and any direct, indirect, incidental, special, consequential, or punitive damages.

**By using this tool, you acknowledge that you are solely responsible for ensuring that your use is authorized, lawful, and within the defined scope of your engagement. You assume all risk associated with the use of this tool.**

Security testing is a professional discipline with significant ethical and legal obligations. Users operating in a professional capacity are expected to adhere to the standards and codes of conduct established by recognized industry bodies, including PTES, OWASP, NIST, and any certifying body under which they hold credentials.

---

</details>

# Polaris

C2 server manager and real-time connection dashboard for the LandNav rogue device platform. Runs on the C2 server and manages all server-side processes.


> **[View full documentation on the Codex Wiki](https://wiki.pr0t31n.com/tools/polaris)** — detailed features, CLI reference, findings catalogue, and integration guides.

## Features

- **Process supervisor** — starts, stops, restarts, and monitors chisel server, iodined, and msfconsole as independent detached processes
- **Process independence** — managed processes survive Polaris restarts; Polaris re-attaches via PID files on startup
- **Chisel session tracking** — parses `/var/log/chisel.log` for active reverse tunnel sessions, maps server-side ports to registered devices
- **Service health** — monitors chisel server, iodined, and msfconsole process status
- **Device registry integration** — reads `device_registry.json` for hostname-to-device_id mapping
- **Clickable port links** — direct access to forwarded Crucible, Gauntlet, and Nessus instances per device
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
- `config.yaml` and `device_registry.json` (shipped with Polaris)
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

Dashboard available at `http://<c2-server>:5900`

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
| Crucible | 25100     | 25100     | 25101     |
| Crucible | 25500     | 25500     | 25501     |
| Nessus   | 28834     | 28834     | 28835     |
| SOCKS    | 1081      | 1081      | 1082      |

## Data Sources

| Source | What | How |
|--------|------|-----|
| `/var/log/chisel.log` | Active chisel sessions + ports | Tail + regex parse |
| `device_registry.json` | Hostname to device_id mapping | JSON read (Polaris repo) |
| `config.yaml` | C2 host, base ports, service config | YAML read (Polaris repo) |
| Process list | Service health (chisel/iodined/msf) | `pgrep` |
| `/var/run/polaris_sessions.json` | Metasploit sessions (Phase C) | JSON read |
| PID files (`/var/run/polaris_*.pid`) | Managed process state | PID read + kill -0 |

## Integration

- **LandNav** — Pi-side client; Polaris owns the server-side config independently
- **Arsenal** — registered for install/update management
- **tmux** — msfconsole runs in `polaris-msf` session
