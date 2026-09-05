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
        os.environ.pop("VAPID_PUBLIC_KEY", None)
        os.environ.pop("VAPID_PRIVATE_KEY", None)
        os.environ.pop("VAPID_SUBJECT", None)
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
        self.assertEqual(response.headers["X-CRM-UI-Version"], "20260905.16")
        self.assertIn(b'data-ui-build="20260905.16"', response.data)
        self.assertIn(b"app-shell-20260905-9", response.data)
        self.assertIn(b"app-theme.css", response.data)
        self.assertIn(b"Carpet Clean Pro", response.data)
        self.assertNotIn(b"Business workspace", response.data)

    def test_notification_feed_is_authenticated_real_and_seen_state_is_ui_only(self):
        denied = self.client.get("/notifications")
        self.assertIn(denied.status_code, (302, 400))
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

    def test_job_detail_uses_single_summary_dynamic_next_step_and_progress(self):
        self.login()
        customer_id = self.mod.run(
            "INSERT INTO customers(first_name,last_name) VALUES (?,?)",
            ("Mark", "Cooksey"),
        )
        job_id = self.mod.run(
            "INSERT INTO jobs(customer_id,title,job_date,job_time,amount,status) VALUES (?,?,?,?,?,?)",
            (customer_id, "Lounge and stairs", "2026-09-17", "09:30", 175, "Booked"),
        )
        response = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'class="job-summary-card"'), 1)
        self.assertEqual(response.data.count(b">Open customer <"), 1)
        self.assertIn(b"17 Sep 2026", response.data)
        self.assertIn(b'href="/calendar?view=day&amp;day=2026-09-17"', response.data)
        self.assertIn(b"Agreed price", response.data)
        self.assertIn(b"\xc2\xa3175.00", response.data)
        self.assertIn(b'class="job-next-card"', response.data)
        self.assertIn(b"Email and phone added", response.data)
        self.assertEqual(response.data.count(b'class="job-progress-stage '), 6)
        self.assertIn(b"More job actions", response.data)

    def test_pwa_manifest_service_worker_and_install_ui(self):
        manifest = self.client.get("/app.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        data = manifest.get_json()
        self.assertEqual(data["name"], "Carpet Clean Pro CRM")
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["start_url"], "/dashboard?source=pwa")
        self.assertTrue(any(icon["sizes"] == "512x512" for icon in data["icons"]))
        worker = self.client.get("/service-worker.js")
        self.assertEqual(worker.status_code, 200)
        self.assertIn(b"notificationclick", worker.data)
        self.assertIn(b"/offline", worker.data)
        self.assertEqual(worker.headers["Service-Worker-Allowed"], "/")
        login = self.client.get("/login")
        self.assertIn(b'app.webmanifest', login.data)
        self.assertIn(b'apple-mobile-web-app-capable', login.data)
        self.login()
        page = self.client.get("/notifications")
        self.assertIn(b"Add to Home Screen", page.data)
        self.assertIn(b"Enable phone alerts", page.data)

    def test_push_subscription_is_authenticated_validated_encrypted_and_removable(self):
        denied = self.client.post("/api/push/subscriptions", json={})
        self.assertIn(denied.status_code, (302, 400))
        self.login()
        token = self.csrf()
        invalid = self.client.post("/api/push/subscriptions", json={"subscription": {"endpoint": "http://bad"}}, headers={"X-CSRF-Token": token})
        self.assertEqual(invalid.status_code, 400)
        subscription = {"endpoint": "https://push.example.test/subscription/abc", "keys": {"p256dh": "p" * 80, "auth": "a" * 24}}
        saved = self.client.post("/api/push/subscriptions", json={"subscription": subscription, "preferences": {"money": False}}, headers={"X-CSRF-Token": token})
        self.assertEqual(saved.status_code, 200)
        row = self.mod.q("SELECT * FROM push_subscriptions", one=True)
        self.assertNotIn("push.example.test", row["subscription_encrypted"])
        self.assertEqual(self.mod.decrypt_push_subscription(row["subscription_encrypted"])["endpoint"], subscription["endpoint"])
        self.assertFalse(__import__("json").loads(row["preferences_json"])["money"])
        again = self.client.post("/api/push/subscriptions", json={"subscription": subscription}, headers={"X-CSRF-Token": token})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(self.mod.q("SELECT COUNT(*) AS c FROM push_subscriptions", one=True)["c"], 1)
        removed = self.client.delete("/api/push/subscriptions", json={"subscription": subscription}, headers={"X-CSRF-Token": token})
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.mod.q("SELECT enabled FROM push_subscriptions", one=True)["enabled"], 0)

    def test_push_delivery_is_deduplicated_and_payload_is_private(self):
        os.environ.update(VAPID_PUBLIC_KEY="public-key", VAPID_PRIVATE_KEY="private-key", VAPID_SUBJECT="mailto:owner@example.test")
        self.login()
        subscription = {"endpoint": "https://push.example.test/subscription/secure", "keys": {"p256dh": "p" * 80, "auth": "a" * 24}}
        self.mod.run("INSERT INTO push_subscriptions(endpoint_hash,subscription_encrypted,preferences_json,enabled) VALUES (?,?,?,1)", (
            __import__("hashlib").sha256(subscription["endpoint"].encode()).hexdigest(), self.mod.encrypt_push_subscription(subscription), "{}"))
        self.mod.run("INSERT INTO intake_submissions(name,email,phone,status,follow_up_status,is_test,ignore_alerts) VALUES (?,?,?,?,?,0,0)",
                     ("Sensitive Customer", "secret@example.test", "07700111222", "New", "Follow up required"))
        payloads = []
        first = self.mod.run_due_push_notifications(sender=lambda sub, payload: payloads.append(payload))
        second = self.mod.run_due_push_notifications(sender=lambda sub, payload: payloads.append(payload))
        self.assertGreaterEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        encoded = __import__("json").dumps(payloads)
        self.assertNotIn("Sensitive Customer", encoded)
        self.assertNotIn("secret@example.test", encoded)
        self.assertNotIn("07700111222", encoded)
        self.assertTrue(all(payload["url"].startswith("/") for payload in payloads))
        self.assertEqual(self.mod.q("SELECT COUNT(*) AS c FROM push_delivery_log", one=True)["c"], len(payloads))

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
