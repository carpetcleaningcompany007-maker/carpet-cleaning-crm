import importlib
import os
import tempfile
import unittest


class ReviewRequestPersonalisationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        import app
        self.appmod = importlib.reload(app)
        self.appmod.app.config["TESTING"] = True
        self.context = self.appmod.app.app_context()
        self.context.push()
        self.appmod.init_db()

    def tearDown(self):
        self.context.pop()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_review_request_uses_customers_first_name(self):
        customer = {"first_name": "Sarah", "last_name": "Jones"}
        replacements = self.appmod.customer_message_replacements(customer)
        template = self.appmod.message_template("review_request_sms")
        rendered = self.appmod.render_simple_template(template["body"], replacements)

        self.assertTrue(rendered.startswith("Hi Sarah,"))
        self.assertIn("thank you for choosing me", rendered)
        self.assertNotIn("Sarah Jones", rendered)

    def test_imported_zero_never_appears_as_customer_name(self):
        customer = {"first_name": 0, "last_name": ""}
        replacements = self.appmod.customer_message_replacements(customer)
        template = self.appmod.message_template("review_request_sms")
        rendered = self.appmod.render_simple_template(template["body"], replacements)

        self.assertTrue(rendered.startswith("Hi there,"))
        self.assertNotIn("Hi 0", rendered)

    def test_job_template_uses_same_safe_fallback(self):
        replacements = self.appmod.template_context_for_job({"first_name": "0"})
        self.assertEqual(replacements["{{first_name}}"], "there")
        self.assertEqual(replacements["{{name}}"], "there")

    def test_customer_name_search_ignores_incidental_email_matches(self):
        self.appmod.run(
            "INSERT INTO customers(first_name,last_name,email,phone) VALUES (?,?,?,?)",
            ("Mark", "Cooksey", "mark@example.com", "07000000001"),
        )
        self.appmod.run(
            "INSERT INTO customers(first_name,last_name,email,phone) VALUES (?,?,?,?)",
            ("Other", "Person", "marketing@example.com", "07000000002"),
        )
        client = self.appmod.app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True

        response = client.get("/customers?q=Mark&scope=all")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Mark Cooksey", body)
        self.assertNotIn("Other Person", body)

    def test_review_email_uses_first_name_and_has_no_logo_placeholder(self):
        job = {
            "first_name": "Mark Cooksey",
            "last_name": "Cooksey",
            "job_date": "2026-09-04",
            "address": "1 High Street",
            "town": "Ludlow",
            "postcode": "SY8 1AA",
        }
        rendered = self.appmod.day_run_email_html(
            "review",
            job,
            "Hi Mark, thank you for choosing me to clean your carpets.",
        )

        self.assertIn("Hi Mark,", rendered)
        self.assertNotIn("Mark Cooksey", rendered)
        self.assertNotIn("site/email-logo.png", rendered)
        self.assertNotIn('width="104"', rendered)
        self.assertIn("Please click here to leave us a Google review", rendered)
        self.assertIn("background-color:#071524", rendered)


if __name__ == "__main__":
    unittest.main()
