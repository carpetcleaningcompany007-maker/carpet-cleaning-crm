import importlib
import os
import tempfile
import unittest
from unittest import mock


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        os.environ["CRM_SECRET_KEY"] = "test-secret-that-is-long-and-random-enough"
        os.environ["CRM_PUBLIC_BASE_URL"] = "https://crm.example.test"
        import app
        self.mod = importlib.reload(app)
        self.app = self.mod.app
        self.production_secure_cookie = self.app.config["SESSION_COOKIE_SECURE"]
        self.app.config.update(TESTING=False, SESSION_COOKIE_SECURE=False)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.mod.init_db()
        self.client = self.app.test_client()

    def tearDown(self):
        self.ctx.pop()
        os.environ.pop("TWILIO_AUTH_TOKEN", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def csrf(self):
        self.client.get("/login")
        with self.client.session_transaction() as session:
            return session["csrf_token"]

    def login(self):
        token = self.csrf()
        with mock.patch.object(self.mod.time, "sleep"):
            response = self.client.post("/login", data={
                "username": "admin", "password": "admin123", "_csrf_token": token,
            })
        return response

    def test_security_headers_and_cookie_settings(self):
        response = self.client.get("/login")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertTrue(self.production_secure_cookie)
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])

    def test_login_rotates_session_and_requires_csrf(self):
        with self.client.session_transaction() as session:
            session["attacker_marker"] = "remove-me"
        token = self.csrf()
        rejected = self.client.post("/login", data={"username": "admin", "password": "admin123"})
        self.assertEqual(rejected.status_code, 400)
        with mock.patch.object(self.mod.time, "sleep"):
            accepted = self.client.post("/login", data={
                "username": "admin", "password": "admin123", "_csrf_token": token,
            })
        self.assertEqual(accepted.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertTrue(session["logged_in"])
            self.assertNotIn("attacker_marker", session)
            self.assertTrue(session.permanent)

    def test_authenticated_mutation_without_csrf_is_rejected(self):
        self.login()
        response = self.client.post("/customers/new", data={"first_name": "A", "last_name": "B"})
        self.assertEqual(response.status_code, 400)

    def test_login_rate_limit_blocks_sixth_failure(self):
        token = self.csrf()
        with mock.patch.object(self.mod.time, "sleep"):
            for _ in range(5):
                response = self.client.post("/login", data={
                    "username": "admin", "password": "wrong", "_csrf_token": token,
                })
                self.assertEqual(response.status_code, 200)
            blocked = self.client.post("/login", data={
                "username": "admin", "password": "wrong", "_csrf_token": token,
            })
        self.assertEqual(blocked.status_code, 429)

    def test_twilio_signature_is_enforced_when_configured(self):
        os.environ["TWILIO_AUTH_TOKEN"] = "configured-token"
        response = self.client.post("/webhooks/sms/inbound/twilio", data={"From": "07000000000", "Body": "hello"})
        self.assertEqual(response.status_code, 403)

    def test_automation_endpoint_no_longer_accepts_get(self):
        response = self.client.get("/automation/run-due")
        self.assertEqual(response.status_code, 405)

    def test_customer_update_tokens_use_timed_serializer(self):
        token = self.mod.signed_intake_update_token(42)
        self.assertEqual(self.mod.lead_id_from_update_token(token), 42)
        self.assertGreaterEqual(token.count("."), 2)


if __name__ == "__main__":
    unittest.main()
