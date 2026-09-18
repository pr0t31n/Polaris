#!/usr/bin/env python3
"""Polaris — background data collectors."""

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("polaris.monitor")

BASE_PORTS = {
    "vantage": 25100,
    "crucible": 25500,
    "nessus": 28834,
    "socks": 1081,
}

REGISTRY_PATH = Path(__file__).resolve().parent / "device_registry.json"
CHISEL_LOG = Path("/var/log/chisel.log")
MSF_SESSIONS_FILE = Path("/var/run/polaris_sessions.json")


class DeviceRegistry:

    def __init__(self):
        self._registry = {}
        self._reverse = {}
        self._last_load = 0
        self.reload()

    def reload(self):
        try:
            data = json.loads(REGISTRY_PATH.read_text())
            self._registry = {k: int(v) for k, v in data.items()}
            self._reverse = {v: k for k, v in self._registry.items()}
            self._last_load = time.time()
        except Exception as e:
            log.warning("Failed to load device registry: %s", e)

    def _maybe_reload(self):
        if time.time() - self._last_load > 60:
            self.reload()

    def hostname_for_id(self, device_id):
        self._maybe_reload()
        return self._reverse.get(device_id, "device_%d" % device_id)

    def id_for_hostname(self, hostname):
        self._maybe_reload()
        return self._registry.get(hostname)

    def all_devices(self):
        self._maybe_reload()
        return dict(self._registry)

    def device_id_from_port(self, server_port):
        for name, base in BASE_PORTS.items():
            offset = server_port - base
            if 0 <= offset < 100:
                return offset, name
        return None, None


class ChiselMonitor:

    _PROXY_RE = re.compile(
        r'session#(\d+).*proxy#R:(?:[\d.]+:)?(\d+)=>\S+:\s*Listening')
    _CLOSE_RE = re.compile(r'session#(\d+):\s*[Cc]los')
    _SERVER_START_RE = re.compile(r'server:\s*(Listening|Fingerprint)\b')

    def __init__(self, registry, emit_fn=None):
        self._registry = registry
        self._emit = emit_fn
        self._sessions = {}
        self._stop = threading.Event()
        self._file_pos = 0
        self._thread = None
        self._events = []
        self._events_lock = threading.Lock()
        self._replaying = False

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="chisel-monitor")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def get_sessions(self):
        return dict(self._sessions)

    def get_events(self, limit=50):
        with self._events_lock:
            return list(self._events[-limit:])

    def _add_event(self, msg):
        ts = time.strftime("%H:%M:%S")
        entry = {"ts": ts, "msg": msg}
        with self._events_lock:
            self._events.append(entry)
            if len(self._events) > 200:
                self._events = self._events[-200:]
        if self._emit:
            self._emit("event", entry)

    def _run(self):
        self._replay_log()

        while not self._stop.is_set():
            self._poll()
            self._stop.wait(2)

    def _replay_log(self):
        if not CHISEL_LOG.exists():
            return
        self._replaying = True
        try:
            with open(CHISEL_LOG, 'r') as f:
                for line in f:
                    stripped = line.strip()
                    if self._SERVER_START_RE.search(stripped):
                        self._sessions.clear()
                        continue
                    self._parse_line(stripped)
                self._file_pos = f.tell()

            if self._sessions and not self._chisel_server_alive():
                log.info("Chisel server not running — clearing %d stale "
                         "session(s)", len(self._sessions))
                self._sessions.clear()

            if self._sessions:
                log.info("Replayed chisel log: %d active session(s)",
                         len(self._sessions))
                for sess in self._sessions.values():
                    self._add_event("%s chisel connected (session #%d)"
                                   % (sess.get("hostname", "unknown"),
                                      sess["session_id"]))
        except Exception as e:
            log.warning("ChiselMonitor replay error: %s", e)
            self._file_pos = CHISEL_LOG.stat().st_size
        finally:
            self._replaying = False

    @staticmethod
    def _chisel_server_alive():
        try:
            r = subprocess.run(["pgrep", "-f", "chisel server"],
                               capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _poll(self):
        if not CHISEL_LOG.exists():
            return

        try:
            size = CHISEL_LOG.stat().st_size
            if size < self._file_pos:
                self._file_pos = 0

            if size == self._file_pos:
                return

            with open(CHISEL_LOG, 'r') as f:
                f.seek(self._file_pos)
                new_lines = f.readlines()
                self._file_pos = f.tell()

            for line in new_lines:
                self._parse_line(line.strip())
        except Exception as e:
            log.warning("ChiselMonitor poll error: %s", e)

    def _parse_line(self, line):
        m = self._PROXY_RE.search(line)
        if m:
            sess_id = int(m.group(1))
            server_port = int(m.group(2))

            device_id, service = self._registry.device_id_from_port(server_port)
            hostname = (self._registry.hostname_for_id(device_id)
                        if device_id is not None else "unknown")

            if sess_id not in self._sessions:
                self._sessions[sess_id] = {
                    "session_id": sess_id,
                    "device_id": device_id,
                    "hostname": hostname,
                    "ports": {},
                    "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                if not self._replaying:
                    self._add_event("%s chisel connected (session #%d)" % (hostname, sess_id))

            self._sessions[sess_id]["ports"][service] = {
                "server_port": server_port,
            }

            if not self._replaying and self._emit:
                self._emit("chisel_update", self._sessions[sess_id])
            return

        m = self._CLOSE_RE.search(line)
        if m:
            sess_id = int(m.group(1))
            if sess_id in self._sessions:
                closed = self._sessions.pop(sess_id)
                hostname = closed.get("hostname", "unknown")
                if not self._replaying:
                    self._add_event("%s chisel disconnected (session #%d)" % (hostname, sess_id))
                    if self._emit:
                        self._emit("chisel_closed", {
                            "session_id": sess_id,
                            "hostname": hostname,
                        })


class ServiceHealthCheck:

    def __init__(self, emit_fn=None):
        self._emit = emit_fn
        self._stop = threading.Event()
        self._status = {}
        self._thread = None
        self._force = threading.Event()

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="service-health")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def get_status(self):
        return dict(self._status)

    def force_check(self):
        self._force.set()

    def _run(self):
        while not self._stop.is_set():
            self._check_all()
            self._force.clear()
            self._stop.wait(10)
            if self._force.is_set():
                self._force.clear()
                self._check_all()

    def _check_all(self):
        new_status = {
            "chisel_server": self._proc_alive("chisel server"),
            "iodined": self._proc_alive("iodined"),
            "msfconsole": (self._proc_alive("msfconsole")
                           or self._proc_alive("ruby.*msf")),
        }

        if new_status != self._status:
            self._status = new_status
            if self._emit:
                self._emit("service_status", self._status)

    def _proc_alive(self, pattern):
        try:
            r = subprocess.run(["pgrep", "-f", pattern],
                               capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False


class IodineMonitor:

    _DNS0_PATH = Path("/sys/class/net/dns0")

    def __init__(self, registry, emit_fn=None, tunnel_net="10.53.53.1/24"):
        self._registry = registry
        self._emit = emit_fn
        self._clients = {}
        self._stop = threading.Event()
        self._thread = None
        self._tunnel_base = self._parse_tunnel_base(tunnel_net)

    @staticmethod
    def _parse_tunnel_base(tunnel_net):
        ip_part = tunnel_net.split("/")[0]
        octets = ip_part.split(".")
        if len(octets) == 4:
            return ".".join(octets[:3])
        return "10.53.53"

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="iodine-monitor")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def get_clients(self):
        return dict(self._clients)

    def _run(self):
        while not self._stop.is_set():
            self._poll()
            self._stop.wait(15)

    def _poll(self):
        if not self._DNS0_PATH.exists():
            if self._clients:
                self._clients.clear()
                if self._emit:
                    self._emit("iodine_update", {})
            return

        new_clients = {}
        for i in range(2, 11):
            ip = "%s.%d" % (self._tunnel_base, i)
            try:
                r = subprocess.run(
                    ["ping", "-c", "1", "-W", "1", ip],
                    capture_output=True, timeout=3)
                if r.returncode == 0:
                    new_clients[ip] = {
                        "tunnel_ip": ip,
                        "state": "REACHABLE",
                    }
            except Exception:
                pass

        if new_clients != self._clients:
            self._clients = new_clients
            if self._emit:
                self._emit("iodine_update",
                           list(self._clients.values()))

    def peer_count(self):
        return len(self._clients)


class MetasploitMonitor:

    TMUX_SESSION = "polaris-msf"
    _SESSION_OPEN_RE = re.compile(
        r'Meterpreter session (\d+) opened \(([^)]+)\)')
    _SESSION_CLOSE_RE = re.compile(
        r'session (\d+) closed')

    def __init__(self, registry, emit_fn=None):
        self._registry = registry
        self._emit = emit_fn
        self._sessions = {}
        self._stop = threading.Event()
        self._thread = None
        self._seen_lines = set()

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="msf-monitor")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def get_sessions(self):
        return dict(self._sessions)

    def _run(self):
        while not self._stop.is_set():
            self._poll_tmux()
            self._poll_file()
            self._stop.wait(10)

    def _poll_tmux(self):
        try:
            r = subprocess.run(
                ["tmux", "capture-pane", "-t", self.TMUX_SESSION,
                 "-p", "-S", "-200"],
                capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return
            changed = False
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line or line in self._seen_lines:
                    continue
                self._seen_lines.add(line)
                if len(self._seen_lines) > 2000:
                    self._seen_lines = set(list(self._seen_lines)[-1000:])

                m = self._SESSION_OPEN_RE.search(line)
                if m:
                    sid = int(m.group(1))
                    conn = m.group(2)
                    remote_ip = self._extract_remote_ip(conn)
                    hostname = self._hostname_from_ip(remote_ip)
                    self._sessions[sid] = {
                        "session_id": sid,
                        "connection": conn,
                        "remote_ip": remote_ip,
                        "hostname": hostname,
                    }
                    changed = True
                    continue

                m = self._SESSION_CLOSE_RE.search(line)
                if m:
                    sid = int(m.group(1))
                    if sid in self._sessions:
                        del self._sessions[sid]
                        changed = True

            if changed and self._emit:
                self._emit("msf_sessions",
                           list(self._sessions.values()))
        except Exception as e:
            log.warning("MetasploitMonitor tmux poll error: %s", e)

    def _poll_file(self):
        if not MSF_SESSIONS_FILE.exists():
            return
        try:
            data = json.loads(MSF_SESSIONS_FILE.read_text())
            if isinstance(data, list):
                new = {}
                for i, s in enumerate(data):
                    sid = s.get("session_id", i)
                    new[sid] = s
                if new != self._sessions:
                    self._sessions = new
                    if self._emit:
                        self._emit("msf_sessions",
                                   list(self._sessions.values()))
        except Exception:
            pass

    @staticmethod
    def _extract_remote_ip(conn):
        parts = conn.split(" -> ")
        if len(parts) == 2:
            remote = parts[1]
        else:
            remote = conn
        ip = remote.split(":")[0] if ":" in remote else remote
        return ip.strip()

    def _hostname_from_ip(self, ip):
        return None
