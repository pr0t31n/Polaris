#!/usr/bin/env python3
"""Polaris — C2 process supervisor.

Manages chisel server, iodined, and msfconsole as independent detached
processes.  Each process survives a Polaris restart — on startup the
supervisor re-attaches to running processes via PID files.
"""

import logging
import os
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path

log = logging.getLogger("polaris.supervisor")

_HERE = Path(__file__).resolve().parent
PID_DIR = Path("/var/run")


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_pid(path):
    try:
        pid = int(Path(path).read_text().strip())
        return pid if _pid_alive(pid) else None
    except Exception:
        return None


def _write_pid(path, pid):
    Path(path).write_text(str(pid))


def _remove_pid(path):
    Path(path).unlink(missing_ok=True)


def _pgrep(pattern):
    try:
        r = subprocess.run(["pgrep", "-f", pattern],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return [int(p) for p in r.stdout.strip().split() if p]
    except Exception:
        pass
    return []


def _port_pid(port):
    try:
        r = subprocess.run(
            ["ss", "-tlnp", "sport", "=", ":%d" % port],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            import re
            m = re.search(r'pid=(\d+)', r.stdout)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def _proc_name(pid):
    try:
        r = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _ufw_allow(port, proto="tcp"):
    if not shutil.which("ufw"):
        return
    try:
        r = subprocess.run(["ufw", "status"], capture_output=True,
                           text=True, timeout=5)
        rule = "%d/%s" % (port, proto)
        if rule not in (r.stdout or ""):
            subprocess.run(["ufw", "allow", rule],
                           capture_output=True, timeout=5)
            log.info("UFW: opened %s", rule)
    except Exception as e:
        log.warning("UFW check failed: %s", e)


def _kill_pid(pid, timeout=5):
    if not _pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.5)


class Supervisor:

    def __init__(self, config):
        self._config = config
        self._started = {}

    def reload_config(self, config):
        self._config = config

    # ── Status ─────────────────────────────────────────────────

    def get_status(self):
        return {
            "chisel_server": self._service_status("chisel_server"),
            "iodined": self._service_status("iodined"),
            "msfconsole": self._service_status("msfconsole"),
        }

    def _service_status(self, name):
        pid_file = PID_DIR / ("polaris_%s.pid" % name)
        pid = _read_pid(pid_file)
        started = self._started.get(name)
        return {
            "running": pid is not None,
            "pid": pid,
            "since": started,
            "enabled": self._is_enabled(name),
        }

    def _is_enabled(self, name):
        if name == "chisel_server":
            return self._config.get("chisel", {}).get("enabled", False)
        if name == "iodined":
            return self._config.get("dns_tunnel", {}).get("enabled", True)
        if name == "msfconsole":
            return True
        return False

    # ── Reattach ───────────────────────────────────────────────

    def reattach_all(self):
        for name in ("chisel_server", "iodined", "msfconsole"):
            pid_file = PID_DIR / ("polaris_%s.pid" % name)
            pid = _read_pid(pid_file)
            if pid:
                self._started[name] = "reattached"
                log.info("Reattached to %s (pid %d)", name, pid)
            else:
                _remove_pid(pid_file)

    # ── Start / Stop / Restart ─────────────────────────────────

    def start_service(self, name):
        if name == "chisel_server":
            return self._start_chisel()
        if name == "iodined":
            return self._start_iodined()
        if name == "msfconsole":
            return self._start_msfconsole()
        return False, "Unknown service: %s" % name

    def stop_service(self, name):
        pid_file = PID_DIR / ("polaris_%s.pid" % name)
        pid = _read_pid(pid_file)
        if not pid:
            if name == "msfconsole":
                self._kill_tmux_session()
            _remove_pid(pid_file)
            self._started.pop(name, None)
            return True, "%s not running" % name

        if name == "msfconsole":
            self._kill_tmux_session()

        _kill_pid(pid)
        _remove_pid(pid_file)
        self._started.pop(name, None)
        log.info("Stopped %s (pid %d)", name, pid)
        return True, "Stopped %s" % name

    def restart_service(self, name):
        self.stop_service(name)
        time.sleep(1)
        return self.start_service(name)

    def start_all(self):
        results = {}
        for name in ("iodined", "chisel_server", "msfconsole"):
            if self._is_enabled(name):
                ok, msg = self.start_service(name)
                results[name] = {"ok": ok, "msg": msg}
            else:
                results[name] = {"ok": False, "msg": "Disabled in config"}
        return results

    def stop_all(self):
        results = {}
        for name in ("msfconsole", "chisel_server", "iodined"):
            ok, msg = self.stop_service(name)
            results[name] = {"ok": ok, "msg": msg}
        return results

    # ── Chisel Server ──────────────────────────────────────────

    def _start_chisel(self):
        cfg = self._config.get("chisel", {})
        if not cfg.get("enabled", False):
            return False, "Chisel disabled in config"

        if not shutil.which("chisel"):
            return False, "chisel binary not found"

        pid_file = PID_DIR / "polaris_chisel.pid"
        existing = _read_pid(pid_file)
        if existing:
            return True, "Chisel already running (pid %d)" % existing

        port = cfg.get("server_port", 443)

        conflict_pid = _port_pid(port)
        if conflict_pid:
            pname = _proc_name(conflict_pid)
            if pname == "chisel":
                log.info("Killing stale chisel (pid %d) on :%d",
                         conflict_pid, port)
                _kill_pid(conflict_pid)
            else:
                log.info("Port %d in use by '%s' (pid %d) — stopping meshcentral",
                         port, pname, conflict_pid)
                subprocess.run(["systemctl", "stop", "meshcentral"],
                               capture_output=True, timeout=10)
                time.sleep(2)
                if _port_pid(port):
                    return False, ("Port %d still in use after stopping "
                                   "meshcentral" % port)

        orphans = _pgrep("chisel server")
        for opid in orphans:
            log.info("Killing orphaned chisel server (pid %d)", opid)
            _kill_pid(opid, timeout=3)

        _ufw_allow(port, "tcp")

        cmd = ["chisel", "server", "--reverse", "--v", "--port", str(port)]

        auth = cfg.get("auth", "")
        if auth:
            cmd += ["--auth", auth]

        if cfg.get("tls", True):
            key_file, cert_file = self._ensure_chisel_tls(cfg)
            if key_file and cert_file:
                cmd += ["--tls-key", key_file, "--tls-cert", cert_file]

        log_file = "/var/log/chisel.log"
        lf = open(log_file, "a")

        proc = subprocess.Popen(
            cmd, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)

        time.sleep(1)

        verify_pid = _port_pid(port)
        if verify_pid:
            _write_pid(pid_file, verify_pid)
            self._started["chisel_server"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            log.info("Chisel server started on :%d (pid %d, TLS=%s)",
                     port, verify_pid, cfg.get("tls", True))
            return True, "Chisel started on :%d (pid %d)" % (port, verify_pid)

        proc.terminate()
        return False, "Chisel failed to start — check /var/log/chisel.log"

    def _ensure_chisel_tls(self, cfg):
        key_file = cfg.get("tls_key", "")
        cert_file = cfg.get("tls_cert", "")

        polaris_dir = os.path.dirname(os.path.abspath(__file__))
        tls_dir = os.path.join(polaris_dir, "keys")

        if not key_file or not cert_file or not os.path.exists(key_file):
            os.makedirs(tls_dir, exist_ok=True)
            key_file = os.path.join(tls_dir, "chisel_tls.key")
            cert_file = os.path.join(tls_dir, "chisel_tls.crt")

        if not os.path.exists(key_file) or not os.path.exists(cert_file):
            c2_host = self._config.get("c2", {}).get("host", "localhost")
            r = subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "ec",
                 "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                 "-keyout", key_file, "-out", cert_file,
                 "-days", "3650", "-subj", "/CN=%s" % c2_host],
                capture_output=True, timeout=30)
            if r.returncode == 0:
                log.info("Auto-generated chisel TLS cert: %s", cert_file)
            else:
                log.warning("TLS cert generation failed: %s", r.stderr)
                return None, None

        return key_file, cert_file

    # ── iodined ────────────────────────────────────────────────

    def _start_iodined(self):
        dns_cfg = self._config.get("dns_tunnel", {})
        if not dns_cfg.get("enabled", True):
            return False, "DNS tunnel disabled in config"

        domain = dns_cfg.get("domain", "")
        password = dns_cfg.get("password", "")
        if not domain or not password:
            return False, "DNS tunnel: domain/password not configured"

        if not shutil.which("iodined"):
            return False, "iodined binary not found"

        pid_file = PID_DIR / "polaris_iodined.pid"
        existing = _read_pid(pid_file)
        if existing:
            return True, "iodined already running (pid %d)" % existing

        running = _pgrep("^iodined")
        if running:
            _write_pid(pid_file, running[0])
            self._started["iodined"] = "reattached"
            return True, "iodined already running (pid %d)" % running[0]

        ns_out = ""
        try:
            r = subprocess.run(
                ["dig", "+short", "NS", domain],
                capture_output=True, text=True, timeout=10)
            ns_out = r.stdout.strip()
        except Exception:
            pass
        if not ns_out:
            c2_host = self._config.get("c2", {}).get("host", "")
            log.warning("No NS delegation for %s — add: %s. IN NS %s.",
                        domain, domain, c2_host)

        _ufw_allow(53, "udp")

        tunnel_net = dns_cfg.get("tunnel_net", "10.53.53.1/24")
        cmd = ["iodined", "-c", "-P", password, tunnel_net, domain]

        log_file = "/var/log/iodined.log"
        lf = open(log_file, "a")

        proc = subprocess.Popen(
            cmd, stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)

        time.sleep(2)

        if proc.poll() is None:
            _write_pid(pid_file, proc.pid)
            self._started["iodined"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            log.info("iodined started: %s (net %s, pid %d)",
                     domain, tunnel_net, proc.pid)
            return True, "iodined started (pid %d)" % proc.pid

        pids = _pgrep("^iodined")
        if pids:
            _write_pid(pid_file, pids[0])
            self._started["iodined"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            log.info("iodined daemonized (pid %d)", pids[0])
            return True, "iodined started (pid %d)" % pids[0]

        stderr = ""
        try:
            with open(log_file) as f:
                stderr = f.read()[-500:]
        except Exception:
            pass

        if "port 53" in stderr.lower() or "address already in use" in stderr.lower():
            return False, "iodined failed — port 53 already in use"
        return False, "iodined failed to start — check /var/log/iodined.log"

    # ── msfconsole ─────────────────────────────────────────────

    TMUX_SESSION = "polaris-msf"

    def _start_msfconsole(self):
        if not shutil.which("msfconsole"):
            return False, "msfconsole not found"

        pid_file = PID_DIR / "polaris_msf.pid"

        existing_pid = self._tmux_pane_pid()
        if existing_pid and _pid_alive(existing_pid):
            _write_pid(pid_file, existing_pid)
            self._started["msfconsole"] = "reattached"
            return True, "msfconsole already running in tmux (pid %d)" % existing_pid

        self._kill_tmux_session()

        self._run_prep_handler()

        _ufw_allow_c2_ports(self._config)

        rc_path = _HERE / "listener.rc"
        if not rc_path.exists():
            return False, "listener.rc not found at %s" % rc_path

        if not shutil.which("tmux"):
            return False, "tmux not found"

        msf_cmd = "msfconsole -r %s" % shlex.quote(str(rc_path))
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", self.TMUX_SESSION, msf_cmd],
            capture_output=True, timeout=10)

        time.sleep(3)

        pid = self._tmux_pane_pid()
        if pid and _pid_alive(pid):
            _write_pid(pid_file, pid)
            self._started["msfconsole"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            log.info("msfconsole started in tmux '%s' (pid %d)",
                     self.TMUX_SESSION, pid)
            return True, ("msfconsole started in tmux '%s' (pid %d)" %
                          (self.TMUX_SESSION, pid))

        return False, "msfconsole failed to start in tmux"

    def _tmux_pane_pid(self):
        try:
            r = subprocess.run(
                ["tmux", "list-panes", "-t", self.TMUX_SESSION,
                 "-F", "#{pane_pid}"],
                capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return int(r.stdout.strip().split("\n")[0])
        except Exception:
            pass
        return None

    def _kill_tmux_session(self):
        try:
            subprocess.run(
                ["tmux", "kill-session", "-t", self.TMUX_SESSION],
                capture_output=True, timeout=5)
        except Exception:
            pass

    def _run_prep_handler(self):
        prep = _HERE / "prep_handler.py"
        if prep.exists():
            try:
                subprocess.run(
                    ["python3", str(prep)],
                    capture_output=True, timeout=30,
                    cwd=str(_HERE))
            except Exception as e:
                log.warning("prep_handler.py failed: %s", e)


def _ufw_allow_c2_ports(config):
    c2_cfg = config.get("c2", {})
    ports = c2_cfg.get("ports", [4443, 8443])
    for p in ports:
        _ufw_allow(p, "tcp")
