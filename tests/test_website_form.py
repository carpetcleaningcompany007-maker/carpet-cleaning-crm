import importlib
import os
import tempfile
import unittest
from unittest import mock


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

        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (body["lead_id"],), one=True)
        self.assertIn("Phone number needs checking", lead["job_notes"])
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
