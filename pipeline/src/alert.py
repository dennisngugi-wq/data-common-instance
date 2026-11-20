#!/usr/bin/env python3
import json
import os
import sys
import urllib.request

import yaml

import settings


def message(text):
    try:
        inst = yaml.safe_load((settings.CONFIG_DIR / "instance.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        inst = {}
    site = os.environ.get("SITE_URL") or inst.get("site_url") or ""
    return f"[{inst.get('name') or 'Data Commons'}] {text}" + (f" ({site})" if site else "")


def send(text, url=None):
    url = url if url is not None else os.environ.get("ALERT_WEBHOOK_URL", "")
    if not url.startswith(("http://", "https://")):
        return False
    req = urllib.request.Request(url, data=json.dumps({"text": message(text)}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": settings.USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
    return True


def main(argv=None):
    text = " ".join(argv if argv is not None else sys.argv[1:]).strip()
    if not text:
        print("usage: alert.py <message>")
        return 2
    try:
        print("alert sent" if send(text) else "ALERT_WEBHOOK_URL not set in env.list: alert kept in logs/ only")
    except Exception as exc:
        print(f"alert could not be sent: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
