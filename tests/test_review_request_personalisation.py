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


if __name__ == "__main__":
    unittest.main()
