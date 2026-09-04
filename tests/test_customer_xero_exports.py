import csv
import importlib
import io
import os
import tempfile
import unittest
from unittest import mock


class CustomerXeroExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        import app
        self.mod = importlib.reload(app)
        self.app = self.mod.app
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.mod.init_db()
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state["logged_in"] = True
            state["csrf_token"] = "test-csrf"
        self.first_id = self.mod.run("""INSERT INTO customers(first_name,last_name,phone,email,address,town,postcode,source,notes)
                                       VALUES (?,?,?,?,?,?,?,?,?)""", ("Alice", "Jones", "07802563213", "alice@real.test", "1 High Street", "Ludlow", "SY8 1AA", "Website", "Private note"))
        self.second_id = self.mod.run("""INSERT INTO customers(first_name,last_name,email,address,postcode)
                                        VALUES (?,?,?,?,?)""", ("Bob", "Smith", "bob@real.test", "2 Broad Street", "SY8 2BB"))

    def tearDown(self):
        self.ctx.pop()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_individual_export_contains_only_selected_customer(self):
        response = self.client.get(f"/customers/{self.first_id}/export.csv")
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        self.assertEqual(len(rows), 2)
        self.assertIn("Alice", rows[1])
        self.assertNotIn("Bob", response.get_data(as_text=True))
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_bulk_export_contains_all_customers(self):
        response = self.client.get("/exports/customers.csv")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Alice", body)
        self.assertIn("Bob", body)

    def test_manual_xero_action_is_post_only_and_reports_safe_match(self):
        self.assertEqual(self.client.get(f"/xero/sync-contact/{self.first_id}").status_code, 405)
        with mock.patch.object(self.mod, "ensure_xero_contact_for_customer", return_value={
            "contact_id": "xero-123", "action": "updated", "match_reason": "exact email"
        }) as sync:
            response = self.client.post(f"/xero/sync-contact/{self.first_id}", data={"_csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no duplicate was created", response.get_data(as_text=True))
        sync.assert_called_once_with(self.first_id, return_outcome=True, allow_incomplete=False)

    def test_verified_xero_link_is_shown_as_completed_with_reference_and_time(self):
        self.mod.run("UPDATE customers SET xero_contact_id=?, xero_contact_synced_at=? WHERE id=?", ("xero-safe-reference", "2026-09-04 10:30:00", self.first_id))
        response = self.client.get(f"/customers/{self.first_id}")
        body = response.get_data(as_text=True)
        self.assertIn("Synced with Xero", body)
        self.assertIn("xero-safe-reference", body)
        self.assertIn("Last synced 4 September 2026 at 11:30", body)
        self.assertIn("Technical details", body)
        self.assertIn("This step is complete", body)

    def test_customer_details_are_staged_and_save_through_existing_route(self):
        response = self.client.get(f"/customers/{self.first_id}")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(page.count('data-customer-stage="'), 4)
        self.assertIn("Customer details, one clear stage at a time", page)
        self.assertIn("Jobs and sales", page)
        self.assertIn("Messages", page)
        for field_name in ("first_name", "last_name", "phone", "email", "address", "town", "postcode", "source", "tags", "notes"):
            self.assertIn(f'name="{field_name}"', page)

        saved = self.client.post(f"/customers/{self.first_id}/edit", data={
            "_csrf_token": "test-csrf", "first_name": "Alice", "last_name": "Jones",
            "phone": "07802563213", "email": "alice.updated@real.test", "address": "3 New Street",
            "town": "Ludlow", "postcode": "SY8 1BB", "source": "Website", "tags": "repeat", "notes": "Updated note",
        }, follow_redirects=True)
        self.assertEqual(saved.status_code, 200)
        self.assertIn("Customer details saved", saved.get_data(as_text=True))
        customer = self.mod.q("SELECT * FROM customers WHERE id=?", (self.first_id,), one=True)
        self.assertEqual(customer["email"], "alice.updated@real.test")
        self.assertEqual(customer["address"], "3 New Street")

    def test_customer_page_defaults_to_one_current_workflow_and_collapses_long_sections(self):
        response = self.client.get(f"/customers/{self.first_id}")
        page = response.get_data(as_text=True)
        self.assertIn('id="customer-stage-overview" open', page)
        self.assertIn('id="customer-stage-1" open', page)
        self.assertNotIn('id="customer-stage-2" open', page)
        self.assertIn('<details class="panel customer-section customer-details-workflow customer-page-accordion" id="customer-details">', page)
        self.assertIn('<details class="hub-collapse extra-customer-tools" id="customer-message-actions">', page)
        self.assertIn('<details class="hub-collapse customer-library-record" id="customer-library-record">', page)
        self.assertEqual(page.count('class="workflow-subaccordion"'), 3)
        self.assertIn("customer-library-accordion", page)
        self.assertIn("if(other!==stage)other.open=false", page)

    def test_due_next_summary_uses_current_stage_and_real_job_time(self):
        hub = {"stages": [
            {"number": 1, "state": "done", "complete": True, "missing": []},
            {"number": 3, "state": "current", "complete": False, "missing": ["Booking confirmation sent"], "subtitle": "Confirm booking"},
        ]}
        result = self.mod.customer_due_next_summary(hub, latest_job={"job_date": "2026-09-08", "job_time": "14:30"})
        self.assertTrue(result["has_action"])
        self.assertEqual(result["title"], "Confirm the quote and booking")
        self.assertEqual(result["due"], "8 September 2026 at 14:30")
        self.assertEqual(result["href"], "#customer-stage-3")
        self.assertIn("Booking confirmation sent", result["reason"])

    def test_due_next_summary_never_invents_work_when_every_stage_is_complete(self):
        hub = {"stages": [{"number": number, "state": "done", "complete": True, "missing": []} for number in range(1, 7)]}
        result = self.mod.customer_due_next_summary(hub)
        self.assertFalse(result["has_action"])
        self.assertEqual(result["title"], "Nothing due right now")
        self.assertEqual(result["due"], "No outstanding date or time")

    def test_due_next_summary_uses_open_follow_up_reminder(self):
        hub = {"stages": [{"number": 6, "state": "current", "complete": False, "missing": ["Maintenance reminder sent"], "subtitle": "Follow up"}]}
        result = self.mod.customer_due_next_summary(hub, reminders=[{"status": "Open", "title": "Annual clean reminder", "reminder_date": "2026-10-12", "notes": "Customer is due for a yearly clean."}])
        self.assertEqual(result["title"], "Annual clean reminder")
        self.assertEqual(result["due"], "12 October 2026")
        self.assertEqual(result["reason"], "Customer is due for a yearly clean.")

    def test_customer_page_renders_compact_due_next_action(self):
        response = self.client.get(f"/customers/{self.first_id}")
        page = response.get_data(as_text=True)
        self.assertIn('class="customer-due-next', page)
        self.assertIn("Due next", page)
        self.assertIn("Open next action", page)
        self.assertIn('href="#customer-stage-1"', page)

    def test_complete_genuine_intake_syncs_but_incomplete_and_test_data_do_not(self):
        genuine = {"name": "Alice Jones", "email": "alice@real.test", "phone": "07802563213", "address": "1 High Street", "postcode": "SY8 1AA"}
        lead_id, customer_id = self.mod.create_intake_from_website_payload(genuine, require_valid_phone=True)
        with mock.patch.object(self.mod, "sync_xero_contact_for_intake", return_value="xero-new") as sync:
            result = self.mod.attempt_automatic_xero_sync(lead_id, customer_id, genuine)
        self.assertTrue(result[0])
        sync.assert_called_once_with(lead_id)

        incomplete = {"name": "Real Person", "email": "person@real.test", "postcode": "SY8 1AA"}
        lead_id, customer_id = self.mod.create_intake_from_website_payload(incomplete)
        with mock.patch.object(self.mod, "sync_xero_contact_for_intake") as sync:
            result = self.mod.attempt_automatic_xero_sync(lead_id, customer_id, incomplete)
        self.assertFalse(result[0])
        self.assertIn("missing address", result[1])
        self.assertIn("Needs your confirmation", result[1])
        sync.assert_not_called()

        test_data = {"name": "Test Customer", "email": "person@example.com", "address": "1 High Street", "postcode": "SY8 1AA"}
        lead_id, customer_id = self.mod.create_intake_from_website_payload(test_data)
        with mock.patch.object(self.mod, "sync_xero_contact_for_intake") as sync:
            result = self.mod.attempt_automatic_xero_sync(lead_id, customer_id, test_data)
        self.assertFalse(result[0])
        sync.assert_not_called()

    def test_operator_can_explicitly_continue_without_address_and_action_is_audited(self):
        self.mod.run("UPDATE customers SET address='', postcode='' WHERE id=?", (self.first_id,))
        with mock.patch.object(self.mod, "find_xero_contact_match_for_customer", return_value={"contact": None, "reason": "No exact match found"}), \
             mock.patch.object(self.mod, "xero_api_request", return_value={"Contacts": [{"ContactID": "xero-partial-safe"}]}):
            outcome = self.mod.ensure_xero_contact_for_customer(self.first_id, return_outcome=True, allow_incomplete=True)
        self.assertEqual(outcome["contact_id"], "xero-partial-safe")
        self.assertEqual(outcome["bypassed_missing"], ["address", "postcode"])
        audit = self.mod.q("SELECT message FROM xero_sync_log WHERE local_id=? ORDER BY id DESC", (self.first_id,), one=True)
        self.assertIn("operator explicitly continued with missing fields: address, postcode", audit["message"])

    def test_continue_anyway_never_bypasses_identity_or_contact_requirement(self):
        unsafe_id = self.mod.run("INSERT INTO customers(first_name,last_name,address,postcode) VALUES (?,?,?,?)", ("", "", "1 High Street", "SY8 1AA"))
        with self.assertRaisesRegex(RuntimeError, "name, phone or email"):
            self.mod.ensure_xero_contact_for_customer(unsafe_id, allow_incomplete=True)

    def test_manual_continue_flag_is_forwarded_and_visible_in_timeline(self):
        with mock.patch.object(self.mod, "ensure_xero_contact_for_customer", return_value={
            "contact_id": "xero-confirmed", "action": "created", "match_reason": "No exact match found", "bypassed_missing": ["address"]
        }) as sync:
            response = self.client.post(
                f"/xero/sync-contact/{self.first_id}",
                data={"_csrf_token": "test-csrf", "allow_incomplete": "1"},
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        sync.assert_called_once_with(self.first_id, return_outcome=True, allow_incomplete=True)
        note = self.mod.q("SELECT note_text FROM customer_timeline WHERE customer_id=? ORDER BY id DESC", (self.first_id,), one=True)
        self.assertIn("explicit confirmation", note["note_text"])


if __name__ == "__main__":
    unittest.main()
