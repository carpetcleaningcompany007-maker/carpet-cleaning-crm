import os
import tempfile
import unittest
from unittest.mock import patch


class ClickSendHistoryTests(unittest.TestCase):
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

    def sms(self, message_id='external-1',status='Delivered'):
        return {'message_id':message_id,'to':'+447700900123','from':'Company','body':'Sent outside CRM','date':1788773400,'direction':'out','status':status}

    def test_external_sms_import_is_idempotent_and_updates_existing_event(self):
        row=self.sms()
        existing=self.mod.log_sms_event(self.customer,None,'ClickSend','send','+447700900123','','Sent outside CRM','external-1','Accepted','outbound')
        self.mod.store_clicksend_history_item('sms',row)
        self.mod.attach_clicksend_history_items()
        self.mod.store_clicksend_history_item('sms',dict(row,status='Failed'))
        self.mod.attach_clicksend_history_items()
        events=self.mod.q('SELECT * FROM sms_events WHERE customer_id=?',(self.customer,))
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['id'],existing)
        self.assertEqual(events[0]['status'],'Failed')

    def test_shared_numbers_are_not_assigned_and_inbound_sender_is_matched(self):
        other=self.mod.run('INSERT INTO customers(first_name,last_name,phone) VALUES (?,?,?)',('Other','Person','07700900123'))
        self.mod.store_clicksend_history_item('sms',self.sms())
        self.mod.attach_clicksend_history_items()
        self.assertEqual(self.mod.q('SELECT COUNT(*) AS n FROM sms_events',one=True)['n'],0)
        self.mod.run('UPDATE customers SET phone=? WHERE id=?',('07700900456',other))
        self.mod.store_clicksend_history_item('sms',dict(self.sms('in-1'),direction='in',**{'from':'+447700900123','to':'+447700900999'}))
        self.mod.attach_clicksend_history_items()
        rows=self.mod.customer_conversation_rows(self.customer)
        self.assertEqual(len(rows),2)
        self.assertIn('Received',[r['status'] for r in rows])

    def test_email_recipients_provider_ids_and_old_logs_do_not_duplicate(self):
        self.mod.run("INSERT INTO customer_email_events(customer_id,recipient,subject,body,status,external_id) VALUES (?,?,?,?,?,?)",(self.customer,'test@example.invalid','Hello','Original text','Sent','mail-1'))
        record={'message_id':'mail-1','to':[{'email':'TEST@example.invalid'},{'email':'unknown@example.invalid'}],'subject':'Hello','body_plain_text':'Original text','date_added':1788773400,'status':'Delivered'}
        for _ in range(2):
            self.mod.store_clicksend_history_item('email',record)
            self.mod.attach_clicksend_history_items()
        self.assertEqual(len(self.mod.customer_conversation_rows(self.customer)),1)
        self.assertEqual(self.mod.q('SELECT status FROM customer_email_events',one=True)['status'],'Delivered')
        self.assertEqual(self.mod.q('SELECT COUNT(*) AS n FROM clicksend_history_items WHERE customer_id IS NULL',one=True)['n'],1)

    def test_pagination_resumes_and_never_sends(self):
        def history(channel,start,end,page):
            return ([self.sms('page-'+str(page))] if channel=='sms' else []), (3 if channel=='sms' else 1)
        with patch.object(self.mod,'clicksend_history_credentials',return_value=('user','key')), patch.object(self.mod,'clicksend_history_page',side_effect=history), patch.object(self.mod,'send_env_email') as email, patch.object(self.mod,'send_clicksend_env_sms') as sms:
            self.mod.poll_clicksend_history()
            state=self.mod.q("SELECT * FROM clicksend_history_sync WHERE channel='sms'",one=True)
            self.assertEqual(state['next_page'],3)
            self.mod.run('UPDATE clicksend_history_sync SET last_attempt=0')
            self.mod.poll_clicksend_history()
            self.assertEqual(len(self.mod.customer_conversation_rows(self.customer)),3)
            state=self.mod.q("SELECT * FROM clicksend_history_sync WHERE channel='sms'",one=True)
            self.assertEqual(state['status'],'Up to date')
            email.assert_not_called();sms.assert_not_called()

    def test_email_access_error_does_not_block_sms_and_retry_keeps_page(self):
        import urllib.error
        def history(channel,start,end,page):
            if channel=='email':
                raise urllib.error.HTTPError('https://rest.clicksend.com',403,'Forbidden',None,None)
            return [self.sms()],1
        with patch.object(self.mod,'clicksend_history_credentials',return_value=('user','key')), patch.object(self.mod,'clicksend_history_page',side_effect=history):
            self.mod.poll_clicksend_history()
        self.assertEqual(len(self.mod.customer_conversation_rows(self.customer)),1)
        state=self.mod.q("SELECT * FROM clicksend_history_sync WHERE channel='email'",one=True)
        self.assertEqual(state['next_page'],1)
        self.assertIn('403',state['status'])

    def test_http_parser_handles_both_documented_history_envelopes(self):
        import io,json
        for data in ([self.sms()],{'data':[self.sms()],'last_page':2}):
            with patch.object(self.mod,'clicksend_history_credentials',return_value=('user','key')), patch.object(self.mod.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps({'response_code':'SUCCESS','data':data}).encode())):
                rows,last=self.mod.clicksend_history_page('sms',0,1,1)
                self.assertEqual(rows[0]['message_id'],'external-1')
                self.assertGreaterEqual(last,1)
