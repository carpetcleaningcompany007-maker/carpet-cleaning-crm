import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


class CarpetRefreshReminderTests(unittest.TestCase):
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
            ("Sarah", "Jones", "sarah@example.com", "07700900123", "1 High Street", "SY1 1AA"),
        )
        self.job_id = self.mod.run(
            "INSERT INTO jobs(customer_id,title,job_date,status,amount,created_at) VALUES (?,?,?,?,?,datetime('now'))",
            (self.customer_id, "Carpet cleaning", "2026-03-06", "Completed", 150),
        )

    def tearDown(self):
        self.ctx.pop()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_rule_is_single_and_off_by_default(self):
        rules = [rule for rule in self.mod.automation_settings_rows() if rule["template_key"] == "maintenance_reminder_email"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["rule_key"], "carpet_refresh_reminder")
        self.assertEqual(int(rules[0]["active"]), 0)
        self.assertEqual(rules[0]["timing_value"], "6")

    def test_renderer_is_branded_personal_and_interval_aware(self):
        customer, _job, subject, body, rendered = self.mod.carpet_refresh_preview_payload(self.customer_id, 12)
        self.assertEqual(customer["first_name"], "Sarah")
        self.assertIn("Sarah", subject)
        self.assertIn("around a year", body)
        self.assertIn("Hi Sarah", rendered)
        self.assertNotIn("Sarah Jones", rendered)
        self.assertIn("site/email-logo.png", rendered)
        self.assertIn("site/hero-carpet-cleaning.webp", rendered)
        self.assertIn("Arrange a carpet refresh", rendered)
        self.assertNotIn("facebook.com", rendered.lower())

    def test_preview_uses_exact_renderer(self):
        response = self.client.get(f"/communication-automation/carpet-refresh/preview?customer_id={self.customer_id}&interval=3")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("around three months", body)
        self.assertIn("Hi Sarah", body)

    def test_test_send_targets_owner_not_customer(self):
        self.mod.run("UPDATE settings SET test_email=? WHERE id=1", ("owner@example.com",))
        with patch.object(self.mod, "send_env_email", return_value=(True, "Sent")) as sender:
            response = self.client.post("/communication-automation/carpet-refresh/send", data={
                "send_mode": "test", "customer_id": str(self.customer_id), "interval": "6",
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(sender.call_args.args[0], "owner@example.com")
        self.assertNotEqual(sender.call_args.args[0], "sarah@example.com")

    def test_manual_send_deduplicates_customer_job_and_channel(self):
        with patch.object(self.mod, "send_env_email", return_value=(True, "Sent")) as sender, \
             patch.object(self.mod, "send_owner_customer_message_copy"):
            first = self.client.post("/communication-automation/carpet-refresh/send", data={
                "send_mode": "manual", "customer_id": str(self.customer_id), "interval": "6",
            })
            second = self.client.post("/communication-automation/carpet-refresh/send", data={
                "send_mode": "manual", "customer_id": str(self.customer_id), "interval": "6",
            })
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(sender.call_count, 1)
        log = self.mod.q("SELECT * FROM communication_automation_log WHERE rule_key='carpet_refresh_reminder'", one=True)
        self.assertEqual(log["customer_id"], self.customer_id)
        self.assertEqual(log["job_id"], self.job_id)
        self.assertEqual(log["channel"], "email")


if __name__ == "__main__":
    unittest.main()
