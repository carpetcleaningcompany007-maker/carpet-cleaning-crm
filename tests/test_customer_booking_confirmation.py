import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


class CustomerBookingConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        import app
        self.mod = importlib.reload(app)
        self.mod.app.config["TESTING"] = True
        self.ctx = self.mod.app.app_context()
        self.ctx.push()
        self.mod.init_db()
        self.client = self.mod.app.test_client()
        with self.client.session_transaction() as session:
            session["logged_in"] = True
        self.customer_id = self.mod.run(
            "INSERT INTO customers(first_name,last_name,email,phone,address,postcode) VALUES (?,?,?,?,?,?)",
            ("Bob", "Example", "bob@example.com", "07700900123", "1 High Street", "SY1 1AA"),
        )

    def tearDown(self):
        self.ctx.pop()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def booking_data(self):
        return {
            "name": "Bob Example", "email": "bob@example.com", "phone": "07700900123",
            "preferred_date": "2026-09-10", "preferred_time": "10:00",
            "job_duration_minutes": "120", "agreed_quote_price": "150",
            "confirmation_channel": "email", "confirm_and_send": "1",
        }

    def test_clash_blocks_booking_and_message(self):
        other_id = self.mod.run(
            "INSERT INTO customers(first_name,last_name) VALUES (?,?)", ("Alice", "Busy")
        )
        self.mod.run(
            "INSERT INTO jobs(customer_id,title,job_date,job_time,job_duration_minutes,status) VALUES (?,?,?,?,?,?)",
            (other_id, "Carpet cleaning", "2026-09-10", "09:30", 120, "Booked"),
        )
        with patch.object(self.mod, "send_env_email") as send_email:
            response = self.client.post(f"/customers/{self.customer_id}/save-booking-details", data=self.booking_data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.mod.q("SELECT COUNT(*) AS c FROM jobs WHERE customer_id=?", (self.customer_id,), one=True)["c"], 0)
        send_email.assert_not_called()

    def test_clear_slot_books_syncs_and_sends(self):
        with patch.object(self.mod, "google_calendar_token_row", return_value={"access_token": "test"}), \
             patch.object(self.mod, "google_calendar_list_events", return_value=[]), \
             patch.object(self.mod, "sync_job_to_google_calendar", return_value=(True, "Synced", "event-1")), \
             patch.object(self.mod, "send_env_email", return_value=(True, "Sent")) as send_email, \
             patch.object(self.mod, "send_owner_customer_message_copy"):
            response = self.client.post(f"/customers/{self.customer_id}/save-booking-details", data=self.booking_data())
        self.assertEqual(response.status_code, 302)
        job = self.mod.q("SELECT * FROM jobs WHERE customer_id=?", (self.customer_id,), one=True)
        self.assertEqual(job["job_date"], "2026-09-10")
        self.assertEqual(job["job_time"], "10:00")
        self.assertEqual(job["status"], "Booked")
        send_email.assert_called_once()


if __name__ == "__main__":
    unittest.main()
