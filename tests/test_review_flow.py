import re
import unittest
from unittest import mock
import test_customer_xero_exports as fixture

class ReviewFlowTests(unittest.TestCase):
    tearDown = fixture.CustomerXeroExportTests.tearDown
    def setUp(self):
        fixture.CustomerXeroExportTests.setUp(self)
        self.mod.run("UPDATE settings SET test_email='owner@example.test',sms_test_number='07999999999' WHERE id=1")
        self.url = f'/send-contact-form?action_type=review&customer_id={self.first_id}'

    def form(self, channel='email', test=False):
        response=self.client.get(self.url+'&review_channel='+channel+('&target=test' if test else ''))
        self.assertEqual(response.status_code,200)
        token=re.search(r'name="preview_token" value="([^"]+)"',response.get_data(as_text=True)).group(1)
        return dict(action_type='review',customer_id=self.first_id,review_channel=channel,target='test' if test else 'customer',preview_token=token,review_action='send',_csrf_token='test-csrf')

    def count(self):
        return self.mod.q('SELECT COUNT(*) AS n FROM communications',one=True)['n']

    def test_preview_and_old_success_query_never_send(self):
        with mock.patch.object(self.mod,'send_env_email') as email, mock.patch.object(self.mod,'_raw_send_clicksend_env_sms') as sms:
            for suffix in ('','&review_channel=sms','&target=test','&sent=1&email=owner@example.test&phone=07999999999'):
                response=self.client.get(self.url+suffix)
                self.assertEqual(response.status_code,200)
                self.assertNotIn(b'Review request sent',response.data)
            self.assertEqual(email.call_count+sms.call_count,0)
        self.assertEqual(self.count(),0)

    def test_customer_recipient_authoritative_and_exact_email(self):
        data=self.form(); expected=self.mod.review_preview_data(self.mod.q('SELECT * FROM customers WHERE id=?',(self.first_id,),one=True),'email')
        data.update(email='owner@example.test',phone='07999999999',name='Wrong')
        with mock.patch.object(self.mod,'send_env_email',return_value=(True,'Provider accepted')) as send:
            r=self.client.post('/send-contact-form',data=data,follow_redirects=True)
        self.assertEqual(send.call_args.args,('alice@real.test',expected['subject'],expected['text_body'],expected['html']))
        self.assertIn(b'Accepted - delivery unconfirmed',r.data)
        self.assertEqual(self.count(),1)
        self.assertEqual(self.mod.q('SELECT email FROM customers WHERE id=?',(self.first_id,),one=True)['email'],'alice@real.test')

    def test_test_email_isolated(self):
        data=self.form(test=True)
        with mock.patch.object(self.mod,'send_env_email',return_value=(True,'Accepted')) as send, mock.patch.object(self.mod,'send_owner_customer_message_copy') as copy:
            r=self.client.post('/send-contact-form',data=data,follow_redirects=True)
        self.assertEqual(send.call_args.args[0],'owner@example.test')
        self.assertTrue(send.call_args.args[1].startswith('TEST - '))
        self.assertIsNone(send.call_args.kwargs['customer'])
        self.assertFalse(send.call_args.kwargs['record_customer_event'])
        copy.assert_not_called(); self.assertEqual(self.count(),0)
        self.assertIn(b'Test: Accepted - delivery unconfirmed',r.data)
        self.assertFalse(self.mod.q('SELECT review_request_sent_at FROM customers WHERE id=?',(self.first_id,),one=True)['review_request_sent_at'])

    def test_test_sms_not_subject_to_customer_pause(self):
        data=self.form('sms',True)
        with mock.patch.object(self.mod,'_raw_send_clicksend_env_sms',return_value=(True,'SMS accepted by ClickSend')) as send:
            r=self.client.post('/send-contact-form',data=data,follow_redirects=True)
        self.assertEqual(send.call_args.args[0],'07999999999');self.assertTrue(send.call_args.args[1].startswith('TEST - Hi Alice,'))
        self.assertIsNone(send.call_args.kwargs['customer']);self.assertEqual(self.count(),0)
        self.assertIn(b'Accepted - delivery unconfirmed',r.data)

    def test_sms_customer_pause_is_honest_and_does_not_call_provider(self):
        data=self.form('sms')
        with mock.patch.object(self.mod,'CUSTOMER_SMS_SENDING_PAUSED',True), mock.patch.object(self.mod,'_raw_send_clicksend_env_sms') as send:
            r=self.client.post('/send-contact-form',data=data)
        send.assert_not_called();self.assertEqual(self.count(),0)
        self.assertIn(b'Customer texting is currently paused',r.data)

    def test_failure_and_demo_never_mark_customer_or_show_success(self):
        for result in ((False,'INSUFFICIENT_CREDIT'),(True,'Demo only')):
            data=self.form()
            with mock.patch.object(self.mod,'send_env_email',return_value=result):
                r=self.client.post('/send-contact-form',data=data,follow_redirects=True)
            self.assertIn(b'Not sent',r.data);self.assertEqual(self.count(),0)

    def test_sms_failure_retains_actual_reason(self):
        data=self.form('sms',True)
        with mock.patch.object(self.mod,'_raw_send_clicksend_env_sms',return_value=(False,'ClickSend rejected: INSUFFICIENT_CREDIT')):
            r=self.client.post('/send-contact-form',data=data,follow_redirects=True)
        self.assertIn(b'INSUFFICIENT_CREDIT',r.data);self.assertIn(b'Test: Not sent',r.data);self.assertEqual(self.count(),0)

    def test_double_submit_only_calls_once(self):
        data=self.form()
        with mock.patch.object(self.mod,'send_env_email',return_value=(True,'Accepted')) as send:
            self.client.post('/send-contact-form',data=data)
            r=self.client.post('/send-contact-form',data=data)
        self.assertEqual(send.call_count,1);self.assertIn(b'already attempted',r.data)

    def test_missing_token_channel_change_and_changed_recipient_block(self):
        data=self.form(test=True);data.pop('preview_token')
        with mock.patch.object(self.mod,'send_env_email') as email,mock.patch.object(self.mod,'_raw_send_clicksend_env_sms') as sms:
            self.client.post('/send-contact-form',data=data)
            data=self.form(test=True);data['review_channel']='sms';self.client.post('/send-contact-form',data=data)
            data=self.form();self.mod.run("UPDATE customers SET email='new@example.test' WHERE id=?",(self.first_id,));self.client.post('/send-contact-form',data=data)
        email.assert_not_called();sms.assert_not_called()

    def test_preview_override_matches_send_and_segments(self):
        customer=self.mod.q('SELECT * FROM customers WHERE id=?',(self.first_id,),one=True)
        with mock.patch.object(self.mod,'customer_template_override',return_value={'subject':'Special {{first_name}}','body':'Hi {{first_name}}, special wording'}):
            data=self.mod.review_preview_data(customer,'email');page=self.client.get(self.url)
            self.assertIn('Hi Alice, special wording',data['html']);self.assertIn(b'Special Alice',page.data)
        sms=self.mod.review_preview_data(customer,'sms');info=self.mod.sms_length_info(sms['body'])
        self.assertTrue(all(c in self.mod.SMS_GSM_BASIC or c in self.mod.SMS_GSM_EXTENDED for c in sms['body']))
        page=self.client.get(self.url+'&review_channel=sms');self.assertIn(f"{info['parts']} SMS segment".encode(),page.data)

    def test_legacy_ruby_record_repair_is_specific_and_auditable(self):
        self.mod.run("INSERT INTO customers(id,first_name,last_name) VALUES(747,'Ruby','Reseigh')")
        self.mod.run("INSERT INTO communications(id,customer_id,channel,subject,body,created_at) VALUES(384,747,'SMS','Google review request','Hi Ruby, original text','2026-09-28 21:47:00')")
        self.mod.init_db(); row=self.mod.q('SELECT * FROM communications WHERE id=384',one=True)
        self.assertEqual(row['channel'],'Test blocked');self.assertEqual(row['body'],'Hi Ruby, original text')
        self.assertFalse(self.mod.communication_matches([row],'google review','review request'))

    def test_customer_sms_only_selected_channel(self):
        with mock.patch.object(self.mod,'CUSTOMER_SMS_SENDING_PAUSED',False):
            data=self.form('sms')
            with mock.patch.object(self.mod,'_raw_send_clicksend_env_sms',return_value=(True,'Queued')) as sms,mock.patch.object(self.mod,'send_env_email') as email:
                self.client.post('/send-contact-form',data=data)
            email.assert_not_called();self.assertEqual(sms.call_args.args[0],'07802563213');self.assertEqual(self.count(),1)

    def test_unconfirmed_exception_is_locked(self):
        data=self.form()
        with mock.patch.object(self.mod,'send_env_email',side_effect=TimeoutError) as send:
            r=self.client.post('/send-contact-form',data=data,follow_redirects=True)
            self.client.post('/send-contact-form',data=data)
        self.assertEqual(send.call_count,1);self.assertIn(b'Unconfirmed - check history',r.data);self.assertEqual(self.count(),0)

    def test_manual_sms_reaches_provider_but_background_and_get_do_not(self):
        import json
        customer=self.mod.q('SELECT * FROM customers WHERE id=?',(self.first_id,),one=True)
        response=json.dumps({'response_code':'SUCCESS','data':{'messages':[{'message_id':'test-provider-id','status':'SUCCESS'}]}})
        with mock.patch.dict('os.environ',{'CLICKSEND_USERNAME':'test','CLICKSEND_API_KEY':'fake'}), mock.patch.object(self.mod,'clicksend_reply_number',return_value=''), mock.patch.object(self.mod,'http_post_basic_json',return_value=response) as provider:
            ok,_=self.mod._raw_send_clicksend_env_sms(customer['phone'],'Test body',customer=customer)
            self.assertFalse(ok);provider.assert_not_called()
            with self.app.test_request_context('/',method='GET'):
                self.mod.session['logged_in']=True
                ok,_=self.mod._raw_send_clicksend_env_sms(customer['phone'],'Test body',customer=customer)
                self.assertFalse(ok)
            with self.app.test_request_context('/',method='POST'):
                self.mod.session['logged_in']=True
                ok,detail=self.mod._raw_send_clicksend_env_sms(customer['phone'],'Test body',customer=customer)
                self.assertTrue(ok);self.assertIn('accepted',detail)
            self.assertEqual(provider.call_count,1)

    def test_automated_first_message_and_rules_stay_paused_even_in_authenticated_post(self):
        lead=self.mod.run("INSERT INTO intake_submissions(name,phone,customer_id) VALUES('Alice','07802563213',?)",(self.first_id,))
        with mock.patch.object(self.mod,'send_clicksend_env_sms') as sms, mock.patch.object(self.mod,'send_env_email') as email, mock.patch.object(self.mod.threading,'Timer') as timer:
            self.mod.schedule_enquiry_acknowledgement(lead,self.first_id,{'name':'Alice','phone':'07802563213'})
            timer.assert_not_called()
            self.mod.run("UPDATE enquiry_acknowledgement_queue SET status='Queued',due_at='2000-01-01' WHERE lead_id=?",(lead,))
            with self.app.test_request_context('/',method='POST'):
                self.mod.session['logged_in']=True
                self.assertEqual(self.mod.run_due_enquiry_acknowledgements(),[])
                self.assertEqual(self.mod.run_due_communication_automations(),[])
                self.assertEqual(self.mod.automation_send_for_rule({},{}),[])
            sms.assert_not_called();email.assert_not_called()
        self.assertEqual(self.mod.q('SELECT status FROM enquiry_acknowledgement_queue WHERE lead_id=?',(lead,),one=True)['status'],'Awaiting approval')

    def test_follow_up_and_legacy_schedule_do_not_send_customer_messages(self):
        lead=self.mod.run("INSERT INTO intake_submissions(name,phone,customer_id,status) VALUES('Alice','07802563213',?,'New')",(self.first_id,))
        self.mod.run("INSERT INTO enquiry_follow_up_queue(lead_id,customer_id,phone,body,due_at,status,scheduled_send_at) VALUES(?,?,?,'Hello','2000-01-01','Scheduled','2000-01-01')",(lead,self.first_id,'07802563213'))
        with mock.patch.object(self.mod,'send_clicksend_env_sms') as sms, mock.patch.object(self.mod,'send_env_email') as email, mock.patch.object(self.mod,'owner_contact_form_recipients',return_value=('','')):
            self.mod.run_due_enquiry_follow_up_sms()
            self.mod.run_due_scheduled_enquiry_texts()
            sms.assert_not_called();email.assert_not_called()
        self.assertFalse(self.mod.q('SELECT sent_at FROM enquiry_follow_up_queue WHERE lead_id=?',(lead,),one=True)['sent_at'])

    def test_provider_per_message_rejection_and_pending_are_distinct(self):
        import json
        for status, expected in [('INSUFFICIENT_CREDIT',b'Not sent'),('QUEUED',b'Accepted - delivery unconfirmed')]:
            data=self.form('sms',True)
            response=json.dumps({'response_code':'SUCCESS','data':{'messages':[{'message_id':'mock-'+status,'status':status}]}})
            with mock.patch.dict('os.environ',{'CLICKSEND_USERNAME':'test','CLICKSEND_API_KEY':'fake'}), mock.patch.object(self.mod,'clicksend_reply_number',return_value=''), mock.patch.object(self.mod,'http_post_basic_json',return_value=response):
                r=self.client.post('/send-contact-form',data=data,follow_redirects=True)
            self.assertIn(expected,r.data)
            self.assertEqual(self.count(),0)
            event=self.mod.q('SELECT * FROM sms_events ORDER BY id DESC LIMIT 1',one=True)
            self.assertFalse(event['customer_id'])
            self.assertEqual(event['to_phone'],'+447999999999')

    def test_customer_sms_action_is_visible_and_test_switch_is_explicit(self):
        page=self.client.get(self.url+'&review_channel=sms').get_data(as_text=True)
        self.assertIn('Send text to Alice',page)
        self.assertIn('To customer: Alice',page)
        self.assertIn('07802563213',page)
        self.assertIn('Test to me',page)
        self.assertIn('position:fixed',page)
        self.assertIn('name="target" value="customer"',page)
        test=self.client.get(self.url+'&review_channel=sms&target=test').get_data(as_text=True)
        self.assertIn('Send test text to me',test)
        self.assertIn('To customer: Alice',test)
        self.assertIn('07999999999',test)
        self.assertNotIn('Back to customer preview',test)

    def test_email_auth_failure_is_actionable_and_persistent(self):
        data=self.form(test=True)
        with mock.patch.object(self.mod,'send_env_email',return_value=(False,'535 Username and Password not accepted; secret-debug')):
            response=self.client.post('/send-contact-form',data=data,follow_redirects=True)
        self.assertIn(b'rejected the sign-in',response.data)
        self.assertNotIn(b'secret-debug',response.data)
        later=self.client.get(self.url+'&target=test')
        self.assertIn(b'rejected the sign-in',later.data)
        self.assertIn(b'Test: Not sent',later.data)
        self.assertEqual(self.count(),0)

if __name__=='__main__': unittest.main()
