import importlib
import os
import tempfile
import unittest


class AssistantCustomerApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.old_token = os.environ.get("CRM_ASSISTANT_API_TOKEN")
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        os.environ["CRM_ASSISTANT_API_TOKEN"] = "test-assistant-token-with-enough-entropy"
        import app
        self.appmod = importlib.reload(app)
        self.app = self.appmod.app
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.appmod.init_db()
        self.client = self.app.test_client()

    def tearDown(self):
        self.ctx.pop()
        if self.old_token is None:
            os.environ.pop("CRM_ASSISTANT_API_TOKEN", None)
        else:
            os.environ["CRM_ASSISTANT_API_TOKEN"] = self.old_token
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def post_customer(self, payload=None, token="test-assistant-token-with-enough-entropy"):
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        return self.client.post("/api/assistant/customers", json=payload or {
            "first_name": "Jane",
            "last_name": "Example",
            "phone": "07802 563213",
            "email": "jane@example.com",
            "address": "1 High Street",
            "town": "Ludlow",
            "postcode": "SY8 1AA",
            "source": "Assistant screenshot intake",
            "notes": "Customer details supplied in a screenshot.",
        }, headers=headers)

    def test_requires_configured_bearer_token(self):
        self.assertEqual(self.post_customer(token=None).status_code, 401)
        self.assertEqual(self.post_customer(token="wrong-token").status_code, 401)
        os.environ.pop("CRM_ASSISTANT_API_TOKEN")
        response = self.post_customer()
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.get_json()["error"].lower())

    def test_creates_customer_and_audit_entry_without_external_actions(self):
        response = self.post_customer()
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["created"])
        customer = self.appmod.q("SELECT * FROM customers WHERE id=?", (body["customer_id"],), one=True)
        self.assertEqual(customer["first_name"], "Jane")
        self.assertEqual(customer["source"], "Assistant screenshot intake")
        timeline = self.appmod.q("SELECT * FROM customer_timeline WHERE customer_id=?", (body["customer_id"],), one=True)
        self.assertIn("authorised assistant intake API", timeline["note_text"])

    def test_retry_returns_existing_customer_instead_of_duplicate(self):
        first = self.post_customer()
        second = self.post_customer()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.get_json()["created"])
        count = self.appmod.q("SELECT COUNT(*) AS c FROM customers", one=True)["c"]
        self.assertEqual(count, 1)

    def test_accepts_full_name_and_email_only(self):
        response = self.post_customer({"name": "Alex Morgan", "email": "alex@example.com", "postcode": "SY8 2BB"})
        self.assertEqual(response.status_code, 201)
        customer = self.appmod.q("SELECT * FROM customers WHERE id=?", (response.get_json()["customer_id"],), one=True)
        self.assertEqual((customer["first_name"], customer["last_name"]), ("Alex", "Morgan"))

    def test_rejects_invalid_contact_data(self):
        response = self.post_customer({"first_name": "Bad", "last_name": "Phone", "phone": "123"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("valid UK phone", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
