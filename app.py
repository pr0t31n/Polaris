#!/usr/bin/env python3
"""Polaris — C2 server manager + connection dashboard.

Manages chisel server, iodined, and msfconsole as independent
processes on the C2 server.  Real-time dashboard on port 5900.
No authentication — runs on the C2 server only.
"""

VERSION = "1.2.2"

import json
import logging
import os
import sys
from pathlib import Path

import yaml
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

from monitor import (DeviceRegistry, ChiselMonitor, ServiceHealthCheck,
                     IodineMonitor, MetasploitMonitor, BASE_PORTS)
from supervisor import Supervisor

_HERE = Path(__file__).resolve().parent
CONFIG_PATH = _HERE / "config.yaml"


def _load_config():
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


cfg = _load_config()
C2_HOST = cfg.get("c2", {}).get("host", "66.194.172.172")
MONITOR_PORT = 5900

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(24).hex()
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
)
log = logging.getLogger("polaris")

registry = DeviceRegistry()
supervisor = Supervisor(cfg)


def _emit(event, data):
    socketio.emit(event, data)


chisel_mon = ChiselMonitor(registry, emit_fn=_emit)
service_mon = ServiceHealthCheck(emit_fn=_emit)
_tunnel_net = cfg.get("dns_tunnel", {}).get("tunnel_net", "10.53.53.1/24")
iodine_mon = IodineMonitor(registry, emit_fn=_emit, tunnel_net=_tunnel_net)
msf_mon = MetasploitMonitor(registry, emit_fn=_emit)


# ── Dashboard ──────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
                           c2_host=C2_HOST,
                           base_ports=json.dumps(BASE_PORTS),
                           version=VERSION)


@app.route("/api/status")
def api_status():
    devices = registry.all_devices()
    chisel_sessions = chisel_mon.get_sessions()
    services = service_mon.get_status()
    iodine_clients = iodine_mon.get_clients()
    msf_sessions = msf_mon.get_sessions()
    sup_status = supervisor.get_status()

    appliances = {}

    def _ensure(hostname, fallback_device_id=None):
        if hostname not in appliances:
            device_id = registry.id_for_hostname(hostname)
            if device_id is None:
                device_id = (fallback_device_id
                             if fallback_device_id is not None
                             else len(appliances))
            appliances[hostname] = {
                "hostname": hostname,
                "device_id": device_id,
                "chisel": False,
                "dns_tunnel": False,
                "meterpreter": False,
                "ports": {
                    "vantage": BASE_PORTS["vantage"] + device_id,
                    "crucible": BASE_PORTS["crucible"] + device_id,
                    "nessus": BASE_PORTS["nessus"] + device_id,
                    "socks": BASE_PORTS["socks"] + device_id,
                },
            }

    for sess in chisel_sessions.values():
        hn = sess.get("hostname")
        if hn:
            _ensure(hn, sess.get("device_id"))
            appliances[hn]["chisel"] = True

    iodine_peer_count = len(iodine_clients)
    if iodine_peer_count > 0 and services.get("iodined"):
        for a in appliances.values():
            if a["chisel"]:
                a["dns_tunnel"] = True

    msf_count = len(msf_sessions)
    if msf_count > 0 and services.get("msfconsole"):
        matched = set()
        for sess in msf_sessions.values():
            hn = sess.get("hostname")
            if hn and hn in appliances:
                appliances[hn]["meterpreter"] = True
                matched.add(hn)
        if not matched:
            for a in appliances.values():
                if a["chisel"]:
                    a["meterpreter"] = True

    return jsonify({
        "services": services,
        "supervisor": sup_status,
        "appliances": appliances,
        "c2_host": C2_HOST,
        "events": chisel_mon.get_events(),
    })


# ── Service Control ────────────────────────────────────────────

@app.route("/api/service/<name>/start", methods=["POST"])
def api_service_start(name):
    ok, msg = supervisor.start_service(name)
    service_mon.force_check()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/service/<name>/stop", methods=["POST"])
def api_service_stop(name):
    ok, msg = supervisor.stop_service(name)
    service_mon.force_check()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/service/<name>/restart", methods=["POST"])
def api_service_restart(name):
    ok, msg = supervisor.restart_service(name)
    service_mon.force_check()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/start-all", methods=["POST"])
def api_start_all():
    results = supervisor.start_all()
    service_mon.force_check()
    return jsonify(results)


@app.route("/api/stop-all", methods=["POST"])
def api_stop_all():
    results = supervisor.stop_all()
    service_mon.force_check()
    return jsonify(results)


# ── SocketIO ───────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    socketio.emit("service_status", service_mon.get_status())


def main():
    log.info("Polaris v%s starting on :%d", VERSION, MONITOR_PORT)

    supervisor.reattach_all()

    chisel_mon.start()
    service_mon.start()
    iodine_mon.start()
    msf_mon.start()

    try:
        socketio.run(app, host="0.0.0.0", port=MONITOR_PORT,
                     debug=False, allow_unsafe_werkzeug=True)
    finally:
        chisel_mon.stop()
        service_mon.stop()
        iodine_mon.stop()
        msf_mon.stop()


if __name__ == "__main__":
    main()
