import importlib
import json
import os
import tempfile
import unittest
from unittest import mock


class FakeOpenAIResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode('utf-8')


class AICustomerReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.tmp.close()
        os.environ['CRM_DB_PATH'] = self.tmp.name
        os.environ['DISABLE_CRM_BACKGROUND_AUTOMATION'] = '1'
        os.environ['OPENAI_API_KEY'] = 'test-server-key'
        import app
        self.appmod = importlib.reload(app)
        self.app = self.appmod.app
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.appmod.init_db()
        self.appmod.ai_settings_row()
        self.appmod.run("UPDATE ai_settings SET enabled=1, services='Professional carpet and upholstery cleaning', prices_and_rules='Never quote unless a price is recorded.' WHERE id=1")
        cur = self.appmod.db().execute("""INSERT INTO customers(first_name,last_name,phone,email,postcode)
            VALUES ('Jane','Example','07800111222','jane@example.com','SY1 1AA')""")
        self.customer_id = cur.lastrowid
        cur = self.appmod.db().execute("""INSERT INTO intake_submissions(name,phone,email,postcode,what_cleaned,number_rooms,stains,additional_notes,customer_id)
            VALUES ('Jane Example','07800111222','jane@example.com','SY1 1AA','Carpet cleaning','2','Coffee','Please advise on the lounge carpet',?)""", (self.customer_id,))
        self.lead_id = cur.lastrowid
        self.appmod.db().execute("INSERT INTO communications(customer_id,channel,subject,body,created_at) VALUES (?,'SMS','Inbound SMS','Can you help next week?',datetime('now'))", (self.customer_id,))
        self.appmod.db().commit()

    def tearDown(self):
        self.ctx.pop()
        os.environ.pop('OPENAI_API_KEY', None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def fake_payload(self, manual=False):
        result = {
            'subject': '',
            'body': 'Hi Jane, thank you for the details. Paul will review your two-room carpet enquiry.',
            'needs_manual_response': manual,
            'manual_reason': 'Paul needs to confirm availability.' if manual else '',
        }
        return {'output_text': json.dumps(result), 'usage': {'input_tokens': 900, 'output_tokens': 70}}

    def test_generation_uses_enquiry_and_records_usage_without_sending(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured['request'] = json.loads(req.data.decode('utf-8'))
            return FakeOpenAIResponse(self.fake_payload())

        with mock.patch.object(self.appmod.urllib.request, 'urlopen', side_effect=fake_urlopen), \
             mock.patch.object(self.appmod, 'send_sms_gateway', side_effect=AssertionError('Generation must not send SMS')), \
             mock.patch.object(self.appmod, 'send_comms_email', side_effect=AssertionError('Generation must not send email')):
            draft = self.appmod.generate_ai_customer_reply(self.customer_id, self.lead_id, 'SMS')

        self.assertEqual(draft['status'], 'Generated')
        self.assertEqual(draft['body'], 'Hi Jane, thank you for the details. Paul will review your two-room carpet enquiry.')
        prompt = captured['request']['input']
        instructions = captured['request']['instructions']
        context = json.loads(prompt.split('\n', 1)[1])
        self.assertEqual(context['customer_name_for_greeting'], 'Jane Example')
        self.assertIn('Coffee', prompt)
        self.assertIn('Please advise on the lounge carpet', prompt)
        self.assertIn('Can you help next week?', prompt)
        self.assertIn('thank you very much for your enquiry', instructions)
        self.assertIn('Do not ask again for information already supplied', instructions)
        self.assertIn('customer_name_for_greeting', instructions)
        self.assertIn("Write as Paul's helpful secretary", instructions)
        self.assertIn('I can arrange for Paul to give you a quick call', instructions)
        self.assertIn('Carpet cleaning, upholstery cleaning, rug cleaning and hard-floor cleaning are different services', instructions)
        self.assertFalse(captured['request']['store'])
        usage = self.appmod.q('SELECT * FROM ai_usage_log WHERE draft_id=?', (draft['id'],), one=True)
        self.assertEqual(usage['status'], 'Success')
        self.assertGreater(usage['estimated_cost_usd'], 0)

    def test_submitted_enquiry_name_wins_over_customer_record_name(self):
        self.appmod.run("UPDATE customers SET first_name='Paul', last_name='Nicholas' WHERE id=?", (self.customer_id,))
        context, resolved_customer_id = self.appmod.ai_context_payload(self.customer_id, self.lead_id, 'SMS')
        self.assertEqual(resolved_customer_id, self.customer_id)
        self.assertEqual(context['customer_name_for_greeting'], 'Jane Example')
        self.assertEqual(context['customer']['first_name'], 'Paul')

    def test_missing_customer_name_is_not_invented(self):
        self.appmod.run("UPDATE intake_submissions SET name='' WHERE id=?", (self.lead_id,))
        self.appmod.run("UPDATE customers SET first_name='', last_name='' WHERE id=?", (self.customer_id,))
        context, _ = self.appmod.ai_context_payload(self.customer_id, self.lead_id, 'SMS')
        self.assertIsNone(context['customer_name_for_greeting'])

    def test_manual_response_flag_is_saved(self):
        with mock.patch.object(self.appmod.urllib.request, 'urlopen', return_value=FakeOpenAIResponse(self.fake_payload(manual=True))):
            draft = self.appmod.generate_ai_customer_reply(self.customer_id, self.lead_id, 'Email')
        self.assertEqual(draft['needs_manual_response'], 1)
        self.assertIn('availability', draft['manual_reason'])

    def test_owner_is_alerted_when_manual_draft_is_generated(self):
        with mock.patch.object(self.appmod.urllib.request, 'urlopen', return_value=FakeOpenAIResponse(self.fake_payload())):
            draft = self.appmod.generate_ai_customer_reply(self.customer_id, self.lead_id, 'SMS')
        with mock.patch.dict(os.environ, {'OWNER_ALERT_EMAIL': 'owner@example.com', 'OWNER_ALERT_MOBILE': '07802563213'}), \
             mock.patch.object(self.appmod, 'send_env_email', return_value=(True, 'sent')) as email_send, \
             mock.patch.object(self.appmod, 'send_clicksend_env_sms', return_value=(True, 'sent')) as sms_send, \
             self.app.test_request_context('/'):
            results = self.appmod.notify_owner_ai_draft_ready(draft)

        self.assertEqual(results['email'], (True, 'sent'))
        self.assertEqual(results['sms'], (True, 'sent'))
        self.assertIn('Nothing has been sent', email_send.call_args.args[2])
        self.assertIn('#ai-reply', email_send.call_args.args[2])
        self.assertIn('Nothing sent', sms_send.call_args.args[1])
        self.assertIn('#ai-reply', sms_send.call_args.args[1])

    def test_new_enquiry_prepares_one_approval_draft_and_alert_links_to_it(self):
        with mock.patch.object(self.appmod.urllib.request, 'urlopen', return_value=FakeOpenAIResponse(self.fake_payload())):
            first, message = self.appmod.ensure_ai_draft_for_intake(self.lead_id, self.customer_id)
            second, second_message = self.appmod.ensure_ai_draft_for_intake(self.lead_id, self.customer_id)

        self.assertIsNotNone(first)
        self.assertEqual(first['id'], second['id'])
        self.assertIn('prepared', message.lower())
        self.assertIn('already', second_message.lower())
        count = self.appmod.q('SELECT count(*) AS c FROM ai_drafts WHERE intake_id=?', (self.lead_id,), one=True)
        self.assertEqual(count['c'], 1)
        with self.app.test_request_context('/'):
            alert = self.appmod.owner_enquiry_alert_text(
                {'name': 'Jane Example', 'phone': '07800111222'},
                customer_id=self.customer_id,
                lead_id=self.lead_id,
            )
        self.assertIn('review, edit, send, regenerate or discard', alert.lower())
        self.assertIn('#ai-reply', alert)

    def test_disabled_assistant_does_not_call_openai(self):
        self.appmod.run('UPDATE ai_settings SET enabled=0 WHERE id=1')
        with mock.patch.object(self.appmod.urllib.request, 'urlopen', side_effect=AssertionError('OpenAI must not be called')):
            with self.assertRaisesRegex(RuntimeError, 'switched off'):
                self.appmod.generate_ai_customer_reply(self.customer_id, self.lead_id, 'SMS')

    def test_ai_pages_require_login_and_render_after_login(self):
        client = self.app.test_client()
        self.assertEqual(client.get('/ai-settings').status_code, 302)
        with client.session_transaction() as session:
            session['logged_in'] = True
        response = client.get('/ai-settings')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Safe draft-only assistant', response.data)

    def test_enquiry_and_sms_pages_show_manual_ai_draft_controls(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['logged_in'] = True
        enquiry = client.get(f'/intake-forms/{self.lead_id}')
        sms = client.get(f'/sms-threads/{self.customer_id}')
        self.assertEqual(enquiry.status_code, 200)
        self.assertEqual(sms.status_code, 200)
        self.assertIn(b'Generate AI Reply', enquiry.data)
        self.assertIn(b'Nothing sends until', enquiry.data)
        self.assertIn(b'Generate AI Reply', sms.data)


if __name__ == '__main__':
    unittest.main()
