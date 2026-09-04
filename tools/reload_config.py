#!/usr/bin/env python3
"""Home Assistant Configuration Reload Tool.

Calls the Home Assistant API to reload configuration after config files have
been pushed to the instance.

Uses `homeassistant.reload_all` rather than `homeassistant.reload_core_config`.
The latter reloads only core settings and leaves YAML-configured integrations
untouched, so a newly added `rest:`, `command_line:` or `template:` block ends
up on disk without ever being loaded. That happened on 2026-09-04: a package of
REST sensors rsynced cleanly, the reload reported success, and none of the
entities existed until `reload_all` was called by hand.

`reload_all` covers core config, automations, scripts, scenes, template
entities, REST, command_line and the input helpers. It was added in HA 2024.4;
older instances fall back to the core-only reload with a warning.
"""

import os
import sys
from pathlib import Path

import requests

# Preferred first. Each entry is (service, description, is_fallback).
RELOAD_SERVICES = [
    ("homeassistant/reload_all", "all reloadable configuration", False),
    ("homeassistant/reload_core_config", "core configuration only", True),
]


def load_env_file():
    """Load environment variables from .env file."""
    env_file = Path(".env")
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")


def call_reload(ha_url, headers, service, description):
    """Call one reload service. Returns (ok, service_missing)."""
    url = f"{ha_url}/api/services/{service}"
    response = requests.post(url, headers=headers, json={}, timeout=60)

    if response.status_code == 200:
        print(f"✅ Reloaded {description}")
        return True, False

    # HA answers 400 for an unknown service; treat that as "try the fallback"
    # rather than a hard failure, so older instances still deploy.
    if response.status_code in (400, 404):
        return False, True

    print(f"❌ Failed to reload: {response.status_code}")
    if response.text:
        print(f"   Response: {response.text}")
    return False, False


def reload_config():
    """Reload Home Assistant configuration via API."""
    load_env_file()

    ha_url = os.getenv("HA_URL", "http://homeassistant.local:8123").rstrip("/")
    token = os.getenv("HA_TOKEN", "")

    if not token:
        print("❌ Error: HA_TOKEN not found in environment or .env file")
        print("   Create a .env file with: HA_TOKEN=your_long_lived_access_token")
        print("   Get your token from Home Assistant Profile page")
        return False

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        for service, description, is_fallback in RELOAD_SERVICES:
            if is_fallback:
                print(f"⚠️  reload_all unavailable, falling back to {description}")
                print("   YAML integrations added since the last restart may not")
                print("   appear until Home Assistant is restarted.")

            print(f"🔄 Reloading {description}...")
            ok, service_missing = call_reload(ha_url, headers, service, description)

            if ok:
                return True
            if not service_missing:
                return False

        print("❌ No usable reload service on this Home Assistant instance")
        return False

    except requests.exceptions.Timeout:
        print("❌ Timeout: Home Assistant took too long to respond")
        print("   This may indicate a configuration error preventing reload")
        return False

    except requests.exceptions.ConnectionError:
        print(f"❌ Connection error: Cannot reach Home Assistant at {ha_url}")
        print("   Check that Home Assistant is running and accessible")
        return False

    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False


if __name__ == "__main__":
    SUCCESS = reload_config()
    sys.exit(0 if SUCCESS else 1)
