import importlib
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo


class WebsiteFormTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        os.environ.pop("OWNER_ALERT_EMAIL", None)
        os.environ.pop("OWNER_ALERT_MOBILE", None)
        import app
        self.appmod = importlib.reload(app)
        self.app = self.appmod.app
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.appmod.init_db()

    def tearDown(self):
        self.ctx.pop()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def post_form(self, **overrides):
        payload = {
            "name": "Website Form Test",
            "phone": "not-a-phone",
            "email": "customer@example.com",
            "postcode": "SY8 1AA",
            "service": "Carpet cleaning",
            "areas": "1",
            "contact_consent": "Yes",
        }
        payload.update(overrides)
        with mock.patch.object(self.appmod, "send_env_email", return_value=(False, "Email disabled for test")), \
             mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(False, "SMS disabled for test")), \
             mock.patch.object(self.appmod, "schedule_enquiry_follow_up_sms", return_value=(False, "Follow-up disabled for test")):
            return self.app.test_client().post("/api/website-form", data=payload)

    def test_website_form_accepts_valid_email_when_phone_needs_checking(self):
        response = self.post_form()
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["complete"])
        self.assertIn("address", body["missing_details"])
        self.assertIn("Request missing details", body["next_action"])

        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (body["lead_id"],), one=True)
        self.assertIn("Phone number needs checking", lead["job_notes"])
        self.assertIn("Missing details:", lead["job_notes"])
        self.assertEqual(lead["status"], "Needs missing details")
        self.assertEqual(lead["follow_up_status"], "Request missing details")
        self.assertEqual(lead["customer_sms_status"], "Skipped: Customer phone number is missing or needs checking.")

    def test_website_form_rejects_invalid_phone_without_valid_email(self):
        response = self.post_form(email="not-an-email")
        self.assertEqual(response.status_code, 400)
        self.assertIn("valid UK phone number", response.get_json()["error"])

    def test_website_form_does_not_upload_to_xero_automatically(self):
        with mock.patch.object(self.appmod, "xero_api_request", side_effect=AssertionError("Xero should not be called")):
            response = self.post_form(phone="07802 563213")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (body["lead_id"],), one=True)
        self.assertEqual(lead["xero_sync_status"], "Pending manual approval")
        self.assertIn("manual approval required", body["automation"]["xero"]["message"])

    def test_complete_website_form_marks_enquiry_ready_for_review(self):
        response = self.post_form(
            phone="07802 563213",
            address="1 High Street",
            parking="Driveway parking",
            preferred_days_times="Tuesday morning",
            notes="Lounge carpet with coffee stain",
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["complete"])
        self.assertEqual(body["missing_details"], [])
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (body["lead_id"],), one=True)
        self.assertEqual(lead["status"], "Waiting for review")
        self.assertEqual(lead["follow_up_status"], "Follow up required")

    def test_website_form_prepares_customer_messages_but_waits_for_approval(self):
        class FixedLateDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 7, 15, 21, 30, tzinfo=tz or ZoneInfo("Europe/London"))

        payload = {
            "name": "Late Customer",
            "phone": "07802 563213",
            "email": "late@example.com",
            "postcode": "SY8 1AA",
            "service": "Carpet cleaning",
            "areas": "2 bedrooms",
            "contact_consent": "Yes",
        }
        with mock.patch.object(self.appmod, "datetime", FixedLateDateTime), \
             mock.patch.object(self.appmod, "send_env_email", return_value=(False, "Email disabled for test")), \
             mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(False, "SMS should not send automatically")) as sms_send:
            response = self.app.test_client().post("/api/website-form", data=payload)

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (body["lead_id"],), one=True)
        queued = self.appmod.q("SELECT * FROM enquiry_follow_up_queue WHERE lead_id=?", (body["lead_id"],), one=True)
        self.assertIsNone(queued)
        self.assertIn("Pending Paul approval", lead["customer_email_status"])
        self.assertIn("Pending Paul approval", lead["customer_sms_status"])
        self.assertIn("Paul approval is required", body["automation"]["follow_up_sms_queue"]["message"])
        sms_send.assert_not_called()

    def test_approved_customer_sms_button_sends_prepared_message(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status, source, customer_sms_status, follow_up_status)
            VALUES (?,?,?,?,?,?,?)""",
            ("Approved Customer", "07802 563213", "approved@example.com", "Waiting for review", "Website form", "Pending Paul approval", "Follow up required"))
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            with mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS sent")) as sms_send, \
                 mock.patch.object(self.appmod, "send_owner_customer_message_copy", return_value=(True, "copy sent")):
                response = client.post(f"/intake-forms/{lead_id}/customer-message", data={"action": "send_prepared_sms"})

        self.assertEqual(response.status_code, 302)
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
        self.assertIn("Sent: SMS sent", lead["customer_sms_status"])
        sms_send.assert_called_once()

    def test_due_enquiry_sms_is_not_sent_before_ten_am(self):
        class FixedMorningDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 7, 16, 9, 15, tzinfo=tz or ZoneInfo("Europe/London"))

        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status, source, customer_sms_status, follow_up_status)
            VALUES (?,?,?,?,?,?,?)""",
            ("Morning Customer", "07802 563213", "morning@example.com", "Waiting for review", "Website form", "Pending", "Follow up required"))
        self.appmod.run("""INSERT INTO enquiry_follow_up_queue
            (lead_id, phone, body, due_at, status)
            VALUES (?,?,?,?,?)""",
            (lead_id, "07802 563213", "Polite queued text", "2026-07-16T09:00:00+01:00", "Queued"))

        with mock.patch.object(self.appmod, "datetime", FixedMorningDateTime), \
             mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "Should not send before 10")) as sms_send:
            result = self.appmod.run_due_enquiry_follow_up_sms()

        row = self.appmod.q("SELECT * FROM enquiry_follow_up_queue WHERE lead_id=?", (lead_id,), one=True)
        self.assertEqual(result[0]["status"], "Queued")
        self.assertIn("2026-07-16T10:00:00", row["due_at"])
        self.assertEqual(row["sent_at"], "")
        sms_send.assert_not_called()
