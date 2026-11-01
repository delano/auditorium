import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import quote

import auditorium
import core
from test_core import Fixture


class HttpTests(Fixture):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), auditorium.Handler)
        self.server.database = str(self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def send(self, method, path, payload=None, origin=True, database=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        self.addCleanup(conn.close)
        headers = {
            "X-Auditorium-Database": quote(str(database or self.db)),
            "Content-Type": "application/json",
        }
        if origin:
            headers["Origin"] = "http://127.0.0.1:" + str(self.server.server_port)
        conn.request(
            method, path, body=json.dumps(payload) if payload else None, headers=headers
        )
        response = conn.getresponse()
        body = response.read()
        return response.status, json.loads(body)

    def test_http_rejects_origin_and_database_mismatch(self):
        body = {"action": "handoff", "kind": "discovery", "database_id": self.uid}
        status, _ = self.send("POST", "/api/action", body, origin=False)
        self.assertEqual(status, 400)
        other = self.root / "other.sqlite"
        core.create(other, self.root, "example/docs")
        status, _ = self.send("POST", "/api/action", body, database=other)
        self.assertEqual(status, 400)
        self.assertEqual(core.state(other)["handoffs"], [])

    def test_http_state_and_scoped_prompt(self):
        status, state = self.send("GET", "/api/state?database=" + quote(str(self.db)))
        self.assertEqual(status, 200)
        self.assertEqual(state["workspace"]["id"], self.uid)
        status, result = self.send(
            "POST",
            "/api/action",
            {
                "database_id": self.uid,
                "action": "handoff",
                "kind": "validation",
                "batch_id": 1,
                "finding_ids": ["A"],
            },
        )
        self.assertEqual(status, 200)
        h = next(
            h for h in result["state"]["handoffs"] if h["id"] == result["handoff_id"]
        )
        self.assertIn('Finding IDs: ["A"]', h["prompt"])

    def test_own_check_and_acceptance_roll_back_together(self):
        c = self.report()["checks"][0]
        with (
            patch("core.tree_at", return_value={"A.md": "blob-a"}),
            patch("core.decide", side_effect=ValueError("Concurrent change")),
        ):
            status, _ = self.send(
                "POST",
                "/api/action",
                {
                    "database_id": self.uid,
                    "action": "own_check",
                    "batch_id": 1,
                    "check": c,
                    "accept": True,
                },
            )
        self.assertEqual(status, 400)
        self.assertEqual(core.state(self.db)["checks"], [])

    def test_own_check_and_acceptance_one_action(self):
        c = self.report()["checks"][0]
        with patch("core.tree_at", return_value={"A.md": "blob-a"}):
            status, result = self.send(
                "POST",
                "/api/action",
                {
                    "database_id": self.uid,
                    "action": "own_check",
                    "batch_id": 1,
                    "check": c,
                    "accept": True,
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(
            next(i for i in result["state"]["queue"] if i["id"] == "A")["resolved"]
        )


if __name__ == "__main__":
    unittest.main()
