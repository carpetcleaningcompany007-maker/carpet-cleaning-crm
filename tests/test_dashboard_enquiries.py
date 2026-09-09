import json,unittest
from unittest.mock import patch
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import test_customer_conversation as fixture

class DashboardEnquiryTests(unittest.TestCase):
    setUp=fixture.CustomerConversationTests.setUp
    tearDown=fixture.CustomerConversationTests.tearDown
    def lead(self,name='New enquiry',status='Queued',is_test=0):
        lead=self.mod.run("INSERT INTO intake_submissions(name,phone,email,customer_id,status,is_test) VALUES (?,?,?,?,?,?)",(name,'07700900123','test@example.invalid',self.customer,'New',is_test))
        due=(datetime.now(ZoneInfo('Europe/London'))-timedelta(minutes=1)).isoformat(timespec='seconds')
        self.mod.run("INSERT INTO enquiry_acknowledgement_queue(lead_id,customer_id,payload_json,due_at,status) VALUES (?,?,?,?,?)",(lead,self.customer,json.dumps({'name':name,'phone':'07700900123'}),due,status))
        return lead
    def action(self,lead,action):return self.client.post(f'/dashboard/enquiries/{lead}/action',data={'action':action})
    def status(self,lead):return self.mod.q('SELECT status FROM enquiry_acknowledgement_queue WHERE lead_id=?',(lead,),one=True)['status']
    def test_alert_count_real_queue_and_test_exclusion(self):
        self.lead('Actual lead');self.lead('Hidden test',is_test=1)
        page=self.client.get('/dashboard/enquiry-alerts')
        self.assertEqual(page.status_code,200);self.assertIn(b'Actual lead',page.data);self.assertNotIn(b'Hidden test',page.data)
        self.assertIn(b'data-due=',page.data);self.assertIn(b'Stop scheduled message',page.data)
    def test_stop_prevents_background_send(self):
        lead=self.lead();self.action(lead,'stop');self.assertEqual(self.status(lead),'Cancelled')
        with patch.object(self.mod,'send_clicksend_env_sms') as sms,patch.object(self.mod,'send_env_email') as email:
            self.mod.run_due_enquiry_acknowledgements();sms.assert_not_called();email.assert_not_called()
        self.assertIn(b'Automatic acknowledgement stopped',self.client.get('/dashboard/enquiry-alerts').data)
    def test_call_stops_queue_and_records_ownership(self):
        lead=self.lead();response=self.action(lead,'call');self.assertEqual(response.status_code,302)
        self.assertEqual(self.status(lead),'Cancelled');self.assertEqual(self.mod.dashboard_enquiry_alerts()[0]['owner_action'],'call')
    def test_inflight_send_cannot_claim_cancelled(self):
        lead=self.lead(status='Sending');self.action(lead,'stop');self.assertEqual(self.status(lead),'Sending')
        self.assertIsNone(self.mod.q('SELECT * FROM dashboard_enquiry_decisions WHERE lead_id=?',(lead,),one=True))
    def test_send_now_only_target_once(self):
        lead=self.lead();other=self.lead('Other customer')
        with patch.object(self.mod,'customer_sms_hours_open',return_value=True),patch.object(self.mod,'send_clicksend_env_sms',return_value=(True,'Message ID: TEST123')) as sms:
            self.action(lead,'send_now');self.action(lead,'send_now');self.assertEqual(sms.call_count,1)
        self.assertEqual(self.status(lead),'Accepted');self.assertEqual(self.status(other),'Queued')
    def test_quiet_hours_no_send(self):
        lead=self.lead()
        with patch.object(self.mod,'customer_sms_hours_open',return_value=False),patch.object(self.mod,'send_clicksend_env_sms') as sms:
            self.action(lead,'send_now');sms.assert_not_called()
        self.assertEqual(self.status(lead),'Queued')
    def test_auth_required(self):
        lead=self.lead()
        with self.client.session_transaction() as session:session.clear()
        self.assertEqual(self.client.get('/dashboard/enquiry-alerts').status_code,302)
        self.assertEqual(self.action(lead,'stop').status_code,302);self.assertEqual(self.status(lead),'Queued')

    def test_cancel_wins_after_worker_reads_before_claim(self):
        lead=self.lead();original_q=self.mod.q
        def read_then_cancel(sql,*args,**kwargs):
            rows=original_q(sql,*args,**kwargs)
            if 'SELECT q.*' in sql and 'enquiry_acknowledgement_queue' in sql:
                self.mod.run("UPDATE enquiry_acknowledgement_queue SET status='Cancelled' WHERE lead_id=?",(lead,))
            return rows
        with patch.object(self.mod,'q',side_effect=read_then_cancel),patch.object(self.mod,'send_clicksend_env_sms') as sms:
            self.mod.run_due_enquiry_acknowledgements();sms.assert_not_called()
        self.assertEqual(self.status(lead),'Cancelled')

    def test_failure_time_and_owner_choice_are_visible(self):
        lead=self.lead(status='Delivery failed')
        self.mod.run("UPDATE intake_submissions SET customer_sms_status=?,customer_email_status=? WHERE id=?",('Failed: SMS rejected','Failed: email returned',lead))
        page=self.client.get('/dashboard/enquiry-alerts').data
        self.assertIn(b'DELIVERY PROBLEM',page);self.assertIn(b'email returned',page);self.assertIn(b'data-received=',page)
        self.action(lead,'message')
        self.assertIn(b'You chose to message personally',self.client.get('/dashboard/enquiry-alerts').data)
        self.action(lead,'handled');self.assertEqual(self.mod.dashboard_enquiry_alerts(),[])
    def test_email_sent_is_not_treated_as_delivery_confirmation(self):
        self.lead(status='Email fallback sent')
        page=self.client.get('/dashboard/enquiry-alerts').data
        self.assertIn(b'email delivery is not confirmed',page)
