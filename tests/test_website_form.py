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
        self.assertIn("Skipped: phone number", lead["customer_sms_status"])
        self.assertIn("Queued: acknowledgement email", lead["customer_email_status"])

    def test_website_form_does_not_automatically_create_or_alert_an_ai_draft(self):
        with mock.patch.object(
            self.appmod,
            "ensure_ai_draft_for_intake",
            side_effect=AssertionError("Website submission must not create the old automatic AI draft"),
        ), mock.patch.object(
            self.appmod,
            "notify_owner_ai_draft_ready",
            side_effect=AssertionError("Website submission must not send the old AI draft alert"),
        ):
            response = self.post_form(phone="07802 563213")
        self.assertEqual(response.status_code, 200)
        result = response.get_json()["automation"]
        self.assertFalse(result["ai_draft"]["ok"])
        self.assertNotIn("ai_draft_owner_alert", result)

    def test_clicksend_insufficient_credit_is_reported_as_failure(self):
        clicksend_response = {
            "response_code": "SUCCESS",
            "response_msg": "Messages queued for delivery",
            "data": {"messages": [{
                "message_id": "test-message-id",
                "status": "INSUFFICIENT_CREDIT",
            }]},
        }
        with mock.patch.dict(os.environ, {
            "CLICKSEND_USERNAME": "test-user",
            "CLICKSEND_API_KEY": "test-key",
        }, clear=False), mock.patch.object(
            self.appmod, "http_post_basic_json", return_value=__import__("json").dumps(clicksend_response)
        ):
            ok, message = self.appmod.send_clicksend_env_sms("07802563213", "New lead")
        self.assertFalse(ok)
        self.assertIn("INSUFFICIENT_CREDIT", message)
        event = self.appmod.q("SELECT event_type, status FROM sms_events ORDER BY id DESC LIMIT 1", one=True)
        self.assertEqual(event["event_type"], "send_failed")
        self.assertEqual(event["status"], "Insufficient_Credit")

    def test_nested_ai_owner_alert_result_does_not_break_form_response(self):
        automation = {
            "ai_draft": (True, "AI draft prepared for approval."),
            "ai_draft_owner_alert": {
                "email": (True, "Email sent."),
                "sms": (True, "SMS accepted."),
            },
        }
        with mock.patch.object(self.appmod, "run_website_enquiry_automation", return_value=automation):
            response = self.app.test_client().post("/api/website-form", data={
                "name": "Sarah Style Test",
                "phone": "07802 563213",
                "email": "customer@example.com",
                "postcode": "SY8 2BH",
                "service": "Carpet cleaning",
                "rooms": "5",
                "stains": "Makeup",
            })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["automation"]["ai_draft_owner_alert"]["ok"])
        self.assertIn("email: Email sent", body["automation"]["ai_draft_owner_alert"]["message"])

    def test_two_page_quote_preserves_carpet_and_upholstery_as_distinct_services(self):
        response = self.post_form(
            phone="07802 563213",
            service="",
            building_type="Private home or residence",
            rooms="5",
            stains="Makeup",
            extras=["Upholstery"],
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertNotIn("service required", body["missing_details"])
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (body["lead_id"],), one=True)
        self.assertEqual(lead["what_cleaned"], "Carpet cleaning and Upholstery cleaning")
        self.assertEqual(lead["upholstery"], "Yes")
        self.assertEqual(lead["number_rooms"], "5")
        self.assertEqual(lead["stains"], "Makeup")

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

    def test_website_form_sends_thank_you_but_holds_follow_up_sms_for_approval(self):
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
             mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "Thank-you SMS sent")) as sms_send:
            response = self.app.test_client().post("/api/website-form", data=payload)

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (body["lead_id"],), one=True)
        queued = self.appmod.q("SELECT * FROM enquiry_follow_up_queue WHERE lead_id=?", (body["lead_id"],), one=True)
        self.assertIsNotNone(queued)
        self.assertEqual(queued["status"], "Awaiting approval")
        self.assertIn("quick call", queued["body"].lower())
        acknowledgement = self.appmod.q("SELECT * FROM enquiry_acknowledgement_queue WHERE lead_id=?", (body["lead_id"],), one=True)
        self.assertIsNotNone(acknowledgement)
        self.assertEqual(acknowledgement["status"], "Queued")
        self.assertIn("Queued: acknowledgement text", lead["customer_sms_status"])
        self.assertIn("Pending Paul approval", body["automation"]["follow_up_sms_queue"]["message"])
        sms_send.assert_not_called()

    def test_delayed_acknowledgement_prefers_valid_phone_and_does_not_email(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status) VALUES (?,?,?,?)""",
            ("SMS Customer", "07802 563213", "sms@example.com", "Waiting for review"))
        self.appmod.schedule_enquiry_acknowledgement(
            lead_id, data={"name": "SMS Customer", "phone": "07802 563213", "email": "sms@example.com"}, delay_minutes=-1
        )
        with mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS accepted")) as sms_send, \
             mock.patch.object(self.appmod, "send_env_email", return_value=(True, "Email sent")) as email_send:
            result = self.appmod.run_due_enquiry_acknowledgements()
        self.assertEqual(result[0]["channel"], "sms")
        sms_send.assert_called_once()
        email_send.assert_not_called()

    def test_acknowledgement_uses_requested_spacing_signature_and_no_hyphens(self):
        message = self.appmod.enquiry_acknowledgement_text({"name": "Paul Nicholas"})
        self.assertTrue(message.startswith("Hi, thank you very much for your enquiry."))
        self.assertIn("I've just received the information you've sent over and had a look through it.", message)
        self.assertIn("Is it possible for you to send me a few photos?", message)
        self.assertTrue(message.endswith("Thanks,\nPaul\nThe Carpet Cleaning Company"))
        self.assertNotIn("-", message)
        self.assertNotIn("—", message)

    def test_delayed_acknowledgement_uses_email_for_invalid_phone(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status) VALUES (?,?,?,?)""",
            ("Email Customer", "not-a-phone", "email@example.com", "Waiting for review"))
        self.appmod.schedule_enquiry_acknowledgement(
            lead_id, data={"name": "Email Customer", "phone": "not-a-phone", "email": "email@example.com"}, delay_minutes=-1
        )
        with mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS accepted")) as sms_send, \
             mock.patch.object(self.appmod, "send_env_email", return_value=(True, "Email sent")) as email_send:
            result = self.appmod.run_due_enquiry_acknowledgements()
        self.assertEqual(result[0]["channel"], "email")
        sms_send.assert_not_called()
        email_send.assert_called_once()

    def test_approved_follow_up_sms_button_sends_follow_up_message(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status, source, customer_sms_status, follow_up_status)
            VALUES (?,?,?,?,?,?,?)""",
            ("Approved Customer", "07802 563213", "approved@example.com", "Waiting for review", "Website form", "Sent: Thank-you SMS sent", "Follow up required"))
        self.appmod.run("""INSERT INTO enquiry_follow_up_queue
            (lead_id, phone, body, due_at, status)
            VALUES (?,?,?,?,?)""",
            (lead_id, "07802 563213", "Can I give you a quick call?", "2026-07-16T10:00:00+01:00", "Awaiting approval"))
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            with mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS sent")) as sms_send, \
                 mock.patch.object(self.appmod, "send_owner_customer_message_copy", return_value=(True, "copy sent")):
                response = client.post(f"/intake-forms/{lead_id}/customer-message", data={"action": "send_follow_up_sms"})

        self.assertEqual(response.status_code, 302)
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
        queued = self.appmod.q("SELECT * FROM enquiry_follow_up_queue WHERE lead_id=?", (lead_id,), one=True)
        self.assertEqual(queued["status"], "Sent")
        self.assertIn("Follow-up SMS sent", lead["follow_up_status"])
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

    def test_mark_test_clears_alerts_and_skips_follow_up_queue(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status, source, follow_up_status)
            VALUES (?,?,?,?,?,?)""",
            ("TEST Paul", "07802 563213", "test@example.com", "Waiting for review", "Website form", "Follow up required"))
        self.appmod.run("""INSERT INTO enquiry_follow_up_queue
            (lead_id, phone, body, due_at, status) VALUES (?,?,?,?,?)""",
            (lead_id, "07802 563213", "Test follow up", "2026-07-16T10:00:00+01:00", "Awaiting approval"))
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.post(f"/intake-forms/{lead_id}/quick-action", data={"action": "mark_test"})
        self.assertEqual(response.status_code, 302)
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
        queued = self.appmod.q("SELECT * FROM enquiry_follow_up_queue WHERE lead_id=?", (lead_id,), one=True)
        self.assertEqual(lead["is_test"], 1)
        self.assertEqual(lead["ignore_alerts"], 1)
        self.assertEqual(lead["follow_up_status"], "Test - alerts ignored")
        self.assertEqual(queued["status"], "Skipped")

    def test_test_enquiry_cannot_schedule_follow_up(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, is_test, ignore_alerts) VALUES (?,?,1,1)""", ("Test", "07802 563213"))
        ok, message = self.appmod.schedule_enquiry_follow_up_sms(lead_id)
        self.assertFalse(ok)
        self.assertIn("alerts are ignored", message)

    def test_named_booking_times_have_safe_automation_times(self):
        self.assertEqual(self.appmod.parse_hhmm("Morning"), (9, 0))
        self.assertEqual(self.appmod.parse_hhmm("Afternoon"), (13, 0))
        self.assertEqual(self.appmod.parse_hhmm("Time to be confirmed"), (9, 0))

    def test_named_booking_time_is_rendered_in_customer_message(self):
        rendered = self.appmod.render_simple_template(
            "Your booking is {{time}}.",
            {"{{time}}": "Afternoon"},
        )
        self.assertEqual(rendered, "Your booking is Afternoon.")

    def test_booking_time_dropdown_contains_named_and_exact_options(self):
        options = self.appmod.BOOKING_TIME_OPTIONS
        self.assertIn("Morning", options)
        self.assertIn("Afternoon", options)
        self.assertIn("Time to be confirmed", options)
        self.assertIn("09:30", options)
