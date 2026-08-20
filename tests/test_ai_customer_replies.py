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
        self.assertEqual(draft['body'], 'Hi Jane, thank you for the details. Paul will review your two-room carpet enquiry.\n\nKind regards,\nThe Carpet Cleaning Company')
        prompt = captured['request']['input']
        instructions = captured['request']['instructions']
        context = json.loads(prompt.split('\n', 1)[1])
        self.assertEqual(context['customer_name_for_greeting'], 'Jane')
        self.assertIn('Coffee', prompt)
        self.assertIn('Please advise on the lounge carpet', prompt)
        self.assertIn('Can you help next week?', prompt)
        self.assertIn('thank you for your enquiry', instructions)
        self.assertIn('Do not ask about parking or access in an initial enquiry reply', instructions)
        self.assertIn('FIRST response to a new website enquiry', instructions)
        self.assertIn('Use the word "photos", never "photographs"', instructions)
        self.assertIn('Paul must approve every draft', instructions)
        self.assertIn('Do not over-explain, repeat facts, list packages, quote prices', instructions)
        self.assertIn('Do not ask whether the customer wants the cheapest quote or the best result', instructions)
        self.assertIn('End every initial reply with exactly', instructions)
        self.assertIn('Do not ask again for information already supplied', instructions)
        self.assertIn('customer_name_for_greeting', instructions)
        self.assertIn('Do not pretend to be Paul', instructions)
        self.assertIn('Do not routinely say that you have had a quick look through the details', instructions)
        self.assertIn('If photos are already attached', instructions)
        self.assertIn('Carpet cleaning, upholstery cleaning, rug cleaning and hard-floor cleaning are different services', instructions)
        self.assertFalse(captured['request']['store'])
        usage = self.appmod.q('SELECT * FROM ai_usage_log WHERE draft_id=?', (draft['id'],), one=True)
        self.assertEqual(usage['status'], 'Success')
        self.assertGreater(usage['estimated_cost_usd'], 0)

    def test_automatic_inbound_conversation_drafting_is_paused(self):
        with mock.patch.object(self.appmod, 'generate_ai_customer_reply', side_effect=AssertionError('Ongoing AI must not run')):
            draft, message = self.appmod.prepare_ai_draft_for_inbound_sms(self.customer_id)
        self.assertIsNone(draft)
        self.assertIn('Initial enquiry drafts only', message)

    def test_initial_reply_drops_early_access_and_address_request(self):
        context = {'recent_conversation': []}
        draft = (
            "Hi Jane, thank you for your enquiry. Would you be able to send over a few photos "
            "of the rooms please, and any address or access details when you're ready?"
        )
        polished = self.appmod.ai_polish_conversation_draft(draft, context)
        self.assertIn('photos of the rooms please?', polished)
        self.assertNotIn('address', polished.lower())
        self.assertNotIn('access', polished.lower())
        self.assertTrue(polished.endswith('Kind regards,\nThe Carpet Cleaning Company'))

    def test_submitted_enquiry_name_wins_over_customer_record_name(self):
        self.appmod.run("UPDATE customers SET first_name='Paul', last_name='Nicholas' WHERE id=?", (self.customer_id,))
        context, resolved_customer_id = self.appmod.ai_context_payload(self.customer_id, self.lead_id, 'SMS')
        self.assertEqual(resolved_customer_id, self.customer_id)
        self.assertEqual(context['customer_name_for_greeting'], 'Jane')
        self.assertEqual(context['customer']['first_name'], 'Paul')

    def test_current_intake_facts_are_isolated_from_stale_customer_notes(self):
        self.appmod.run(
            "UPDATE customers SET notes=? WHERE id=?",
            ('Created from an older enquiry.\nNumber of rooms: 2\nWhat cleaned: Upholstery cleaning', self.customer_id),
        )
        self.appmod.run(
            "UPDATE intake_submissions SET number_rooms='3', what_cleaned='Carpet cleaning' WHERE id=?",
            (self.lead_id,),
        )

        context, _ = self.appmod.ai_context_payload(self.customer_id, self.lead_id, 'SMS')

        self.assertEqual(context['original_enquiry']['number_rooms'], '3')
        self.assertEqual(context['original_enquiry']['what_cleaned'], 'Carpet cleaning')
        self.assertNotIn('notes', context['customer'])
        self.assertNotIn('Number of rooms: 2', json.dumps(context))
        self.assertEqual(context['context_scope']['current_intake_id'], self.lead_id)

    def test_conversation_before_current_intake_is_not_sent_to_ai(self):
        self.appmod.run(
            "UPDATE intake_submissions SET created_at='2026-08-15 12:00:00' WHERE id=?",
            (self.lead_id,),
        )
        self.appmod.run(
            "UPDATE communications SET body='Old job said two rooms', created_at='2026-08-14 12:00:00' WHERE customer_id=?",
            (self.customer_id,),
        )
        self.appmod.run(
            "INSERT INTO communications(customer_id,channel,subject,body,created_at) VALUES (?,'SMS','Inbound SMS','Current enquiry follow-up','2026-08-15 12:05:00')",
            (self.customer_id,),
        )

        context, _ = self.appmod.ai_context_payload(self.customer_id, self.lead_id, 'SMS')
        serialized = json.dumps(context)

        self.assertNotIn('Old job said two rooms', serialized)
        self.assertIn('Current enquiry follow-up', serialized)

    def test_missing_customer_name_is_not_invented(self):
        self.appmod.run("UPDATE intake_submissions SET name='' WHERE id=?", (self.lead_id,))
        self.appmod.run("UPDATE customers SET first_name='', last_name='' WHERE id=?", (self.customer_id,))
        context, _ = self.appmod.ai_context_payload(self.customer_id, self.lead_id, 'SMS')
        self.assertIsNone(context['customer_name_for_greeting'])

    def test_corrupted_calendar_test_name_is_not_used_for_greeting(self):
        self.appmod.run("UPDATE intake_submissions SET name='TEST Paul Nicholas Calendar Note' WHERE id=?", (self.lead_id,))
        context, _ = self.appmod.ai_context_payload(self.customer_id, self.lead_id, 'SMS')
        self.assertIsNone(context['customer_name_for_greeting'])

    def test_normal_customer_greeting_uses_first_name_only(self):
        context, _ = self.appmod.ai_context_payload(self.customer_id, self.lead_id, 'SMS')
        self.assertEqual(context['customer_name_for_greeting'], 'Jane')

    def test_follow_up_does_not_thank_for_enquiry_again(self):
        context = {
            'recent_conversation': [
                {'speaker': 'Business', 'body': 'Hi Jane, thank you very much for your enquiry.'},
                {'speaker': 'Customer', 'body': 'How much do you charge?'},
            ]
        }
        polished = self.appmod.ai_polish_conversation_draft(
            'Hi Jane, thank you very much for your enquiry. We have three cleaning options.',
            context,
        )
        self.assertEqual(polished, 'Hi Jane, we have three cleaning options.')
        self.assertNotIn('thank you', polished.lower())

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
        self.assertIn('/ai-drafts/owner-review/', email_send.call_args.args[2])
        self.assertIn('Review and send', email_send.call_args.args[3])
        self.assertIn('Nothing sent', sms_send.call_args.args[1])
        self.assertIn('Hi Jane, thank you for the details', sms_send.call_args.args[1])
        self.assertIn('/ai-drafts/owner-review/', sms_send.call_args.args[1])

    def test_private_owner_link_reviews_edits_and_sends_without_login(self):
        with mock.patch.object(self.appmod.urllib.request, 'urlopen', return_value=FakeOpenAIResponse(self.fake_payload())):
            draft = self.appmod.generate_ai_customer_reply(self.customer_id, self.lead_id, 'SMS')
        with self.app.test_request_context('/'):
            review_url = self.appmod.ai_owner_review_url(draft)
        path = review_url.split('http://localhost', 1)[-1]
        client = self.app.test_client()
        with mock.patch.object(self.appmod, 'send_clicksend_env_sms', return_value=(True, 'sent')) as sms_send:
            preview = client.get(path)
            self.assertEqual(preview.status_code, 200)
            sms_send.assert_not_called()
            sent = client.post(path, data={'action': 'send', 'subject': '', 'body': 'Edited from the private approval page'})
        self.assertEqual(sent.status_code, 200)
        sms_send.assert_called_once()
        self.assertEqual(sms_send.call_args.args[1], 'Edited from the private approval page')
        saved = self.appmod.q('SELECT status,body FROM ai_drafts WHERE id=?', (draft['id'],), one=True)
        self.assertEqual(saved['status'], 'Sent')
        self.assertEqual(saved['body'], 'Edited from the private approval page')

    def test_invalid_owner_link_cannot_review_or_send(self):
        response = self.app.test_client().get('/ai-drafts/owner-review/not-a-valid-token')
        self.assertEqual(response.status_code, 400)

    def test_sending_ai_sms_uses_render_clicksend_configuration(self):
        cur = self.appmod.db().execute(
            """INSERT INTO ai_drafts(customer_id,intake_id,channel,subject,body,status,created_at,updated_at)
               VALUES (?,?,'SMS','','A reviewed AI reply','Generated',datetime('now'),datetime('now'))""",
            (self.customer_id, self.lead_id),
        )
        draft_id = cur.lastrowid
        self.appmod.db().commit()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['logged_in'] = True

        with mock.patch.object(
            self.appmod,
            'send_clicksend_env_sms',
            return_value=(True, 'SMS accepted by ClickSend.'),
        ) as sms_send, mock.patch.object(
            self.appmod,
            'send_sms_gateway',
            side_effect=AssertionError('AI sends must use the Render/ClickSend configuration path'),
        ):
            response = client.post(
                f'/ai-drafts/{draft_id}/action',
                data={'action': 'send', 'body': 'A reviewed AI reply'},
            )

        self.assertEqual(response.status_code, 302)
        sms_send.assert_called_once()
        self.assertEqual(sms_send.call_args.args[0], '07800111222')
        saved = self.appmod.q('SELECT status FROM ai_drafts WHERE id=?', (draft_id,), one=True)
        self.assertEqual(saved['status'], 'Sent')

    def test_clicksend_inbound_reply_is_matched_and_prepares_ai_draft(self):
        client = self.app.test_client()
        payload = {
            'from': '+447800111222',
            'to': '+441743000000',
            'body': 'Yes please, a call tomorrow afternoon would be helpful.',
            'message_id': 'inbound-chantal-test-1',
        }
        with mock.patch.object(
            self.appmod,
            'prepare_ai_draft_for_inbound_sms',
            return_value=(mock.Mock(), 'prepared'),
        ) as prepare:
            response = client.post('/webhooks/sms/inbound/clicksend', json=payload)

        self.assertEqual(response.status_code, 200)
        prepare.assert_called_once_with(self.customer_id)
        inbound = self.appmod.q(
            "SELECT * FROM sms_events WHERE external_id='inbound-chantal-test-1'",
            one=True,
        )
        self.assertEqual(inbound['customer_id'], self.customer_id)
        self.assertEqual(inbound['direction'], 'inbound')
        communication = self.appmod.q(
            "SELECT * FROM communications WHERE customer_id=? AND subject='Inbound SMS' ORDER BY id DESC LIMIT 1",
            (self.customer_id,),
            one=True,
        )
        self.assertIn('call tomorrow afternoon', communication['body'])

    def test_duplicate_clicksend_webhook_does_not_create_two_drafts(self):
        client = self.app.test_client()
        payload = {
            'from': '+447800111222',
            'body': 'Duplicate delivery test',
            'message_id': 'inbound-duplicate-test-1',
        }
        with mock.patch.object(
            self.appmod,
            'prepare_ai_draft_for_inbound_sms',
            return_value=(mock.Mock(), 'prepared'),
        ) as prepare:
            self.assertEqual(client.post('/webhooks/sms/inbound/clicksend', json=payload).status_code, 200)
            self.assertEqual(client.post('/webhooks/sms/inbound/clicksend', json=payload).status_code, 200)

        prepare.assert_called_once_with(self.customer_id)

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
