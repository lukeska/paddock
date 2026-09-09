#!/usr/bin/python
"""Deterministic NDJSON backend used by the TUI PTY acceptance tests."""

from __future__ import annotations

import json
import sys
import time


def dashboard(state: str = "active") -> dict[str, object]:
    return {
        "services": [{
            "key": "web", "title": "Web", "group": "web",
            "state": state, "detail": "Serving sites", "configured": True,
            "connection": [], "port": None, "autostart": False,
        }]
    }


def sites() -> dict[str, object]:
    return {
        "sites": [{
            "name": "linguine", "host": "linguine.test",
            "url": "https://linguine.test", "php": "8.5",
            "secured": True, "root": "/srv/linguine", "node": "22",
            "reverb_available": True, "reverb_configured": True,
            "reverb_state": "inactive", "reverb_autostart": False,
            "reverb_port": 8080, "queue_available": True,
            "queue_configured": True, "queue_state": "active",
            "queue_autostart": True,
            "type": "laravel", "document_root": "public",
            "custom_config": "/home/demo/.config/paddock/nginx/linguine.custom.conf",
            "custom_config_present": False,
            "nginx_config_error": False,
            "project_config": ".paddock/nginx.conf",
            "project_config_status": "pending",
        }],
        "php_versions": ["8.4", "8.5"],
        "node_versions": ["20", "22"],
    }


def snapshot(state: str = "active") -> dict[str, object]:
    return {
        "protocol_version": 6,
        "dashboard": dashboard(state),
        "services": {"instances": []},
        "sites": sites(),
        "php": {
            "architecture": "x86_64",
            "versions": [
                {"minor": "8.5", "release": "8.5.8", "architecture": "x86_64", "installed": True, "available": True, "path": "/php/8.5"},
                {"minor": "8.4", "release": "8.4.23", "architecture": "x86_64", "installed": False, "available": True, "path": None},
            ],
        },
        "node": {
            "architecture": "x86_64",
            "versions": [
                {"major": "24", "release": "24.8.0", "architecture": "x86_64", "installed": False, "available": True, "path": None},
                {"major": "22", "release": "22.19.0", "architecture": "x86_64", "installed": True, "available": True, "path": "/node/22"},
            ],
        },
        "parking": {"paths": ["/home/demo/Code"], "conflicts": []},
        "theme": None,
    }


for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    params = request["params"]
    if method == "snapshot.get":
        result = snapshot()
    elif method == "dashboard.set_active":
        # Leave enough time for at least one animation frame to reach the PTY.
        time.sleep(0.25)
        active = params["active"]
        result = {
            "ok": True,
            "summary": f"{'Started' if active else 'Stopped'} all configured services",
            "detail": None,
            "snapshot": dashboard("active" if active else "inactive"),
        }
    elif method == "worker.logs":
        result = {"lines": ["fixture log line"]}
    elif method == "php.install":
        result = {"ok": True, "summary": f"Installed PHP {params['minor']}", "detail": None, "snapshot": snapshot()["php"]}
    elif method == "node.install":
        result = {"ok": True, "summary": f"Installed Node.js {params['major']}", "detail": None, "snapshot": snapshot()["node"]}
    elif method == "parking.add":
        result = {"ok": True, "summary": f"Parked {params['path']}", "detail": None, "snapshot": snapshot()["parking"]}
    elif method == "parking.remove":
        result = {"ok": True, "summary": f"Forgot {params['path']}", "detail": None, "snapshot": {"paths": [], "conflicts": []}}
    else:
        result = {
            "ok": True, "summary": f"Completed {method}", "detail": None,
            "snapshot": sites(),
        }
    print(json.dumps({"id": request["id"], "ok": True, "result": result}), flush=True)
