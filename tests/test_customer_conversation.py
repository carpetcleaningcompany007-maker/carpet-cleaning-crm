import os
import tempfile
import unittest
from unittest.mock import patch


class CustomerConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['CRM_DB_PATH'] = os.path.join(self.tmp.name, 'test.db')
        os.environ['DISABLE_CRM_BACKGROUND_AUTOMATION'] = '1'
        import importlib
        import app
        self.mod = importlib.reload(app)
        self.mod.app.config.update(TESTING=True)
        self.ctx = self.mod.app.app_context()
        self.ctx.push()
        self.mod.init_db()
        customer = self.mod.run('INSERT INTO customers(first_name,last_name,email,phone) VALUES (?,?,?,?)', ('Test', 'Customer', 'test@example.invalid', '07700900123'))
        self.customer = customer
        self.job = self.mod.run('INSERT INTO jobs(customer_id,title,status) VALUES (?,?,?)', (customer, 'Test job', 'Booked'))
        self.client = self.mod.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True

    def tearDown(self):
        self.ctx.pop()
        self.tmp.cleanup()

    def test_history_combines_channels_and_keeps_customers_separate(self):
        self.mod.run("INSERT INTO inbound_customer_emails(message_id,sender_email,customer_id,body_text,received_at) VALUES (?,?,?,?,?)", ('one','test@example.invalid',self.customer,'Customer email reply','2026-09-07 08:30:00'))
        self.mod.log_sms_event(self.customer,None,'Test','inbound','','','Customer text reply',direction='inbound')
        with patch.object(self.mod,'_send_env_email',return_value=(True,'Sent')):
            customer=self.mod.q('SELECT * FROM customers WHERE id=?',(self.customer,),one=True)
            self.mod.send_env_email(customer['email'],'Our reply','Sent email',customer=customer)
        self.mod.run("INSERT INTO communications(customer_id,channel,subject,body,created_at) VALUES (?,?,?,?,datetime('now'))",(self.customer,'Email','Our reply','Sent email'))
        other=self.mod.run("INSERT INTO customers(first_name,last_name) VALUES (?,?)",('Other','Customer'))
        self.mod.log_sms_event(other,None,'Test','inbound','','','Private other message',direction='inbound')
        rows=self.mod.customer_conversation_rows(self.customer)
        self.assertEqual(len(rows),3)
        self.assertEqual({r['body'] for r in rows},{'Sent email','Customer text reply','Customer email reply'})
        self.assertIn('09:30 am',next(r['display_time'] for r in rows if r['body']=='Customer email reply'))
        page=self.client.get(f'/customers/{self.customer}/conversation')
        self.assertEqual(page.status_code,200)
        self.assertNotIn(b'Private other message',page.data)
        self.assertIn(b'Customer email reply',page.data)

    def test_failed_email_is_recorded_as_failed_and_draft_is_preserved(self):
        with patch.object(self.mod,'_send_env_email',return_value=(False,'Provider unavailable')):
            response=self.client.post(f'/customers/{self.customer}/conversation',data={'channel':'Email','subject':'Test','body':'Keep this draft'})
        self.assertIn(b'Keep this draft',response.data)
        rows=self.mod.customer_conversation_rows(self.customer)
        self.assertEqual(rows[0]['status'],'Failed')

    def test_today_uses_uk_midnight_and_sms_opt_out_is_respected(self):
        from datetime import date
        self.mod.run("INSERT INTO customer_email_events(customer_id,body,status,created_at) VALUES (?,?,?,?)",(self.customer,'After UK midnight','Sent','2026-09-06 23:30:00'))
        with patch.object(self.mod,'uk_today',return_value=date(2026,9,7)):
            self.assertEqual(len(self.mod.customer_conversation_rows(today_only=True)),1)
        self.mod.run('UPDATE customers SET sms_opt_out=1 WHERE id=?',(self.customer,))
        with patch.object(self.mod,'send_clicksend_env_sms') as send:
            self.client.post(f'/customers/{self.customer}/conversation',data={'channel':'Text','body':'Do not send'})
            send.assert_not_called()

    def test_dashboard_contains_today_messages_and_search_filters_history(self):
        self.mod.run('UPDATE jobs SET job_date=? WHERE id=?',(self.mod.uk_today().isoformat(),self.job))
        self.mod.log_sms_event(self.customer,None,'Test','inbound','','','Searchable reply',direction='inbound')
        response=self.client.get('/dashboard')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Today',response.data)
        self.assertIn(f'/customers/{self.customer}/conversation'.encode(),response.data)
        self.assertEqual(self.mod.customer_conversation_rows(self.customer,search='absent'),[])

    def test_delivery_results_hide_provider_code_and_preserve_failure(self):
        ok,text=self.mod.friendly_delivery_result(True,'ClickSend Message ID: secret-id Status: SUCCESS','text message')
        self.assertTrue(ok)
        self.assertEqual(text,'Thank you, your text message has been sent.')
        ok,text=self.mod.friendly_delivery_result(False,'{"status":"ERROR","response_code":500}','email')
        self.assertFalse(ok)
        self.assertNotIn('response_code',text)
        self.assertIn('couldn’t confirm',text)
        ok,text=self.mod.friendly_delivery_result(True,'Demo SMS marked as sent','text message')
        self.assertIn('no real message',text)

    def test_download_contains_only_selected_customer_history(self):
        self.mod.log_sms_event(self.customer,None,'Test','inbound','','','Their reply',direction='inbound')
        other=self.mod.run("INSERT INTO customers(first_name,last_name) VALUES ('Other','Customer')")
        self.mod.log_sms_event(other,None,'Test','inbound','','','Private other reply',direction='inbound')
        response=self.client.get(f'/customers/{self.customer}/conversation/download')
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response.headers['Content-Disposition'])
        self.assertIn(b'Their reply',response.data)
        self.assertNotIn(b'Private other reply',response.data)
        self.assertEqual(self.mod.app.test_client().get(f'/customers/{self.customer}/conversation/download').status_code,302)
        self.assertIn(b'Open messages &amp; history',self.client.get(f'/customers/{self.customer}').data)
