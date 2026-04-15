import json
import unittest

from fastapi.testclient import TestClient

from backend.main import app


class BackendApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def setUp(self) -> None:
        app.state.console.reset()

    def test_health_endpoint(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("state", payload)

    def test_suggestions_are_mode_specific(self) -> None:
        response = self.client.get("/api/suggestions", params={"mode": "cyber mode"})
        self.assertEqual(response.status_code, 200)
        chips = response.json()["chips"]
        self.assertIn("search exploitdb", chips)

    def test_chat_updates_session_state(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={"message": "analyze code", "mode": "dev mode"},
        )
        self.assertEqual(response.status_code, 200)
        lines = response.json()["lines"]
        self.assertTrue(any("workspace scan complete" in entry["text"].lower() for entry in lines))
        snapshot = app.state.console.snapshot()
        self.assertEqual(snapshot["cmdCount"], 1)
        self.assertEqual(snapshot["mode"], "dev mode")

    def test_clear_logs_quick_op_sets_reset_flag(self) -> None:
        response = self.client.post("/api/quickops", json={"op": "clear logs"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["resetLogs"])
        self.assertEqual(payload["lines"][0]["type"], "info")

    def test_state_stream_emits_json_payload(self) -> None:
        with self.client.stream("GET", "/api/state") as response:
            self.assertEqual(response.status_code, 200)
            first_line = next(item for item in response.iter_lines() if item)

        if isinstance(first_line, bytes):
            first_line = first_line.decode("utf-8")

        self.assertTrue(first_line.startswith("data: "))
        payload = json.loads(first_line.removeprefix("data: "))
        self.assertIn("confidence", payload)
        self.assertIn("processes", payload)
