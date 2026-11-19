import importlib
import unittest
from unittest.mock import patch
import test_customer_conversation as fixture

class SharedWorkspaceTests(unittest.TestCase):
    def setUp(self):
        fixture.CustomerConversationTests.setUp(self)
        import unified_app, shared_app
        if 'simple_workspace' not in self.mod.app.view_functions:
            importlib.reload(unified_app)
        if 'full_crm_mode' not in self.mod.app.view_functions:
            importlib.reload(shared_app)

    tearDown = fixture.CustomerConversationTests.tearDown

    def test_view_switch_preserves_original_routes_and_customer_data(self):
        with patch.object(self.mod, 'send_env_email') as email, patch.object(self.mod, 'send_clicksend_env_sms') as sms:
            original = self.client.get('/')
            self.assertEqual(original.status_code, 200)
            self.assertIn(b'Switch to simple mode', original.data)
            self.assertNotIn(b'class="simple-side"', original.data)
            self.assertEqual(self.client.get('/simple').status_code, 200)
            self.assertIn(b'Test Customer', self.client.get('/simple/customers').data)
            conversation = self.client.get(f'/customers/{self.customer}/conversation')
            self.assertIn(b'class="simple-side"', conversation.data)
            self.assertIn(b'Switch to full CRM', conversation.data)
            self.assertIn(b'name="_csrf_token"', conversation.data)
            self.assertEqual(self.client.get('/full').status_code, 302)
            conversation = self.client.get(f'/customers/{self.customer}/conversation')
            self.assertNotIn(b'class="simple-side"', conversation.data)
            self.assertIn(b'AI-powered suggested reply', conversation.data)
            self.assertEqual(self.mod.q('SELECT COUNT(*) AS n FROM customers', one=True)['n'], 1)
            email.assert_not_called()
            sms.assert_not_called()

    def test_logged_out_simple_route_does_not_enable_mode(self):
        with self.client.session_transaction() as s:
            s.clear()
        self.assertEqual(self.client.get('/simple').status_code, 302)
        with self.client.session_transaction() as s:
            self.assertNotIn('simple_mode', s)

if __name__ == '__main__':
    unittest.main()
