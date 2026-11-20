import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

os.environ["DC_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import alert
import registry


class Hook(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        Hook.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class AlertTest(unittest.TestCase):
    def test_no_webhook_sends_nothing(self):
        with mock.patch.dict(os.environ, {"ALERT_WEBHOOK_URL": ""}):
            self.assertFalse(alert.send("refresh failed"))
            self.assertEqual(alert.main(["refresh failed"]), 0)

    def test_webhook_receives_the_message_with_instance_and_site(self):
        server = HTTPServer(("127.0.0.1", 0), Hook)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            env = {"ALERT_WEBHOOK_URL": f"http://127.0.0.1:{server.server_port}/hook",
                   "SITE_URL": "https://data.example.org"}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(alert.main(["REFRESH FAILED: the database could not be built"]), 0)
        finally:
            server.shutdown()
        text = Hook.received[-1]["text"]
        self.assertIn("REFRESH FAILED: the database could not be built", text)
        self.assertIn("https://data.example.org", text)
        self.assertTrue(text.startswith("["))

    def test_unreachable_webhook_is_reported_not_raised(self):
        with mock.patch.dict(os.environ, {"ALERT_WEBHOOK_URL": "http://127.0.0.1:9/none"}):
            self.assertEqual(alert.main(["x"]), 1)


class SiteUrlTest(unittest.TestCase):
    def test_site_url_env_overrides_instance_yaml(self):
        with mock.patch.dict(os.environ, {"SITE_URL": "https://data.example.org/"}):
            self.assertEqual(registry.load().instance["site_url"], "https://data.example.org")

    def test_instance_yaml_is_used_without_env(self):
        with mock.patch.dict(os.environ, {"SITE_URL": ""}):
            self.assertEqual(registry.load().instance["site_url"], "http://localhost:8090")


if __name__ == "__main__":
    unittest.main()
