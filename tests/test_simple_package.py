import unittest
from unittest.mock import patch
import test_customer_conversation as fixture
class IntegratedPackageTests(unittest.TestCase):
 def setUp(self):
  fixture.CustomerConversationTests.setUp(self)
  import importlib,unified_app
  if 'simple_workspace' not in self.mod.app.view_functions: importlib.reload(unified_app)
 tearDown=fixture.CustomerConversationTests.tearDown
 def lead(self,name='Website enquiry',**kwargs):
  data={'name':name,'created_at':'2026-11-19 10:00:00','customer_id':self.customer,'phone':'07700900123','email':'test@example.invalid',**kwargs}
  return self.mod.run('INSERT INTO intake_submissions('+','.join(data)+') VALUES ('+','.join('?' for _ in data)+')',tuple(data.values()))
 def test_navigation_and_original_conversation_forms(self):
  with patch.object(self.mod,'send_env_email') as email,patch.object(self.mod,'send_clicksend_env_sms') as sms:
   for path in ['/simple','/simple/customers','/simple/enquiries','/customer-messages',f'/customers/{self.customer}/conversation','/reminders','/calendar',f'/simple/customers/{self.customer}','/website-analytics']:
    response=self.client.get(path);self.assertEqual(response.status_code,200,path)
    self.assertIn(b'/simple/enquiries',response.data,path)
   response=self.client.get(f'/customers/{self.customer}/conversation')
   self.assertIn(b'name="_csrf_token"',response.data);self.assertIn(b'Send message',response.data)
   self.assertIn(b'writing-assistance.js',response.data);email.assert_not_called();sms.assert_not_called()
 def test_tracker_excludes_tests_and_deduplicates_job_values(self):
  quote=self.mod.run('INSERT INTO quotes(customer_id,total) VALUES (?,?)',(self.customer,120))
  self.mod.run('UPDATE jobs SET quote_id=? WHERE id=?',(quote,self.job))
  self.mod.run("INSERT INTO invoices(customer_id,job_id,total,status) VALUES (?,?,?,'Paid')",(self.customer,self.job,120))
  self.lead('First enquiry',job_id=self.job,gclid='click1');self.lead('Repeat enquiry',job_id=self.job,gclid='click1')
  self.lead('Hidden test',is_test=1)
  with patch.object(self.mod,'render_template',wraps=self.mod.render_template) as render:
   r=self.client.get('/simple/enquiries?period=all');self.assertEqual(r.status_code,200)
  # The module keeps its own render_template reference, so inspect its supplied context.
  import simple_package
  with patch.object(simple_package,'render_template',return_value='ok') as render:
   self.client.get('/simple/enquiries?period=all')
   ctx=render.call_args.kwargs;self.assertEqual(ctx['totals']['leads'],2);self.assertEqual(ctx['totals']['sales'],120);self.assertEqual(ctx['totals']['quotes'],120)
   self.assertEqual(ctx['totals']['sales_known'],1)
  self.assertNotIn(b'Hidden test',r.data);self.assertIn(b'Google Ads',r.data)
 def test_unknowns_source_filters_and_explicit_all_time(self):
  self.lead('Old social lead',created_at='2020-01-01',utm_source='Facebook');self.lead('Current source unknown')
  r=self.client.get('/simple/enquiries?period=all&source=Facebook');self.assertIn(b'Old social lead',r.data);self.assertNotIn(b'Current source unknown',r.data);self.assertIn(b'Paid value unknown',r.data)
  self.assertNotIn(b'Old social lead',self.client.get('/simple/enquiries?period=30').data)
 def test_root_switch_is_opt_in_and_report_requires_auth(self):
  self.mod.app.config['SIMPLE_CRM_HOME']=True
  r=self.client.get('/');self.assertEqual(r.status_code,302);self.assertTrue(r.location.endswith('/simple'))
  with self.client.session_transaction() as s:s.clear()
  self.assertEqual(self.client.get('/simple/enquiries').status_code,302)
if __name__=='__main__':unittest.main()

