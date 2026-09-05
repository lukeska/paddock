#!/usr/bin/python
"""Deterministic NDJSON backend used by the TUI PTY acceptance tests."""

from __future__ import annotations

import json
import sys
import time


def dashboard(state: str = "active") -> dict[str, object]:
    return {
        "services": [{
            "key": "caddy", "title": "Caddy", "group": "web",
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
        }],
        "php_versions": ["8.4", "8.5"],
        "node_versions": ["20", "22"],
    }


def snapshot(state: str = "active") -> dict[str, object]:
    return {
        "protocol_version": 1,
        "dashboard": dashboard(state),
        "services": {"instances": []},
        "sites": sites(),
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
    else:
        result = {
            "ok": True, "summary": f"Completed {method}", "detail": None,
            "snapshot": sites(),
        }
    print(json.dumps({"id": request["id"], "ok": True, "result": result}), flush=True)
