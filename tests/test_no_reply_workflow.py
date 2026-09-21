import json
import unittest
from datetime import timedelta
from unittest.mock import patch
import test_customer_conversation as fixture


class NoReplyWorkflowTests(unittest.TestCase):
    def setUp(self):
        fixture.CustomerConversationTests.setUp(self)
        email = patch.object(self.mod,"send_env_email",return_value=(True,"Email accepted"))
        email.start()
        self.addCleanup(email.stop)
        owner = patch.object(self.mod,"owner_contact_form_recipients",return_value=("owner@example.invalid",""))
        owner.start()
        self.addCleanup(owner.stop)
    tearDown = fixture.CustomerConversationTests.tearDown

    def lead(self, ack_status="Accepted", channel="", sent_at=None, body=""):
        if sent_at is None:
            sent_at = "2026-01-01 10:00:00" if ack_status == "Accepted" else ""
        if ack_status == "Accepted" and not channel:
            channel = "sms"
        lead = self.mod.run("""INSERT INTO intake_submissions(name,phone,email,customer_id,status)
            VALUES ('Workflow customer','07700900123','test@example.invalid',?,'New')""", (self.customer,))
        self.mod.run("""INSERT INTO enquiry_acknowledgement_queue
            (lead_id,customer_id,payload_json,due_at,status,channel,sent_at,body)
            VALUES (?,?,?,datetime('now','-1 hour'),?,?,?,?)""",
            (lead,self.customer,json.dumps({"name":"Workflow customer","phone":"07700900123"}),
             ack_status,channel,sent_at,body))
        return lead

    def follow_up(self, lead, status="Queued", due="2000-01-01T10:00:00+00:00"):
        self.mod.run("""INSERT INTO enquiry_follow_up_queue
            (lead_id,customer_id,phone,body,due_at,status)
            VALUES (?,?,'07700900123','Follow-up draft',?,?)""",
            (lead,self.customer,due,status))

    def test_stage_one_shows_exact_sent_text_and_uk_time(self):
        body = "Original text <check>\nSecond line & exact spacing."
        lead = self.lead("Accepted","sms","2026-09-20 09:15:00",body)
        with patch.object(self.mod,"send_clicksend_env_sms") as sms, patch.object(self.mod,"send_env_email") as email:
            for path in (f"/intake-forms/{lead}", f"/customers/{self.customer}"):
                page = self.client.get(path)
                self.assertEqual(page.status_code,200)
                self.assertIn("Sent — awaiting delivery confirmation".encode(),page.data)
                self.assertIn(b"20 September 2026 at 10:15",page.data)
                self.assertIn(b"Original text &lt;check&gt;",page.data)
            sms.assert_not_called()
            email.assert_not_called()

    def test_overdue_approval_and_queued_are_ready_without_worker_or_send(self):
        for status in ("Queued","Awaiting approval","Ready for Paul"):
            with self.subTest(status=status):
                lead = self.lead()
                self.follow_up(lead,status)
                with patch.object(self.mod,"send_clicksend_env_sms") as sms, patch.object(self.mod,"send_env_email") as email:
                    self.assertEqual(self.mod.prepared_enquiry_follow_up_row(lead)["status"],"Ready to send")
                    page = self.client.get(f"/intake-forms/{lead}")
                    self.assertIn(b"Ready to send",page.data)
                    self.assertIn(b"Ready to send",self.client.get("/dashboard/enquiry-alerts").data)
                    sms.assert_not_called()
                    email.assert_not_called()
                stored = self.mod.q("SELECT * FROM enquiry_follow_up_queue WHERE lead_id=?",(lead,),one=True)
                self.assertEqual(stored["status"],status)
                self.assertFalse(stored["sent_at"])

    def test_worker_marks_ready_without_customer_messages_and_is_idempotent(self):
        lead = self.lead()
        self.follow_up(lead,"Awaiting approval")
        with patch.object(self.mod,"owner_contact_form_recipients",return_value=("","")), patch.object(self.mod,"send_clicksend_env_sms") as sms, patch.object(self.mod,"send_env_email") as email:
            result = self.mod.run_due_enquiry_follow_up_sms()
            self.assertEqual(result[0]["status"],"Ready to send")
            self.assertEqual(self.mod.run_due_enquiry_follow_up_sms(),[])
            sms.assert_not_called()
            email.assert_not_called()
        self.assertFalse(self.mod.q("SELECT sent_at FROM enquiry_follow_up_queue WHERE lead_id=?",(lead,),one=True)["sent_at"])

    def test_future_and_terminal_follow_ups_are_not_ready(self):
        for status,due in (("Queued","2099-01-01T10:00:00+00:00"),("Sent","2000-01-01"),("Skipped","2000-01-01")):
            lead = self.lead()
            self.follow_up(lead,status,due)
            self.assertEqual(self.mod.prepared_enquiry_follow_up_row(lead)["status"],status)

    def test_replied_and_closed_are_not_ready(self):
        lead = self.lead()
        self.follow_up(lead)
        self.mod.run("UPDATE enquiry_follow_up_queue SET created_at='2026-01-01' WHERE lead_id=?",(lead,))
        self.mod.run("INSERT INTO communications(customer_id,channel,subject,body) VALUES (?,'SMS','Inbound SMS reply','Yes please')",(self.customer,))
        self.assertEqual(self.mod.prepared_enquiry_follow_up_row(lead)["status"],"Cancelled - customer replied")
        self.mod.run("UPDATE intake_submissions SET status='Closed - no reply' WHERE id=?",(lead,))
        self.assertEqual(self.mod.prepared_enquiry_follow_up_row(lead)["status"],"Cancelled")

    def test_unsent_failed_and_email_are_truthful(self):
        for status,channel,expected in (("Queued","","Not sent"),("Failed","sms","Not sent"),("Sent","email","Text not sent")):
            lead = self.lead(status,channel)
            first = self.mod.enquiry_first_text(lead)
            self.assertIn(expected,first["status"])
            if channel == "email":
                self.assertEqual(first["body"],"")
            else:
                self.assertTrue(first["is_draft"])
                self.assertIn("Hi Workflow",first["body"])
            self.assertEqual(first["time"],"")

    def test_send_saves_original_body_despite_template_changes(self):
        lead = self.lead("Queued")
        with patch.object(self.mod,"customer_sms_hours_open",return_value=True), patch.object(self.mod,"enquiry_acknowledgement_text",return_value="Exact original text"), patch.object(self.mod,"send_clicksend_env_sms",return_value=(True,"Message ID: test123")):
            self.mod.run_due_enquiry_acknowledgements(lead_id=lead)
        with patch.object(self.mod,"enquiry_acknowledgement_text",return_value="Changed template"):
            first = self.mod.enquiry_first_text(lead)
        self.assertEqual(first["body"],"Exact original text")
        self.assertTrue(first["time"])

    def test_legacy_body_is_taken_only_from_matching_audit(self):
        lead = self.lead("Delivered","sms","2026-09-20 09:15:00")
        self.mod.run("UPDATE enquiry_acknowledgement_queue SET external_id='legacy-id' WHERE lead_id=?",(lead,))
        self.mod.log_sms_event(self.customer,None,"ClickSend","send","","","Legacy original",external_id="legacy-id")
        self.assertEqual(self.mod.enquiry_first_text(lead)["body"],"Legacy original")


    def test_inbound_sms_stops_readiness_and_worker(self):
        lead = self.lead()
        self.follow_up(lead)
        self.mod.log_sms_event(self.customer,None,"ClickSend","inbound","","","Customer replied",direction="inbound")
        self.assertEqual(self.mod.prepared_enquiry_follow_up_row(lead)["status"],"Cancelled - customer replied")
        with patch.object(self.mod,"send_clicksend_env_sms") as sms, patch.object(self.mod,"send_env_email") as email:
            self.assertEqual(self.mod.run_due_enquiry_follow_up_sms()[0]["status"],"Cancelled")
            sms.assert_not_called()
            email.assert_not_called()

    def test_due_time_compares_offsets_and_dry_run_does_not_mutate(self):
        lead = self.lead()
        self.follow_up(lead)
        now = self.mod.datetime.now(self.mod.ZoneInfo("Europe/London"))
        future = (now + timedelta(minutes=30)).astimezone(self.mod.timezone(timedelta(hours=-5))).isoformat()
        self.mod.run("UPDATE enquiry_follow_up_queue SET due_at=? WHERE lead_id=?",(future,lead))
        self.assertEqual(self.mod.run_due_enquiry_follow_up_sms(dry_run=True),[])
        self.assertEqual(self.mod.prepared_enquiry_follow_up_row(lead)["status"],"Queued")
        self.mod.run("UPDATE enquiry_follow_up_queue SET due_at='2000-01-01' WHERE lead_id=?",(lead,))
        self.assertEqual(self.mod.run_due_enquiry_follow_up_sms(dry_run=True)[0]["status"],"Ready to send")
        self.assertEqual(self.mod.q("SELECT status FROM enquiry_follow_up_queue WHERE lead_id=?",(lead,),one=True)["status"],"Queued")

    def test_only_owner_notification_is_sent_by_worker(self):
        lead = self.lead()
        self.follow_up(lead)
        with patch.object(self.mod,"owner_contact_form_recipients",return_value=("","07700900999")), patch.object(self.mod,"crm_external_url",return_value="https://crm.example.invalid/review"), patch.object(self.mod,"send_clicksend_env_sms") as sms, patch.object(self.mod,"send_env_email") as email:
            self.mod.run_due_enquiry_follow_up_sms()
            sms.assert_called_once()
            self.assertEqual(sms.call_args.args[0],"07700900999")
            self.assertIsNone(sms.call_args.kwargs["customer"])
            email.assert_not_called()

    def control(self, lead, action, when="2099-01-15T11:30"):
        return self.client.post(f"/intake-forms/{lead}/customer-message",
            data={"action":action,"follow_up_at":when})

    def queue(self, lead):
        return self.mod.q("SELECT * FROM enquiry_follow_up_queue WHERE lead_id=?",(lead,),one=True)

    def due_schedule(self, lead):
        self.control(lead,"schedule_follow_up_sms")
        self.mod.run("UPDATE enquiry_follow_up_queue SET scheduled_send_at='2000-01-01T10:00:00+00:00' WHERE lead_id=?",(lead,))

    def test_timing_controls_visible_and_schedule_never_sends_immediately(self):
        lead = self.lead()
        self.follow_up(lead)
        with patch.object(self.mod,"send_clicksend_env_sms") as sms, patch.object(self.mod,"send_env_email") as email:
            page = self.client.get(f"/intake-forms/{lead}").data
            for label in ("Send now","Send at","Pause until","Hold until I send"):
                self.assertIn(label.encode(),page)
            self.control(lead,"schedule_follow_up_sms")
            row=self.queue(lead)
            self.assertEqual(row["status"],"Scheduled")
            self.assertTrue(row["schedule_approved_at"])
            self.assertEqual(row["scheduled_send_at"],"2099-01-15T11:30:00+00:00")
            self.assertEqual(self.mod.run_due_scheduled_enquiry_texts(),[])
            sms.assert_not_called(); email.assert_not_called()

    def test_explicit_schedule_sends_saved_text_exactly_once(self):
        lead=self.lead(); self.follow_up(lead)
        self.due_schedule(lead)
        with patch.object(self.mod,"send_clicksend_env_sms",return_value=(True,"Accepted")) as sms, patch.object(self.mod,"send_env_email") as email:
            self.mod.run_due_enquiry_follow_up_sms()
            self.mod.run_due_enquiry_follow_up_sms()
            sms.assert_called_once()
            self.assertEqual(sms.call_args.args[:2],("07700900123","Follow-up draft"))
            email.assert_called_once()
            self.assertEqual(email.call_args.args[0],"owner@example.invalid")
        self.assertEqual(self.queue(lead)["status"],"Sent")
        self.assertTrue(self.queue(lead)["sent_at"])
        self.assertFalse(self.queue(lead)["schedule_approved_at"])

    def test_pause_expiry_is_ready_without_sending_and_cancels_schedule(self):
        lead=self.lead(); self.follow_up(lead); self.due_schedule(lead)
        self.control(lead,"pause_follow_up")
        self.assertEqual(self.queue(lead)["status"],"Paused")
        self.assertFalse(self.queue(lead)["schedule_approved_at"])
        self.assertEqual(self.mod.prepared_enquiry_follow_up_row(lead)["status"],"Paused")
        self.mod.run("UPDATE enquiry_follow_up_queue SET due_at='2000-01-01' WHERE lead_id=?",(lead,))
        with patch.object(self.mod,"owner_contact_form_recipients",return_value=("","")), patch.object(self.mod,"send_clicksend_env_sms") as sms, patch.object(self.mod,"send_env_email") as email:
            self.assertEqual(self.mod.prepared_enquiry_follow_up_row(lead)["status"],"Ready to send")
            self.mod.run_due_enquiry_follow_up_sms()
            sms.assert_not_called();email.assert_not_called()
        self.assertEqual(self.queue(lead)["status"],"Ready to send")

    def test_hold_cancels_schedule_and_resume_only_makes_ready(self):
        lead=self.lead();self.follow_up(lead);self.due_schedule(lead)
        self.control(lead,"hold_follow_up")
        with patch.object(self.mod,"send_clicksend_env_sms") as sms:
            self.mod.run_due_enquiry_follow_up_sms()
            self.assertEqual(self.queue(lead)["status"],"Held")
            self.assertFalse(self.queue(lead)["scheduled_send_at"])
            self.control(lead,"resume_follow_up")
            self.mod.run_due_enquiry_follow_up_sms()
            self.assertEqual(self.queue(lead)["status"],"Ready to send")
            sms.assert_not_called()

    def test_manual_send_cancels_schedule_and_cannot_repeat(self):
        lead=self.lead();self.follow_up(lead);self.due_schedule(lead)
        with patch.object(self.mod,"send_clicksend_env_sms",return_value=(True,"Accepted")) as sms, patch.object(self.mod,"send_owner_customer_message_copy"):
            self.control(lead,"send_follow_up_sms")
            self.control(lead,"send_follow_up_sms")
            self.mod.run_due_enquiry_follow_up_sms()
            sms.assert_called_once()
        self.assertEqual(self.queue(lead)["status"],"Sent")
        self.assertFalse(self.queue(lead)["schedule_approved_at"])

    def test_reply_or_closed_enquiry_cancels_scheduled_send(self):
        for closed in (True,False):
            lead=self.lead(); self.follow_up(lead); self.due_schedule(lead)
            if closed:
                self.mod.run("UPDATE intake_submissions SET status='Closed' WHERE id=?",(lead,))
            else:
                self.mod.log_sms_event(self.customer,None,"ClickSend","inbound","","","Yes",direction="inbound")
            with patch.object(self.mod,"send_clicksend_env_sms") as sms:
                self.mod.run_due_scheduled_enquiry_texts()
                sms.assert_not_called()
            self.assertTrue(self.queue(lead)["status"].startswith("Cancelled"))

    def test_invalid_schedule_keeps_hold(self):
        lead=self.lead();self.follow_up(lead);self.control(lead,"hold_follow_up")
        for when in ("","invalid","2000-01-01T10:00","2099-01-01T10:00+01:00"):
            self.control(lead,"schedule_follow_up_sms",when)
            self.assertEqual(self.queue(lead)["status"],"Held")
        self.assertFalse(self.queue(lead)["schedule_approved_at"])

    def test_failed_scheduled_send_is_not_retried(self):
        lead=self.lead();self.follow_up(lead);self.due_schedule(lead)
        with patch.object(self.mod,"send_clicksend_env_sms",side_effect=TimeoutError) as sms:
            self.mod.run_due_scheduled_enquiry_texts()
            self.mod.run_due_scheduled_enquiry_texts()
            sms.assert_called_once()
        self.assertEqual(self.queue(lead)["status"],"Failed")
        self.assertFalse(self.queue(lead)["schedule_approved_at"])

    def test_unapproved_scheduled_status_cannot_send_and_dry_run_is_read_only(self):
        lead=self.lead();self.follow_up(lead,"Scheduled")
        self.mod.run("UPDATE enquiry_follow_up_queue SET scheduled_send_at='2000-01-01' WHERE lead_id=?",(lead,))
        with patch.object(self.mod,"send_clicksend_env_sms") as sms:
            self.assertEqual(self.mod.run_due_scheduled_enquiry_texts(),[])
            self.due_schedule(lead)
            self.mod.run_due_scheduled_enquiry_texts(dry_run=True)
            sms.assert_not_called()
        self.assertEqual(self.queue(lead)["status"],"Scheduled")

    def test_stale_worker_cannot_send_after_hold_or_reschedule(self):
        lead=self.lead();self.follow_up(lead);self.due_schedule(lead)
        stale=self.queue(lead)
        self.control(lead,"hold_follow_up")
        self.assertFalse(self.mod.claim_enquiry_follow_up(stale))
        self.due_schedule(lead)
        stale=self.queue(lead)
        self.control(lead,"schedule_follow_up_sms","2099-02-15T11:30")
        self.assertFalse(self.mod.claim_enquiry_follow_up(stale))

    def test_sms_opt_out_before_due_prevents_scheduled_send(self):
        lead=self.lead();self.follow_up(lead);self.due_schedule(lead)
        with patch.object(self.mod,"is_customer_sms_opted_out",return_value=True), patch.object(self.mod,"send_clicksend_env_sms") as sms:
            self.mod.run_due_scheduled_enquiry_texts()
            sms.assert_not_called()
        self.assertEqual(self.queue(lead)["status"],"Failed")

    def test_paused_and_held_dashboard_do_not_claim_ready(self):
        lead=self.lead();self.follow_up(lead)
        for action,expected in (("hold_follow_up","Hold until I send"),("pause_follow_up","Paused until"),("schedule_follow_up_sms","Text scheduled to send at")):
            self.control(lead,action)
            with self.mod.app.test_request_context("/dashboard"):
                journey=self.mod.dashboard_enquiry_journey()
            self.assertIn(expected,journey["detail"])
            self.assertIn(expected.encode(),self.client.get("/dashboard/enquiry-alerts").data)

    def edit(self, lead, action, body):
        return self.client.post(f"/intake-forms/{lead}/customer-message",
            data={"action":action,"body":body,"default_body":body})

    def test_customer_only_edit_preserves_other_drafts_and_default(self):
        lead=self.lead();self.follow_up(lead)
        other=self.lead();self.follow_up(other)
        original=self.mod.enquiry_follow_up_default_template()
        body="Hi Jane,\nOnly your lounge.  Thanks!"
        with patch.object(self.mod,"send_clicksend_env_sms") as sms, patch.object(self.mod,"send_env_email") as email:
            self.edit(lead,"save_follow_up_text",body)
            sms.assert_not_called();email.assert_not_called()
        self.assertEqual(self.queue(lead)["body"],body)
        self.assertEqual(self.queue(other)["body"],"Follow-up draft")
        self.assertEqual(self.mod.enquiry_follow_up_default_template(),original)

    def test_permanent_edit_personalises_future_drafts_only(self):
        lead=self.lead();self.follow_up(lead);self.due_schedule(lead)
        body="Hi {{first_name}}, please let me know. Thanks, Paul."
        self.edit(lead,"save_follow_up_default",body)
        self.assertEqual(self.mod.enquiry_follow_up_sms_text({"name":"Jane Smith"}),"Hi Jane, please let me know. Thanks, Paul.")
        self.assertEqual(self.mod.enquiry_follow_up_sms_text({"name":"John Brown"}),"Hi John, please let me know. Thanks, Paul.")
        self.assertEqual(self.queue(lead)["body"],"Follow-up draft")
        self.assertEqual(self.queue(lead)["status"],"Scheduled")
        self.control(lead,"use_follow_up_default")
        self.assertEqual(self.queue(lead)["body"],"Hi Workflow, please let me know. Thanks, Paul.")
        self.assertEqual(self.queue(lead)["status"],"Held")

    def test_editing_scheduled_text_cancels_approval_and_invalidates_worker(self):
        lead=self.lead();self.follow_up(lead);self.due_schedule(lead)
        stale=self.queue(lead)
        self.edit(lead,"save_follow_up_text","New wording")
        self.assertEqual(self.queue(lead)["status"],"Held")
        self.assertFalse(self.queue(lead)["schedule_approved_at"])
        self.assertFalse(self.mod.claim_enquiry_follow_up(stale))
        with patch.object(self.mod,"send_clicksend_env_sms") as sms:
            self.mod.run_due_scheduled_enquiry_texts()
            sms.assert_not_called()

    def test_sent_message_locked_visible_and_confirmation_is_email_only(self):
        lead=self.lead();self.follow_up(lead)
        self.edit(lead,"save_follow_up_text","Exact customised text")
        with patch.object(self.mod,"send_clicksend_env_sms",return_value=(True,"Accepted")) as sms, patch.object(self.mod,"send_env_email",return_value=(True,"Sent")) as email:
            self.control(lead,"send_follow_up_sms")
            self.control(lead,"send_follow_up_sms")
            self.mod.email_follow_up_sent_confirmation(lead,"07700900123")
            sms.assert_called_once();email.assert_called_once()
            self.assertEqual(email.call_args.args[0],"owner@example.invalid")
            self.assertIn("Workflow customer",email.call_args.args[1])
            self.assertIn("Exact customised text",email.call_args.args[2])
            self.assertIn("Sent:",email.call_args.args[2])
            self.assertFalse(email.call_args.kwargs["record_customer_event"])
        self.edit(lead,"save_follow_up_text","Do not change sent history")
        self.assertEqual(self.queue(lead)["body"],"Exact customised text")
        page=self.client.get(f"/intake-forms/{lead}").data
        self.assertIn(b"Already sent",page)
        self.assertIn(b"Confirmation email sent",page)
        self.assertNotIn(b'value="send_follow_up_sms"',page)
        self.assertNotIn(b'id="follow-up-body"',page)

    def test_failed_owner_email_does_not_unlock_or_resend_customer_text(self):
        lead=self.lead();self.follow_up(lead);self.due_schedule(lead)
        with patch.object(self.mod,"send_clicksend_env_sms",return_value=(True,"Accepted")) as sms, patch.object(self.mod,"send_env_email",side_effect=TimeoutError):
            self.mod.run_due_scheduled_enquiry_texts()
            self.control(lead,"send_follow_up_sms")
            self.mod.run_due_scheduled_enquiry_texts()
            sms.assert_called_once()
        self.assertEqual(self.queue(lead)["status"],"Sent")
        self.assertTrue(self.queue(lead)["owner_confirmation_status"].startswith("Confirmation email not sent"))

    def test_failed_customer_text_has_no_success_confirmation(self):
        lead=self.lead();self.follow_up(lead)
        with patch.object(self.mod,"send_clicksend_env_sms",return_value=(False,"Rejected")), patch.object(self.mod,"send_env_email") as email:
            self.control(lead,"send_follow_up_sms")
            email.assert_not_called()
        self.assertEqual(self.queue(lead)["status"],"Failed")

    def test_no_queue_manual_send_still_has_duplicate_lock(self):
        lead=self.lead()
        with patch.object(self.mod,"send_clicksend_env_sms",return_value=(True,"Accepted")) as sms:
            self.control(lead,"send_follow_up_sms")
            self.control(lead,"send_follow_up_sms")
            sms.assert_called_once()
        self.assertEqual(self.queue(lead)["status"],"Sent")

    def test_stale_preview_cannot_send_changed_message(self):
        lead=self.lead();self.follow_up(lead)
        self.edit(lead,"save_follow_up_text","New wording")
        with patch.object(self.mod,"send_clicksend_env_sms") as sms:
            response=self.client.post(f"/intake-forms/{lead}/customer-message",data={"action":"send_follow_up_sms","reviewed_body":"Follow-up draft"})
            sms.assert_not_called()
        self.assertEqual(response.status_code,302)

    def test_empty_edits_and_unknown_default_placeholders_are_rejected(self):
        lead=self.lead();self.follow_up(lead)
        self.edit(lead,"save_follow_up_text","  ")
        self.assertEqual(self.queue(lead)["body"],"Follow-up draft")
        original=self.mod.enquiry_follow_up_default_template()
        self.edit(lead,"save_follow_up_default","Hello {{unsupported}}")
        self.assertEqual(self.mod.enquiry_follow_up_default_template(),original)

    def test_editor_script_is_outside_title_and_text_is_escaped(self):
        lead=self.lead();self.follow_up(lead)
        self.edit(lead,"save_follow_up_text","Hello <script>unsafe()</script>")
        page=self.client.get(f"/intake-forms/{lead}").data.decode()
        self.assertNotIn("follow-up-editor.js",page.split("</title>")[0])
        self.assertIn("follow-up-editor.js",page)
        self.assertIn("&lt;script&gt;unsafe()&lt;/script&gt;",page)

    def test_pending_first_message_displays_full_draft_without_sending(self):
        lead=self.lead("Awaiting approval")
        with patch.object(self.mod,"send_clicksend_env_sms") as sms:
            first=self.mod.enquiry_first_text(lead)
            self.assertTrue(first["is_draft"])
            self.assertIn("Hi Workflow",first["body"])
            page=self.client.get(f"/intake-forms/{lead}").data
            self.assertIn(b"Draft",page)
            self.assertIn(b"Send now",page)
            self.assertIn(b'data-enquiry-step="1"',page)
            self.assertIn(b'data-enquiry-step="2" id="enquiry-step-2" hidden',page)
            sms.assert_not_called()

    def test_approve_pending_first_text_sends_reviewed_draft_once(self):
        lead=self.lead("Awaiting approval")
        body=self.mod.enquiry_first_text(lead)["body"]
        with patch.object(self.mod,"customer_sms_hours_open",return_value=True),patch.object(self.mod,"send_clicksend_env_sms",return_value=(True,"Message ID: first-one")) as sms:
            self.client.post(f"/intake-forms/{lead}/first-text",data={"reviewed_body":body})
            self.client.post(f"/intake-forms/{lead}/first-text",data={"reviewed_body":body})
            sms.assert_called_once()
            self.assertEqual(sms.call_args.args[1],body)
        self.assertEqual(self.mod.enquiry_first_text(lead)["body"],body)

    def test_new_first_draft_is_saved_before_send_and_survives_template_change(self):
        lead=self.mod.run("INSERT INTO intake_submissions(name,phone,customer_id,status) VALUES ('Chris','07700900123',?,'New')",(self.customer,))
        with patch.object(self.mod.threading,"Timer"),patch.object(self.mod,"enquiry_acknowledgement_text",return_value="Original first draft"):
            self.mod.schedule_enquiry_acknowledgement(lead,self.customer,{"name":"Chris","phone":"07700900123"})
        with patch.object(self.mod,"enquiry_acknowledgement_text",return_value="Changed template"):
            self.assertEqual(self.mod.enquiry_first_text(lead)["body"],"Original first draft")

    def first_control(self, lead, action, **fields):
        return self.client.post(f"/intake-forms/{lead}/first-text",data={"action":action,**fields})

    def test_first_message_uses_saved_template_not_carpet_options(self):
        self.mod.run("UPDATE message_templates SET body=? WHERE template_key='website_enquiry_acknowledgement_sms'",("Hi {{first_name}}, thank you. Please send a photo. Paul",))
        body=self.mod.enquiry_acknowledgement_text({"name":"Chris","what_cleaned":"Upholstery cleaning"})
        self.assertEqual(body,"Hi Chris, thank you. Please send a photo. Paul")
        self.assertNotIn("different options",body)
        self.assertNotIn("cheapest",body)

    def test_first_text_edit_hold_pause_and_schedule(self):
        lead=self.lead("Awaiting approval")
        body=self.mod.enquiry_first_text(lead)["body"]
        self.first_control(lead,"schedule",reviewed_body=body,first_text_at="2099-01-15T11:30")
        self.assertEqual(self.mod.q("SELECT status FROM enquiry_acknowledgement_queue WHERE lead_id=?",(lead,),one=True)["status"],"Queued")
        self.first_control(lead,"save_text",body="Hi Chris, this is your edited text.")
        self.assertEqual(self.mod.enquiry_first_text(lead)["body"],"Hi Chris, this is your edited text.")
        self.assertIn("hold",self.mod.enquiry_first_text(lead)["status"].lower())
        self.first_control(lead,"pause",first_text_at="2099-01-15T11:30")
        self.mod.run("UPDATE enquiry_acknowledgement_queue SET due_at='2000-01-01' WHERE lead_id=?",(lead,))
        with patch.object(self.mod,"send_clicksend_env_sms") as sms:
            self.mod.run_due_enquiry_acknowledgements()
            sms.assert_not_called()
        self.assertEqual(self.mod.q("SELECT status FROM enquiry_acknowledgement_queue WHERE lead_id=?",(lead,),one=True)["status"],"Awaiting approval")

    def test_no_follow_up_send_or_schedule_before_first_message(self):
        lead=self.lead("Awaiting approval");self.follow_up(lead)
        with patch.object(self.mod,"send_clicksend_env_sms") as sms:
            for action in ("send_follow_up_sms","send_follow_up_email","send_follow_up_both","schedule_follow_up_sms"):
                response=self.control(lead,action)
                self.assertNotIn("#customer-message-approval",response.location)
            sms.assert_not_called()
        self.assertFalse(self.mod.enquiry_first_contact_sent(lead))
        self.assertEqual(self.queue(lead)["status"],"Queued")

    def test_old_scheduled_follow_up_is_held_if_first_text_not_sent(self):
        lead=self.lead("Awaiting approval");self.follow_up(lead,"Scheduled")
        self.mod.run("UPDATE enquiry_follow_up_queue SET schedule_approved_at=datetime('now'),scheduled_send_at='2000-01-01' WHERE lead_id=?",(lead,))
        with patch.object(self.mod,"send_clicksend_env_sms") as sms:
            self.mod.run_due_scheduled_enquiry_texts()
            sms.assert_not_called()
        self.assertEqual(self.queue(lead)["status"],"Held")

    def test_first_text_confirmation_is_email_only_and_once(self):
        lead=self.lead("Awaiting approval")
        body=self.mod.enquiry_first_text(lead)["body"]
        with patch.object(self.mod,"customer_sms_hours_open",return_value=True),patch.object(self.mod,"send_clicksend_env_sms",return_value=(True,"Message ID: ack-email")) as sms,patch.object(self.mod,"send_env_email",return_value=(True,"Email sent")) as email:
            self.first_control(lead,"send_now",reviewed_body=body)
            self.first_control(lead,"send_now",reviewed_body=body)
            sms.assert_called_once();email.assert_called_once()
            self.assertEqual(email.call_args.args[0],"owner@example.invalid")
            self.assertIn(body,email.call_args.args[2])
        self.assertEqual(self.mod.enquiry_first_text(lead)["confirmation"],"Confirmation email sent")

    def test_first_delivery_receipt_does_not_send_owner_sms(self):
        lead=self.lead(body="Exact first text")
        self.mod.run("UPDATE enquiry_acknowledgement_queue SET external_id='first-receipt' WHERE lead_id=?",(lead,))
        with patch.object(self.mod,"send_clicksend_env_sms") as sms,patch.object(self.mod,"send_env_email",return_value=(True,"Sent")) as email:
            self.mod.process_acknowledgement_delivery_receipt("first-receipt","DELIVERED")
            self.mod.process_acknowledgement_delivery_receipt("first-receipt","DELIVERED")
            sms.assert_not_called()
            email.assert_called_once()

    def test_schedule_summary_only_displays_an_authorised_send(self):
        lead=self.lead("Awaiting approval")
        self.assertEqual(self.mod.enquiry_first_text(lead)["scheduled_time"],"")
        body=self.mod.enquiry_first_text(lead)["body"]
        self.first_control(lead,"schedule",reviewed_body=body,first_text_at="2099-01-15T11:30")
        self.assertIn("11:30",self.mod.enquiry_first_text(lead)["scheduled_time"])
        self.first_control(lead,"hold")
        self.assertEqual(self.mod.enquiry_first_text(lead)["scheduled_time"],"")

    def test_default_stage_tracks_confirmed_sends(self):
        lead=self.lead("Awaiting approval");self.follow_up(lead)
        def page():return self.client.get(f"/intake-forms/{lead}").get_data(as_text=True)
        self.assertIn('data-current-step="1"',page())
        self.mod.run("UPDATE enquiry_acknowledgement_queue SET status='Accepted',sent_at='2026-01-01 10:00:00' WHERE lead_id=?",(lead,))
        self.assertIn('data-current-step="2"',page())
        self.mod.run("UPDATE enquiry_follow_up_queue SET status='Sent' WHERE lead_id=?",(lead,))
        self.assertIn('data-current-step="3"',page())
