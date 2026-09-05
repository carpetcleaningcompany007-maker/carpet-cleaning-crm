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

    def test_authenticated_shell_cannot_be_served_from_stale_browser_cache(self):
        self.login()
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-CRM-UI-Version"], "20260905.12")
        self.assertIn(b'data-ui-build="20260905.12"', response.data)
        self.assertIn(b"app-shell-20260905-9", response.data)
        self.assertIn(b"app-theme.css", response.data)
        self.assertIn(b"Carpet Clean Pro", response.data)
        self.assertNotIn(b"Business workspace", response.data)

    def test_notification_feed_is_authenticated_real_and_seen_state_is_ui_only(self):
        denied = self.client.get("/notifications")
        self.assertEqual(denied.status_code, 302)
        self.login()
        lead_id = self.mod.run("""INSERT INTO intake_submissions(name,status,follow_up_status,is_test,ignore_alerts)
                                  VALUES ('Real Alert','New','Follow up required',0,0)""")
        response = self.client.get("/notifications")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Real Alert", response.data)
        token = self.csrf()
        marked = self.client.post("/notifications/mark-seen", data={"_csrf_token": token})
        self.assertEqual(marked.status_code, 302)
        self.assertIsNotNone(self.mod.q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True))
        state = self.mod.q("SELECT * FROM ui_notification_state WHERE notification_key=?", (f"enquiry:{lead_id}",), one=True)
        self.assertTrue(state["seen_at"])

    def test_mobile_more_uses_all_sidebar_toggles(self):
        source_path = os.path.join(os.path.dirname(self.mod.__file__), "static", "app.js")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("querySelectorAll('[data-sidebar-toggle]')", source)
        self.assertIn("event.preventDefault()", source)
        self.login()
        response = self.client.get("/dashboard")
        self.assertIn(b"mobile-more-20260905-1", response.data)
        self.assertGreaterEqual(response.data.count(b"data-sidebar-toggle"), 2)

    def test_mobile_booking_entry_opens_real_job_form_and_calendar_agenda(self):
        self.login()
        response = self.client.get("/jobs/new")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/jobs?new=1", response.headers["Location"])
        form = self.client.get(response.headers["Location"])
        self.assertIn(b'modal-shell is-open', form.data)
        self.assertIn(b'action="/jobs/new"', form.data)
        calendar = self.client.get("/calendar")
        self.assertEqual(calendar.status_code, 200)
        self.assertIn(b'class="month-grid"', calendar.data)
        self.assertEqual(calendar.data.count(b'class="month-day '), 35)
        self.assertNotIn(b"calendar-mobile-agenda", calendar.data)

        dated = self.client.get("/jobs/new?date=2026-09-17")
        self.assertIn("job_date=2026-09-17", dated.headers["Location"])
        dated_form = self.client.get(dated.headers["Location"])
        self.assertIn(b'value="2026-09-17"', dated_form.data)

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
