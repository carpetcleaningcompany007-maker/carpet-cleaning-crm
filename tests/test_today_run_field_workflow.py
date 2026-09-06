import importlib
import io
import os
import tempfile
import unittest
from unittest import mock


class TodayRunFieldWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.uploads = tempfile.TemporaryDirectory()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["CRM_UPLOAD_FOLDER"] = self.uploads.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        os.environ["CRM_SECRET_KEY"] = "today-run-test-secret"
        import app
        self.mod = importlib.reload(app)
        self.app = self.mod.app
        self.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.mod.init_db()
        self.customer_id = self.mod.run(
            "INSERT INTO customers(first_name,last_name,phone,email,address,postcode) VALUES (?,?,?,?,?,?)",
            ("Field", "Customer", "07123456789", "field@example.com", "1 High Street", "SY1 1AA"),
        )
        self.late_id = self.mod.run(
            "INSERT INTO jobs(customer_id,title,job_date,job_time,status,notes) VALUES (?,?,?,?,?,?)",
            (self.customer_id, "Late clean", "2026-09-06", "14:00", "Booked", "Use rear access"),
        )
        self.early_id = self.mod.run(
            "INSERT INTO jobs(customer_id,title,job_date,job_time,status) VALUES (?,?,?,?,?)",
            (self.customer_id, "Early clean", "2026-09-06", "09:00", "Booked"),
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state["logged_in"] = True

    def tearDown(self):
        self.ctx.pop()
        self.uploads.cleanup()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_day_is_ordered_by_time_and_has_field_actions(self):
        response = self.client.get("/today-run?date=2026-09-06")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertLess(body.index("Early clean"), body.index("Late clean"))
        self.assertIn("On my way", body)
        self.assertIn("Arrived", body)
        self.assertIn("Complete job with details", body)
        self.assertIn("sms:07123456789", body)

    def test_status_actions_preserve_a_timestamped_audit_trail(self):
        for action, expected in (("on_way", "On My Way"), ("arrive", "Arrived"), ("start", "In Progress")):
            response = self.client.post(f"/today-run/job/{self.early_id}/action", data={"action": action})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.mod.q("SELECT status FROM jobs WHERE id=?", (self.early_id,), one=True)["status"], expected)
        events = self.mod.q("SELECT * FROM job_status_events WHERE job_id=? ORDER BY id", (self.early_id,))
        self.assertEqual([row["previous_status"] for row in events], ["Booked", "On My Way", "Arrived"])
        self.assertTrue(all(row["created_at"] for row in events))

    def test_completion_requires_real_details_and_does_not_send_messages(self):
        with mock.patch.object(self.mod, "send_env_email", side_effect=AssertionError("must not send")), mock.patch.object(
            self.mod, "send_clicksend_env_sms", side_effect=AssertionError("must not send")
        ):
            rejected = self.client.post(f"/today-run/job/{self.early_id}/complete", data={"outcome": "Completed as planned"})
            self.assertEqual(rejected.status_code, 302)
            self.assertIsNone(self.mod.q("SELECT * FROM job_completions WHERE job_id=?", (self.early_id,), one=True))
            response = self.client.post(
                f"/today-run/job/{self.early_id}/complete",
                data={
                    "work_carried_out": "Cleaned lounge and stairs",
                    "outcome": "Completed as planned",
                    "completion_notes": "Customer inspected the work",
                    "next_action": "Create invoice",
                    "before_photo": (io.BytesIO(b"small-image"), "before.jpg"),
                    "after_photo": (io.BytesIO(b"small-image"), "after.png"),
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 302)
        completion = self.mod.q("SELECT * FROM job_completions WHERE job_id=?", (self.early_id,), one=True)
        self.assertEqual(completion["outcome"], "Completed as planned")
        self.assertTrue(os.path.exists(os.path.join(self.uploads.name, completion["before_photo"])))
        self.assertEqual(self.mod.q("SELECT status FROM jobs WHERE id=?", (self.early_id,), one=True)["status"], "Completed")

    def test_reopen_is_explicit_and_audited(self):
        self.mod.run("UPDATE jobs SET status='Completed' WHERE id=?", (self.early_id,))
        self.client.post(f"/today-run/job/{self.early_id}/action", data={"action": "reopen", "note": "Completion needs review"})
        job = self.mod.q("SELECT status FROM jobs WHERE id=?", (self.early_id,), one=True)
        event = self.mod.q("SELECT * FROM job_status_events WHERE job_id=? ORDER BY id DESC", (self.early_id,), one=True)
        self.assertEqual(job["status"], "Booked")
        self.assertEqual(event["previous_status"], "Completed")
        self.assertIn("needs review", event["note"])

    def test_invalid_photo_is_rejected_without_completion(self):
        response = self.client.post(
            f"/today-run/job/{self.early_id}/complete",
            data={
                "work_carried_out": "Cleaned",
                "outcome": "Completed as planned",
                "before_photo": (io.BytesIO(b"not allowed"), "before.exe"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.mod.q("SELECT * FROM job_completions WHERE job_id=?", (self.early_id,), one=True))


if __name__ == "__main__":
    unittest.main()
