#!/usr/bin/env python3
"""Strip auto_commands.txt for SSH-tunneled ports.

Lightweight helper called by Polaris supervisor before starting
the Metasploit handler.  Reads config from Polaris's config.yaml
and strips portfwd entries that overlap with SSH or chisel tunnels.

Also callable directly:
    python3 prep_handler.py                    # Uses Polaris config.yaml
    python3 prep_handler.py -c /path/to.yaml   # Alternate config
"""

import sys
import yaml
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AUTO_CMD_PATH = _HERE / "auto_commands.txt"
_DEFAULT_CONFIG = _HERE / "config.yaml"


def ensure_config_current(cfg, config_path):
    """Sync C2 server's config with config.yaml.example defaults."""
    example_path = _HERE / "config.yaml.example"
    if not example_path.exists():
        return cfg

    try:
        with open(example_path) as f:
            example_cfg = yaml.safe_load(f) or {}
    except Exception:
        return cfg

    changed = False

    for section_key, example_section in example_cfg.items():
        if section_key not in cfg:
            cfg[section_key] = example_section
            print("  config: added missing section: %s" % section_key)
            changed = True
            continue

        if isinstance(example_section, dict) and isinstance(cfg[section_key], dict):
            for key, example_val in example_section.items():
                if key not in cfg[section_key]:
                    cfg[section_key][key] = example_val
                    print("  config: %s.%s added (default: %s)" % (
                        section_key, key, example_val))
                    changed = True

    if changed:
        try:
            with open(config_path, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
            print("  config: updated %s" % config_path)
        except Exception as e:
            print("  config: could not write: %s" % e)

    return cfg


def generate_auto_commands(cfg):
    tunnel_ports = set()

    tunnel_cfg = cfg.get("ssh_tunnels")
    if tunnel_cfg:
        for fwd in tunnel_cfg.get("forwards", []):
            lp = fwd.get("local_port")
            if lp:
                tunnel_ports.add(int(lp))

    chisel_cfg = cfg.get("chisel", {})
    if chisel_cfg.get("enabled", False):
        chisel_fwds = chisel_cfg.get("forwards", [])
        if not chisel_fwds and tunnel_cfg:
            chisel_fwds = tunnel_cfg.get("forwards", [])
        for fwd in chisel_fwds:
            lp = fwd.get("local_port")
            if lp:
                tunnel_ports.add(int(lp))

    if not _AUTO_CMD_PATH.exists():
        return

    original_lines = _AUTO_CMD_PATH.read_text().splitlines()
    kept = []
    stripped = []
    for line in original_lines:
        raw = line.strip()
        if raw.startswith("portfwd") and tunnel_ports:
            parts = raw.split()
            try:
                li = parts.index("-l")
                lport = int(parts[li + 1])
                if lport in tunnel_ports:
                    stripped.append((lport, raw))
                    continue
            except (ValueError, IndexError):
                pass
        kept.append(line)

    if stripped:
        _AUTO_CMD_PATH.write_text("\n".join(kept) + "\n")
        for sp, sl in stripped:
            print("  stripped portfwd -l %d (SSH tunnel takes over)" % sp)
    else:
        print("  no portfwd/SSH-tunnel overlap — auto_commands.txt unchanged")


if __name__ == "__main__":
    config_path = _DEFAULT_CONFIG
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg in ("-c", "--config") and i + 1 < len(args):
            config_path = Path(args[i + 1])

    if not config_path.exists():
        sys.exit(0)

    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}

    cfg = ensure_config_current(cfg, config_path)
    generate_auto_commands(cfg)
