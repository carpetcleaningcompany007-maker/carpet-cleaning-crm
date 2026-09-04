import importlib
import json
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
        self.app.config["TESTING"] = True
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

    def test_owner_sms_is_not_sent_when_new_enquiry_email_succeeds(self):
        with mock.patch.dict(os.environ, {
            "OWNER_ALERT_EMAIL": "owner@example.com",
            "OWNER_ALERT_MOBILE": "07802 563213",
        }, clear=False), mock.patch.object(
            self.appmod, "customer_sms_hours_open", return_value=True
        ), mock.patch.object(
            self.appmod, "send_env_email", return_value=(True, "Email sent")
        ) as email_send, mock.patch.object(
            self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS sent")
        ) as sms_send:
            response = self.app.test_client().post("/api/website-form", data={
                "name": "Owner Email Success",
                "phone": "07802 563213",
                "email": "customer@example.com",
                "postcode": "SY8 1AA",
                "service": "Carpet cleaning",
                "areas": "2 rooms",
                "contact_consent": "Yes",
            })

        self.assertEqual(response.status_code, 200)
        email_send.assert_called_once()
        sms_send.assert_called_once()
        owner_notice = sms_send.call_args.args[1]
        self.assertIn("NEW WEBSITE ENQUIRY", owner_notice)
        self.assertIn("Customer text due in about 5 minutes", owner_notice)
        self.assertIn("#customer-message-approval", owner_notice)

    def test_owner_gets_sms_warning_only_when_new_enquiry_email_fails(self):
        with mock.patch.dict(os.environ, {
            "OWNER_ALERT_EMAIL": "owner@example.com",
            "OWNER_ALERT_MOBILE": "07802 563213",
        }, clear=False), mock.patch.object(
            self.appmod, "customer_sms_hours_open", return_value=True
        ), mock.patch.object(
            self.appmod, "send_env_email", return_value=(False, "SMTP unavailable")
        ), mock.patch.object(
            self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS accepted")
        ) as sms_send:
            response = self.app.test_client().post("/api/website-form", data={
                "name": "Owner Email Failure",
                "phone": "07802 563213",
                "email": "customer@example.com",
                "postcode": "SY8 1AA",
                "service": "Carpet cleaning",
                "areas": "2 rooms",
                "contact_consent": "Yes",
            })

        self.assertEqual(response.status_code, 200)
        sms_send.assert_called_once()
        warning = sms_send.call_args.args[1]
        self.assertIn("Owner email alert FAILED", warning)
        self.assertIn("Owner Email Failure", warning)
        self.assertIn("SMTP unavailable", warning)

    def test_new_enquiry_email_uses_saved_owner_contact_fallback(self):
        with mock.patch.object(
            self.appmod, "owner_contact_form_recipients", return_value=("saved-owner@example.com", "07802 563213")
        ), mock.patch.object(
            self.appmod, "customer_sms_hours_open", return_value=True
        ), mock.patch.object(
            self.appmod, "send_env_email", return_value=(True, "Email sent")
        ) as email_send, mock.patch.object(
            self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS sent")
        ):
            response = self.app.test_client().post("/api/website-form", data={
                "name": "Saved Owner Email",
                "phone": "07802 563213",
                "email": "customer@example.com",
                "postcode": "SY8 1AA",
                "service": "Carpet cleaning",
                "areas": "2 rooms",
                "contact_consent": "Yes",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(email_send.call_args.args[0], "saved-owner@example.com")

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
        with mock.patch.object(self.appmod, "customer_sms_hours_open", return_value=True), \
             mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS accepted")) as sms_send, \
             mock.patch.object(self.appmod, "send_env_email", return_value=(True, "Email sent")) as email_send:
            result = self.appmod.run_due_enquiry_acknowledgements()
        self.assertEqual(result[0]["channel"], "sms")
        sms_send.assert_called_once()
        email_send.assert_not_called()

    def test_acknowledgement_waits_for_clicksend_delivery_receipt(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status) VALUES (?,?,?,?)""",
            ("Receipt Customer", "07802 563213", "receipt@example.com", "Waiting for review"))
        self.appmod.schedule_enquiry_acknowledgement(
            lead_id, data={"phone": "07802 563213", "email": "receipt@example.com"}, delay_minutes=-1
        )
        with mock.patch.object(self.appmod, "customer_sms_hours_open", return_value=True), mock.patch.object(
            self.appmod, "send_clicksend_env_sms",
            return_value=(True, "SMS accepted by ClickSend for +447802563213. Message ID: receipt-123. Status: SUCCESS."),
        ):
            self.appmod.run_due_enquiry_acknowledgements()
        queued = self.appmod.q("SELECT * FROM enquiry_acknowledgement_queue WHERE lead_id=?", (lead_id,), one=True)
        self.assertEqual(queued["status"], "Accepted")
        self.assertEqual(queued["external_id"], "receipt-123")
        self.assertEqual(queued["delivered_at"], "")

        with mock.patch.object(self.appmod, "owner_contact_form_recipients", return_value=("owner@example.com", "07802 563213")), \
             mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS accepted")) as owner_confirmation:
            self.appmod.process_acknowledgement_delivery_receipt("receipt-123", "DELIVERED")
        owner_confirmation.assert_called_once()
        self.assertIn("Customer enquiry text delivered to", owner_confirmation.call_args.args[1])
        queued = self.appmod.q("SELECT * FROM enquiry_acknowledgement_queue WHERE lead_id=?", (lead_id,), one=True)
        self.assertEqual(queued["status"], "Delivered")
        self.assertTrue(queued["delivered_at"])

    def test_failed_clicksend_receipt_uses_email_fallback(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status) VALUES (?,?,?,?)""",
            ("Fallback Customer", "07802 563213", "fallback@example.com", "Waiting for review"))
        self.appmod.schedule_enquiry_acknowledgement(
            lead_id, data={"phone": "07802 563213", "email": "fallback@example.com"}, delay_minutes=-1
        )
        with mock.patch.object(self.appmod, "customer_sms_hours_open", return_value=True), mock.patch.object(
            self.appmod, "send_clicksend_env_sms",
            return_value=(True, "SMS accepted by ClickSend for +447802563213. Message ID: failed-123. Status: SUCCESS."),
        ):
            self.appmod.run_due_enquiry_acknowledgements()
        with mock.patch.object(self.appmod, "send_env_email", return_value=(True, "Email sent")) as email_send:
            self.appmod.process_acknowledgement_delivery_receipt("failed-123", "UNDELIVERABLE")
        email_send.assert_called_once()
        queued = self.appmod.q("SELECT * FROM enquiry_acknowledgement_queue WHERE lead_id=?", (lead_id,), one=True)
        self.assertEqual(queued["status"], "Email fallback sent")
        self.assertTrue(queued["fallback_sent_at"])

    def test_unconfirmed_clicksend_delivery_uses_email_fallback(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status, is_test, ignore_alerts) VALUES (?,?,?,?,0,0)""",
            ("Unconfirmed Customer", "07802 563213", "unconfirmed@example.com", "Waiting for review"))
        self.appmod.run("""INSERT INTO enquiry_acknowledgement_queue
            (lead_id, payload_json, due_at, sent_at, channel, status, external_id, created_at)
            VALUES (?,?,datetime('now','-10 minutes'),datetime('now','-6 minutes'),'sms','Accepted','pending-123',datetime('now','-10 minutes'))""",
            (lead_id, '{"email":"unconfirmed@example.com"}'))
        with mock.patch.object(self.appmod, "send_env_email", return_value=(True, "Email sent")) as email_send:
            result = self.appmod.alert_unconfirmed_acknowledgement_deliveries()
        self.assertEqual(result[0]["status"], "Email fallback sent")
        self.assertGreaterEqual(email_send.call_count, 1)
        queued = self.appmod.q("SELECT * FROM enquiry_acknowledgement_queue WHERE lead_id=?", (lead_id,), one=True)
        self.assertEqual(queued["status"], "Email fallback sent")
        self.assertTrue(queued["fallback_sent_at"])

    def test_acknowledgement_uses_requested_spacing_signature_and_no_hyphens(self):
        message = self.appmod.enquiry_acknowledgement_text({"name": "Paul Nicholas"})
        self.assertTrue(message.startswith("Hi Paul, thank you for your enquiry."))
        self.assertIn("I've received your message and I'd be happy to help.", message)
        self.assertIn("Could you reply with a little more information about what you would like cleaned?", message)
        self.assertIn("please send me a few photos as well", message)
        self.assertIn("call me on 07802 563213 if you prefer", message)
        self.assertTrue(message.endswith("Thanks,\nPaul\nThe Carpet Cleaning Company"))
        self.assertNotIn("-", message)
        self.assertNotIn("—", message)

        unnamed_message = self.appmod.enquiry_acknowledgement_text({})
        self.assertTrue(unnamed_message.startswith("Hi, thank you for your enquiry."))

    def test_organic_shrewsbury_source_is_distinct_from_google_ads_landing_page(self):
        organic = self.appmod.website_enquiry_source_label({
            "landing_page": "organic-shrewsbury-carpet-cleaning.html",
            "landing_area": "Shrewsbury",
        })
        ads = self.appmod.website_enquiry_source_label({
            "landing_page": "landing-shrewsbury.html",
            "landing_area": "Shrewsbury",
            "gclid": "test-click-id",
        })
        self.assertEqual(organic, "Shrewsbury organic page")
        self.assertEqual(ads, "Shrewsbury landing page (Google Ads)")

    def test_postcode_location_details_returns_approximate_area_and_map(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"result": {
                    "parish": "Shrewsbury",
                    "admin_ward": "Quarry and Coton Hill",
                    "admin_district": "Shropshire",
                }}).encode("utf-8")

        self.appmod.postcode_location_details.cache_clear()
        with mock.patch.object(self.appmod.urllib.request, "urlopen", return_value=FakeResponse()):
            location = self.appmod.postcode_location_details("sy1 1aa")
        self.assertEqual(location["area"], "Shrewsbury, Quarry and Coton Hill, Shropshire")
        self.assertEqual(location["maps_url"], "https://www.google.com/maps/search/?api=1&query=SY1+1AA")

    def test_owner_alert_includes_postcode_area_and_map_link(self):
        location = {
            "area": "Ludlow, Ludlow East, Shropshire",
            "maps_url": "https://www.google.com/maps/search/?api=1&query=SY8+1AA",
        }
        with mock.patch.object(self.appmod, "postcode_location_details", return_value=location):
            message = self.appmod.owner_enquiry_alert_text({
                "name": "Test Customer",
                "postcode": "SY8 1AA",
                "landing_page": "landing-ludlow.html",
            })
            html = self.appmod.owner_enquiry_alert_html({
                "name": "Test Customer",
                "postcode": "SY8 1AA",
                "landing_page": "landing-ludlow.html",
            })
        self.assertIn("Approximate area: Ludlow, Ludlow East, Shropshire", message)
        self.assertIn("Map: https://www.google.com/maps/search/?api=1&query=SY8+1AA", message)
        self.assertIn("Open approximate location", html)
        self.assertIn("Ludlow, Ludlow East, Shropshire", html)

    def test_homepage_source_has_its_own_label(self):
        self.assertEqual(
            self.appmod.website_enquiry_source_label({"landing_page": "homepage"}),
            "Homepage",
        )

    def test_delayed_acknowledgement_uses_email_for_invalid_phone(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status) VALUES (?,?,?,?)""",
            ("Email Customer", "not-a-phone", "email@example.com", "Waiting for review"))
        self.appmod.schedule_enquiry_acknowledgement(
            lead_id, data={"name": "Email Customer", "phone": "not-a-phone", "email": "email@example.com"}, delay_minutes=-1
        )
        with (mock.patch.object(self.appmod, "customer_sms_hours_open", return_value=True),
              mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS accepted")) as sms_send,\
              mock.patch.object(self.appmod, "send_env_email", return_value=(True, "Email sent")) as email_send):
            result = self.appmod.run_due_enquiry_acknowledgements()
        self.assertEqual(result[0]["channel"], "email")
        sms_send.assert_not_called()
        email_send.assert_called_once()

    def test_outside_hours_acknowledgement_waits_for_sms_window(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, status) VALUES (?,?,?,?)""",
            ("Night Customer", "07802 563213", "night@example.com", "Waiting for review"))
        self.appmod.schedule_enquiry_acknowledgement(
            lead_id, data={"phone": "07802 563213", "email": "night@example.com"}, delay_minutes=-1
        )
        with mock.patch.object(self.appmod, "customer_sms_hours_open", return_value=False), \
             mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "SMS accepted")) as sms_send, \
             mock.patch.object(self.appmod, "send_env_email", return_value=(True, "Email sent")) as email_send:
            result = self.appmod.run_due_enquiry_acknowledgements()
        self.assertEqual(result[0]["channel"], "sms")
        self.assertEqual(result[0]["status"], "Queued")
        sms_send.assert_not_called()
        email_send.assert_not_called()

    def test_customer_sms_window_is_0930_to_1900(self):
        tz = self.appmod.ZoneInfo("Europe/London")
        self.assertFalse(self.appmod.customer_sms_hours_open(self.appmod.datetime(2026, 8, 22, 9, 29, tzinfo=tz)))
        self.assertTrue(self.appmod.customer_sms_hours_open(self.appmod.datetime(2026, 8, 22, 9, 30, tzinfo=tz)))
        self.assertTrue(self.appmod.customer_sms_hours_open(self.appmod.datetime(2026, 8, 22, 18, 59, tzinfo=tz)))
        self.assertFalse(self.appmod.customer_sms_hours_open(self.appmod.datetime(2026, 8, 22, 19, 0, tzinfo=tz)))

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

    def test_missing_details_can_be_manually_overridden_with_audit_note(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, postcode, what_cleaned, status, source, follow_up_status)
            VALUES (?,?,?,?,?,?,?,?)""",
            ("Override Customer", "07802 563213", "override@example.com", "SY8 1AA", "Carpet cleaning", "Needs missing details", "Website form", "Request missing details"))
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.post(f"/intake-forms/{lead_id}/quick-action", data={"action": "override_missing"})
        self.assertEqual(response.status_code, 302)
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
        self.assertEqual(lead["missing_details_overridden"], 1)
        self.assertEqual(lead["status"], "Ready for review")
        self.assertEqual(lead["follow_up_status"], "Follow up required")
        self.assertEqual(self.appmod.intake_missing_details(lead), [])
        note = self.appmod.q("SELECT note_text FROM customer_timeline WHERE customer_id=? ORDER BY id DESC LIMIT 1", (lead["customer_id"],), one=True)
        self.assertIn("manually overridden", note["note_text"])

    def test_sent_missing_details_request_advances_to_waiting_state(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, postcode, what_cleaned, status, follow_up_status, update_form_sent_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            ("Waiting Customer", "07802 563213", "waiting@example.com", "SY8 1AA", "Carpet cleaning", "Needs missing details", "Waiting for customer", "2026-09-04 09:30:00"))
        lead = self.appmod.q("SELECT * FROM intake_submissions WHERE id=?", (lead_id,), one=True)
        self.assertEqual(self.appmod.intake_lead_next_action(lead), "Wait for the customer to return the missing details.")
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.get(f"/intake-forms/{lead_id}")
        page = response.get_data(as_text=True)
        self.assertIn("The next normal step is to wait for the customer", page)
        self.assertIn("Continue without their reply", page)
        self.assertIn("Resend missing-details request", page)

    def test_missing_details_update_link_is_short_and_opens_prefilled_form(self):
        lead_id = self.appmod.run("""INSERT INTO intake_submissions
            (name, phone, email, postcode, what_cleaned, status)
            VALUES (?,?,?,?,?,?)""",
            ("Short Link Customer", "07802 563213", "short@example.com", "SY8 1AA", "Carpet cleaning", "Needs missing details"))
        with self.app.test_request_context("/"):
            short_url = self.appmod.intake_update_short_url(lead_id)
        self.assertIn("/u/", short_url)
        self.assertLess(len(short_url), 140)
        short_path = short_url[short_url.index("/u/"):]
        self.assertEqual(self.appmod.lead_id_from_update_token(short_path.split("/u/", 1)[1]), lead_id)
        with self.app.test_client() as client:
            redirect_response = client.get(short_path)
            self.assertEqual(redirect_response.status_code, 302, short_url + redirect_response.get_data(as_text=True))
            form_response = client.get(redirect_response.headers["Location"])
        page = form_response.get_data(as_text=True)
        self.assertIn("Short Link Customer", page)
        self.assertIn("short@example.com", page)

    def test_successful_automation_texts_owner_confirmation(self):
        rule = {"rule_key": "review_request_after_completion", "label": "Review request after completion"}
        customer = {"first_name": "Jane", "last_name": "Customer"}
        with mock.patch.object(self.appmod, "owner_contact_form_recipients", return_value=("", "+447802563213")), \
             mock.patch.object(self.appmod, "send_clicksend_env_sms", return_value=(True, "queued")) as sms_send:
            ok, message, recipient, notice = self.appmod.send_owner_automation_confirmation(rule, customer, 42, "email", "jane@example.com")
        self.assertTrue(ok)
        self.assertEqual(message, "queued")
        self.assertEqual(recipient, "+447802563213")
        self.assertIn("Review request after completion", notice)
        self.assertIn("Jane Customer", notice)
        self.assertIn("Job #42", notice)
        sms_send.assert_called_once_with("+447802563213", notice, customer=None, category="Automation Sent Confirmation")

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
