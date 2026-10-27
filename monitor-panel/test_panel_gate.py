"""Panel-side gate control: route behaviour and the rendered settings section.

Kept separate from test_admin_v3 because that module needs a full temporary tree, which
this sandbox cannot provide reliably; here the gate state is a single flat file.
"""

import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import release_gate

try:
    import stratum_admin_v3 as admin
except ModuleNotFoundError as exc:  # Flask is only installed on the deployment host
    if exc.name == "flask":
        raise unittest.SkipTest("Flask is installed by the Linux deployment script")
    raise

HERE = Path(__file__).resolve().parent


class PanelGateTest(unittest.TestCase):
    def setUp(self):
        self.state = HERE / f".gate-panel-{uuid.uuid4().hex[:8]}.json"
        self._original = getattr(admin, "GATE_STATE_FILE", None)
        admin.GATE_STATE_FILE = self.state
        admin.app.config.update(TESTING=True, SECRET_KEY="test")
        # The route intentionally requires a Tailscale admin identity; stub it so the test
        # exercises the gate logic rather than the network position of the caller.
        patcher = patch.object(admin, "current_tailscale_admin", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        audit = patch.object(admin, "append_audit", return_value=None)
        audit.start()
        self.addCleanup(audit.stop)
        self.client = admin.app.test_client()
        with self.client.session_transaction() as session:
            session["authenticated"] = True
            session["csrf"] = "token"

    def tearDown(self):
        if self._original is not None:
            admin.GATE_STATE_FILE = self._original
        for suffix in ("", ".lock", ".tmp"):
            try:
                Path(str(self.state) + suffix).unlink(missing_ok=True)
            except OSError:
                pass

    def test_gate_is_closed_by_default(self):
        context = release_gate.status(release_gate.load_state(self.state))
        self.assertFalse(context["open"])
        self.assertEqual(context["mode"], release_gate.MODE_CLOSED)

    def test_open_for_minutes_through_the_route(self):
        response = self.client.post("/update/gate", data={
            "csrf": "token", "action": "open_minutes", "minutes": "30"})
        self.assertEqual(response.status_code, 302)
        state = release_gate.load_state(self.state)
        self.assertEqual(state["mode"], release_gate.MODE_DURATION)
        self.assertTrue(release_gate.status(state)["open"])

    def test_open_for_downloads_through_the_route(self):
        self.client.post("/update/gate", data={
            "csrf": "token", "action": "open_downloads", "downloads": "2"})
        state = release_gate.load_state(self.state)
        self.assertEqual(state["mode"], release_gate.MODE_COUNT)
        self.assertEqual(state["remaining"], 2)

    def test_close_through_the_route_keeps_the_served_counter(self):
        release_gate.save_state(self.state, release_gate.open_for_downloads(5))
        gate = release_gate.load_state(self.state)
        gate["served_total"] = 2
        release_gate.save_state(self.state, gate)

        self.client.post("/update/gate", data={"csrf": "token", "action": "close"})
        state = release_gate.load_state(self.state)
        self.assertEqual(state["mode"], release_gate.MODE_CLOSED)
        self.assertEqual(state["served_total"], 2, "closing must not erase how many were served")

    def test_unknown_action_is_refused_and_changes_nothing(self):
        release_gate.save_state(self.state, release_gate.close())
        response = self.client.post("/update/gate", data={"csrf": "token", "action": "forever"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(release_gate.load_state(self.state)["mode"], release_gate.MODE_CLOSED)

    def test_non_numeric_values_are_rejected(self):
        release_gate.save_state(self.state, release_gate.close())
        self.client.post("/update/gate", data={
            "csrf": "token", "action": "open_minutes", "minutes": "abc"})
        self.assertEqual(release_gate.load_state(self.state)["mode"], release_gate.MODE_CLOSED)

    def test_route_requires_csrf(self):
        response = self.client.post("/update/gate", data={"action": "open_minutes", "minutes": "10"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(release_gate.load_state(self.state)["mode"], release_gate.MODE_CLOSED)

    def test_template_renders_the_gate_controls(self):
        """Rendering the whole settings page needs a live VPS tree, so assert on the template.

        The page context supplies gate_state; a missing attribute there would raise at
        render time, and that is covered by the context check below.
        """
        source = (HERE / "templates" / "v3_dashboard.html").read_text(encoding="utf-8")
        self.assertIn("客户端下载闸门", source)
        self.assertIn("status1.mulinsen.win/exe", source)
        self.assertIn("开放 10 分钟", source)
        self.assertIn("开放 3 次", source)
        self.assertIn("立即关闭", source)

        import release_gate as gate

        context = gate.status(gate.load_state(HERE / "missing-gate.json"))
        for name in ("open", "mode", "reason", "remaining", "seconds_left", "served_total"):
            self.assertIn(name, context, f"the template reads gate_state.{name}")


if __name__ == "__main__":
    unittest.main()
