import importlib
import os
import tempfile
import unittest
from datetime import date
from unittest import mock


class BusinessGoalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        os.environ["CRM_SECRET_KEY"] = "goals-test-secret"
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

    def tearDown(self):
        self.ctx.pop()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_default_target_and_editable_settings(self):
        data = self.mod.business_goals_data(today=date(2026, 9, 4))
        self.assertEqual(data["daily_target"], 300)
        response = self.client.post("/business-goals/settings", data={
            "daily_revenue_target": "450", "working_days_per_week": "6",
        })
        self.assertEqual(response.status_code, 302)
        data = self.mod.business_goals_data(today=date(2026, 9, 4))
        self.assertEqual(data["daily_target"], 450)
        self.assertEqual(data["working_days"], 6)

    def test_goal_calculations_separate_booked_invoiced_and_paid(self):
        self.mod.run("INSERT INTO jobs(job_date,status,amount) VALUES ('2026-09-04','Booked',500)")
        self.mod.run("""INSERT INTO invoices(invoice_date,status,total,xero_amount_paid,xero_amount_due)
                        VALUES ('2026-09-04','Paid',360,360,0)""")
        data = self.mod.business_goals_data(today=date(2026, 9, 4))
        self.assertEqual(data["booked_month"], 500)
        self.assertEqual(data["invoiced_month"], 360)
        self.assertEqual(data["paid_today"], 360)
        self.assertEqual(data["today_progress"], 100)
        self.assertEqual(data["days_hit_month"], 1)

    def test_xero_snapshot_takes_priority_without_double_counting(self):
        self.mod.run("""INSERT INTO invoices(invoice_date,status,total,xero_invoice_id,xero_amount_paid)
                        VALUES ('2026-09-03','Paid',200,'xero-1',200)""")
        self.mod.run("""INSERT INTO xero_invoice_snapshot
                        (xero_invoice_id,invoice_date,fully_paid_on_date,status,total,amount_paid,amount_due)
                        VALUES ('xero-1','2026-09-03','2026-09-04','PAID',200,200,0)""")
        data = self.mod.business_goals_data(today=date(2026, 9, 4))
        self.assertEqual(data["paid_today"], 200)
        self.assertEqual(data["invoiced_month"], 200)

    def test_action_plan_can_be_added_and_implemented(self):
        response = self.client.post("/business-goals/actions", data={
            "title": "Follow up every open quote", "notes": "Do this each morning", "due_date": "2026-09-10",
        })
        self.assertEqual(response.status_code, 302)
        item = self.mod.q("SELECT * FROM business_goal_actions", one=True)
        self.assertEqual(item["status"], "Planned")
        response = self.client.post(f"/business-goals/actions/{item['id']}/toggle")
        self.assertEqual(response.status_code, 302)
        item = self.mod.q("SELECT * FROM business_goal_actions", one=True)
        self.assertEqual(item["status"], "Implemented")
        self.assertTrue(item["completed_at"])

    def test_xero_refresh_saves_revenue_snapshot(self):
        payload = {"Invoices": [{
            "InvoiceID": "inv-123", "InvoiceNumber": "INV-123", "Contact": {"Name": "Customer"},
            "DateString": "2026-09-01", "DueDateString": "2026-09-15", "FullyPaidOnDate": "2026-09-04",
            "Status": "PAID", "Total": 420, "AmountPaid": 420, "AmountDue": 0,
        }]}
        with mock.patch.object(self.mod, "xero_api_request", return_value=payload):
            count = self.mod.refresh_xero_revenue_snapshot()
        self.assertEqual(count, 1)
        row = self.mod.q("SELECT * FROM xero_invoice_snapshot WHERE xero_invoice_id='inv-123'", one=True)
        self.assertEqual(row["amount_paid"], 420)

    def test_goals_page_renders_key_sections(self):
        response = self.client.get("/business-goals")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Your business growth command centre", body)
        self.assertIn("Month-by-month performance", body)
        self.assertIn("Your plan of action", body)


if __name__ == "__main__":
    unittest.main()
