#!/usr/bin/env python3
"""Polaris installer — multi-platform."""
import subprocess
import sys
import os
import shutil
import urllib.request
from pathlib import Path

TOOL_NAME = "Polaris"
MIN_PYTHON = (3, 10)
_HERE = Path(__file__).resolve().parent

SOCKETIO_VERSION = "4.7.5"
SOCKETIO_URL = ("https://cdnjs.cloudflare.com/ajax/libs/socket.io/%s/"
                "socket.io.min.js" % SOCKETIO_VERSION)


def _run(cmd, check=True):
    print("  -> %s" % (" ".join(cmd) if isinstance(cmd, list) else cmd))
    return subprocess.run(cmd, shell=isinstance(cmd, str), check=check)


def _has(binary):
    return shutil.which(binary) is not None


def install_pip_deps():
    req = _HERE / "requirements.txt"
    if req.exists():
        _run([sys.executable, "-m", "pip", "install",
              "--break-system-packages", "-r", str(req)])


def download_socketio_js():
    dest = _HERE / "static" / "socket.io.min.js"
    if dest.exists():
        print("  socket.io.min.js already present")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print("  Downloading socket.io.min.js v%s..." % SOCKETIO_VERSION)
    try:
        urllib.request.urlretrieve(SOCKETIO_URL, str(dest))
        print("  -> %s" % dest)
    except Exception as e:
        print("  WARNING: download failed: %s" % e)
        print("  Manually place socket.io.min.js in static/")


def create_dirs():
    for d in ["templates", "static"]:
        (_HERE / d).mkdir(parents=True, exist_ok=True)


def main():
    if sys.version_info < MIN_PYTHON:
        print("ERROR: Python %s+ required" % ".".join(map(str, MIN_PYTHON)))
        sys.exit(1)

    print("%s - Installation" % TOOL_NAME)
    print()

    create_dirs()
    install_pip_deps()
    download_socketio_js()

    print()
    print("%s installed." % TOOL_NAME)
    print("Start with: python3 app.py")


if __name__ == "__main__":
    main()
