"""Synchronize *unique* Cloudflare credentials to Liara without exposing secrets.

Liara CLI v9.5.1 env set concatenates previous and new entries, potentially
retaining duplicate keys; some API failures are swallowed. Reconcile explicitly,
then read back and verify exactly one matching value per key.
"""
import json
import os
import subprocess
import time

APP = "hamdel-reporter"
NAMES = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")


def cli(*args):
    token = os.environ.get("LIARA_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing Liara deployment credential")
    result = subprocess.run(["liara", *args, "--app", APP, "--api-token", token],
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        # Avoid printing stdout/stderr: environment data and tokens might appear.
        raise RuntimeError("Liara environment CLI failed")
    return result.stdout


def list_env():
    raw = cli("env", "list", "--output=json")
    try:
        envs = json.loads(raw)
        if not isinstance(envs, list):
            raise ValueError()
        if not all(isinstance(item, dict) and
                   isinstance(item.get("key"), str) and
                   isinstance(item.get("value"), str) for item in envs):
            raise ValueError()
        return envs
    except (ValueError, TypeError):
        raise RuntimeError("Cannot safely parse Liara environment list") from None


def matches(items, expected):
    return all([row.get("value") for row in items if row["key"] == key] == [value]
               for key, value in expected.items())


def reconcile():
    expected = {name: os.environ.get(name, "").strip() for name in NAMES}
    if not all(expected.values()):
        raise RuntimeError("Missing Cloudflare speech credentials")
    if not (len(expected[NAMES[0]]) == 32
            and all(c in "0123456789abcdefABCDEF" for c in expected[NAMES[0]])):
        raise RuntimeError("Invalid Cloudflare account ID format")

    # Preserve unrelated environment variables, including Bale and Codex auth.
    previous = list_env()
    if matches(previous, expected):
        print("Liara speech environment already matches (two unique values).")
        return

    # Remove *all* stale duplicates first. Each command restarts the application,
    # so run only if it needs reconciliation.
    cli("env", "unset", *NAMES, "--force")
    if any(row["key"] in NAMES for row in list_env()):
        raise RuntimeError("Liara failed to remove obsolete Cloudflare variables")

    cli("env", "set", *(name + "=" + value for name, value in expected.items()), "--force")
    for attempt in range(5):
        if matches(list_env(), expected):
            print("Liara speech environment reconciled and verified (values hidden).")
            return
        time.sleep(2)
    raise RuntimeError("Liara environment did not match GitHub secrets after update")


if __name__ == "__main__":
    try:
        reconcile()
    except Exception as exc:
        print("Cloudflare environment sync failed:", type(exc).__name__, str(exc))
        raise SystemExit(1)
