import unittest
from unittest.mock import patch
import test_customer_conversation as fixture
class SimpleWorkspaceTests(unittest.TestCase):
 def setUp(self):
  fixture.CustomerConversationTests.setUp(self)
  import importlib,unified_app
  if 'simple_workspace' not in self.mod.app.view_functions: importlib.reload(unified_app)
 tearDown=fixture.CustomerConversationTests.tearDown
 def test_home_uses_existing_tools_without_sending(self):
  with patch.object(self.mod,'send_clicksend_env_sms') as sms,patch.object(self.mod,'send_env_email') as email:
   r=self.client.get('/simple');self.assertEqual(r.status_code,200)
   for url in (b'/simple/customers',b'/customer-messages',b'/calendar',b'/reminders'):self.assertIn(url,r.data)
   sms.assert_not_called();email.assert_not_called()
 def test_customer_search_keeps_original_message_route(self):
  with patch.object(self.mod,'send_clicksend_env_sms') as sms,patch.object(self.mod,'send_env_email') as email:
   r=self.client.get('/simple/customers?q=Test');self.assertEqual(r.status_code,200);self.assertIn(b'Test Customer',r.data);self.assertIn(f'/customers/{self.customer}/conversation'.encode(),r.data)
   self.assertNotIn(b'Test Customer',self.client.get('/simple/customers?q=does-not-exist').data)
   self.assertNotIn(b'Test Customer',self.client.get('/simple/customers?q=%25').data)
   sms.assert_not_called();email.assert_not_called()
 def test_auth_required(self):
  with self.client.session_transaction() as session:session.clear()
  self.assertEqual(self.client.get('/simple').status_code,302)
  self.assertEqual(self.client.get('/simple/customers').status_code,302)
if __name__=='__main__':unittest.main()
