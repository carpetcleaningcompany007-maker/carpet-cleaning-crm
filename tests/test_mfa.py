import importlib
import json
import os
import re
import tempfile
import time
import unittest
from unittest import mock


class MfaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        os.environ["CRM_SECRET_KEY"] = "mfa-test-secret-that-never-leaves-tests"
        import app
        self.mod = importlib.reload(app)
        self.app = self.mod.app
        self.app.config.update(TESTING=False, SESSION_COOKIE_SECURE=False)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.mod.init_db()
        self.client = self.app.test_client()

    def tearDown(self):
        self.ctx.pop()
        os.environ.pop("MFA_BOOTSTRAP_TOKEN", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def csrf(self):
        with self.client.session_transaction() as state:
            token = state.get("csrf_token") or "test-csrf-token"
            state["csrf_token"] = token
            return token

    def authenticated(self):
        with self.client.session_transaction() as state:
            state["logged_in"] = True
            state["csrf_token"] = "test-csrf-token"

    def enrol(self):
        self.authenticated()
        self.client.post("/security/two-step", data={"action": "start", "_csrf_token": self.csrf()})
        row = self.mod.settings()
        encrypted = row["mfa_secret_encrypted"]
        secret = self.mod.decrypt_mfa_secret(encrypted)
        self.assertTrue(secret)
        self.assertNotIn(secret, encrypted)
        code = self.mod.totp_at(secret, int(time.time()) // 30)
        response = self.client.post("/security/two-step", data={
            "action": "confirm", "code": code, "_csrf_token": self.csrf(),
        })
        body = response.get_data(as_text=True)
        recovery_codes = re.findall(r"[A-F0-9]{8}-[A-F0-9]{8}", body)
        self.assertEqual(len(recovery_codes), 10)
        return secret, recovery_codes

    def password_login(self):
        self.client.get("/login")
        with mock.patch.object(self.mod.time, "sleep"):
            return self.client.post("/login", data={
                "username": "admin", "password": "admin123", "_csrf_token": self.csrf(),
            })

    def test_enrollment_encrypts_secret_and_hashes_recovery_codes(self):
        _, recovery_codes = self.enrol()
        row = self.mod.settings()
        self.assertEqual(row["mfa_enabled"], 1)
        stored = row["mfa_recovery_hashes"]
        self.assertEqual(len(json.loads(stored)), 10)
        for code in recovery_codes:
            self.assertNotIn(code, stored)

    def test_password_login_requires_mfa_and_success_rotates_pending_session(self):
        secret, _ = self.enrol()
        self.mod.run("UPDATE settings SET mfa_last_counter=-1 WHERE id=1")
        with self.client.session_transaction() as state:
            state.clear()
            state["attacker_marker"] = "remove"
        response = self.password_login()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/login/two-step"))
        with self.client.session_transaction() as state:
            self.assertNotIn("logged_in", state)
            self.assertNotIn("attacker_marker", state)
            self.assertIn("mfa_pending", state)
        code = self.mod.totp_at(secret, int(time.time()) // 30)
        accepted = self.client.post("/login/two-step", data={"code": code, "_csrf_token": self.csrf()})
        self.assertEqual(accepted.status_code, 302)
        with self.client.session_transaction() as state:
            self.assertTrue(state["logged_in"])
            self.assertNotIn("mfa_pending", state)

    def test_failed_mfa_does_not_authenticate(self):
        self.enrol()
        with self.client.session_transaction() as state:
            state.clear()
        self.password_login()
        with mock.patch.object(self.mod.time, "sleep"):
            response = self.client.post("/login/two-step", data={"code": "000000", "_csrf_token": self.csrf()})
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as state:
            self.assertNotIn("logged_in", state)

    def test_recovery_code_is_one_time(self):
        _, recovery_codes = self.enrol()
        recovery = recovery_codes[0]
        with self.client.session_transaction() as state:
            state.clear()
        self.password_login()
        response = self.client.post("/login/two-step", data={"code": recovery, "_csrf_token": self.csrf()})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as state:
            state.clear()
        self.password_login()
        with mock.patch.object(self.mod.time, "sleep"):
            replay = self.client.post("/login/two-step", data={"code": recovery, "_csrf_token": self.csrf()})
        self.assertEqual(replay.status_code, 200)
        with self.client.session_transaction() as state:
            self.assertNotIn("logged_in", state)

    def test_disable_requires_password_and_second_factor(self):
        secret, _ = self.enrol()
        self.mod.run("UPDATE settings SET mfa_last_counter=-1 WHERE id=1")
        code = self.mod.totp_at(secret, int(time.time()) // 30)
        with mock.patch.object(self.mod.time, "sleep"):
            denied = self.client.post("/security/two-step/disable", data={
                "current_password": "wrong", "code": code, "_csrf_token": self.csrf(),
            })
        self.assertEqual(denied.status_code, 200)
        self.assertEqual(self.mod.settings()["mfa_enabled"], 1)
        self.mod.run("UPDATE settings SET mfa_last_counter=-1 WHERE id=1")
        accepted = self.client.post("/security/two-step/disable", data={
            "current_password": "admin123", "code": code, "_csrf_token": self.csrf(),
        })
        self.assertEqual(accepted.status_code, 302)
        row = self.mod.settings()
        self.assertEqual(row["mfa_enabled"], 0)
        self.assertEqual(row["mfa_secret_encrypted"], "")

    def test_expired_challenge_cannot_be_used(self):
        self.enrol()
        with self.client.session_transaction() as state:
            state.clear()
        self.password_login()
        with self.client.session_transaction() as state:
            pending = dict(state["mfa_pending"])
            pending["created"] = 0
            state["mfa_pending"] = pending
        response = self.client.get("/login/two-step")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/login"))

    def test_invalid_mfa_codes_are_rate_limited(self):
        self.enrol()
        with self.client.session_transaction() as state:
            state.clear()
        self.password_login()
        with mock.patch.object(self.mod.time, "sleep"):
            for _ in range(5):
                response = self.client.post("/login/two-step", data={"code": "000000", "_csrf_token": self.csrf()})
                self.assertEqual(response.status_code, 200)
            blocked = self.client.post("/login/two-step", data={"code": "000000", "_csrf_token": self.csrf()})
        self.assertEqual(blocked.status_code, 429)

    def test_totp_code_cannot_be_replayed(self):
        secret, _ = self.enrol()
        self.mod.run("UPDATE settings SET mfa_last_counter=-1 WHERE id=1")
        with self.client.session_transaction() as state:
            state.clear()
        self.password_login()
        code = self.mod.totp_at(secret, int(time.time()) // 30)
        first = self.client.post("/login/two-step", data={"code": code, "_csrf_token": self.csrf()})
        self.assertEqual(first.status_code, 302)
        with self.client.session_transaction() as state:
            state.clear()
        self.password_login()
        with mock.patch.object(self.mod.time, "sleep"):
            replay = self.client.post("/login/two-step", data={"code": code, "_csrf_token": self.csrf()})
        self.assertEqual(replay.status_code, 200)

    def test_emergency_cli_requires_render_held_token_and_audits_reset(self):
        self.enrol()
        os.environ["MFA_BOOTSTRAP_TOKEN"] = "render-held-test-value"
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["mfa-emergency-disable", "--confirm", "DISABLE-MFA"],
                               input="render-held-test-value\n")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(self.mod.settings()["mfa_enabled"], 0)
        event = self.mod.q("SELECT event_type FROM login_security_events ORDER BY id DESC", one=True)
        self.assertEqual(event["event_type"], "mfa_emergency_disabled")


if __name__ == "__main__":
    unittest.main()
