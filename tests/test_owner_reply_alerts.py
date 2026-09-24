import json
import os
from email.message import EmailMessage
from unittest.mock import patch
import unittest
import test_customer_conversation as fixture


class OwnerReplyAlertTests(unittest.TestCase):
    def setUp(self):
        fixture.CustomerConversationTests.setUp(self)
        env = patch.dict(os.environ, {'OWNER_ALERT_EMAIL':'personal@example.invalid',
            'OWNER_ALERT_MOBILE':'07700900999', 'SMTP_USER':'business@example.invalid'})
        env.start(); self.addCleanup(env.stop)
        inbox = patch.object(self.mod, 'inbound_email_config',return_value=('business@example.invalid',''))
        inbox.start(); self.addCleanup(inbox.stop)
        email = patch.object(self.mod,'send_env_email',return_value=(True,'Accepted'))
        self.email = email.start(); self.addCleanup(email.stop)
        sms = patch.object(self.mod,'send_clicksend_env_sms',return_value=(True,'Accepted'))
        self.sms = sms.start(); self.addCleanup(sms.stop)
        links = patch.object(self.mod,'crm_external_url',return_value='https://crm.example.invalid/conversation')
        links.start(); self.addCleanup(links.stop)

    tearDown = fixture.CustomerConversationTests.tearDown

    def test_actual_reply_sent_only_to_both_owner_emails_and_owner_mobile(self):
        before=dict(self.mod.settings())
        result=self.mod.notify_owner_customer_reply(self.customer,'Can you help <today>?',sender='07700900123')
        self.assertEqual(set(result),{'personal@example.invalid','business@example.invalid','+447700900999'})
        self.assertEqual(self.email.call_count,2)
        for call in self.email.call_args_list:
            self.assertIn('Can you help <today>?',call.args[2])
            self.assertIn('Can you help &lt;today&gt;?',call.args[3])
            self.assertFalse(call.kwargs['record_customer_event'])
        self.sms.assert_called_once()
        self.assertEqual(self.sms.call_args.args[0],'+447700900999')
        self.assertIn('Can you help <today>?',self.sms.call_args.args[1])
        self.assertIsNone(self.sms.call_args.kwargs['customer'])
        self.assertEqual(dict(self.mod.settings()),before)
        saved=self.mod.q('SELECT results_json FROM owner_reply_alert_log',one=True)
        self.assertEqual(set(json.loads(saved['results_json'])),set(result))

    def test_one_email_failure_does_not_prevent_other_email_or_sms(self):
        self.email.side_effect=[RuntimeError('Unavailable'),(True,'Accepted')]
        result=self.mod.notify_owner_customer_reply(self.customer,'Can you help?')
        self.assertFalse(result['personal@example.invalid']['accepted'])
        self.assertTrue(result['business@example.invalid']['accepted'])
        self.sms.assert_called_once()

    def test_duplicate_webhook_and_email_relay_alert_once(self):
        self.mod.notify_owner_customer_reply(self.customer,'Can you help?')
        mail=EmailMessage()
        mail['From']='+447700900123@sms.clicksend.com'
        mail['To']='business@example.invalid'
        mail['Message-ID']='<relay-test@example.invalid>'
        mail.set_content("You've received a reply from +447700900123:\nCan you help?\n\n-------------------\n\nOriginal Message on Monday:\nFirst message")
        with patch.object(self.mod,'prepare_ai_draft_for_inbound_sms'),patch.object(self.mod,'prepare_quote_draft_from_sms_reply'):
            self.mod.ingest_inbound_message(mail.as_bytes())
        self.assertEqual(self.email.call_count,2)
        self.sms.assert_called_once()
        self.assertEqual(len(self.mod.q('SELECT * FROM owner_reply_alert_log')),1)

    def test_later_identical_reply_is_not_silenced(self):
        self.mod.notify_owner_customer_reply(self.customer,'Hello')
        self.mod.run("UPDATE owner_reply_alert_log SET created_at=datetime('now','-11 minutes')")
        self.mod.notify_owner_customer_reply(self.customer,'Hello')
        self.assertEqual(self.email.call_count,4)

    def test_repeated_real_sms_is_not_mistaken_for_a_relay_duplicate(self):
        self.mod.notify_owner_customer_reply(self.customer,'Hello')
        self.mod.notify_owner_customer_reply(self.customer,'Hello')
        self.assertEqual(self.email.call_count,4)

    def test_webhook_alerts_even_when_ai_disabled_and_duplicate_id_is_ignored(self):
        self.mod.ai_settings_row()
        self.mod.run('UPDATE ai_settings SET enabled=0 WHERE id=1')
        with patch.object(self.mod,'clicksend_webhook_authorized',return_value=True),patch.object(self.mod,'generate_ai_customer_reply') as ai:
            for _ in range(2):
                response=self.client.post('/webhooks/sms/inbound/clicksend',json={
                    'from':'07700900123','body':'Is this something you could help with?','message_id':'inbound-test'})
                self.assertEqual(response.status_code,200)
            ai.assert_not_called()
        self.assertEqual(self.email.call_count,2)
        self.sms.assert_called_once()
        self.assertEqual(self.sms.call_args.args[0],'+447700900999')

    def test_regular_customer_email_alerts_both_inboxes(self):
        mail=EmailMessage();mail['From']='test@example.invalid';mail['To']='business@example.invalid'
        mail['Message-ID']='<regular-reply@example.invalid>';mail.set_content('Can you clean the sofa?')
        self.mod.ingest_inbound_message(mail.as_bytes())
        self.assertEqual(self.email.call_count,2)
        self.assertIn('Can you clean the sofa?',self.email.call_args.args[2])

    def test_routing_status_requires_login_is_uncached_and_never_sends(self):
        result=self.client.get('/automation/owner-reply-alerts')
        self.assertEqual(result.json['emails'],['personal@example.invalid','business@example.invalid'])
        self.assertFalse(result.json['sends_customer_reply'])
        self.assertIn('no-store',result.headers['Cache-Control'])
        self.assertEqual(self.mod.app.test_client().get('/automation/owner-reply-alerts').status_code,302)
        self.email.assert_not_called();self.sms.assert_not_called()

    def test_same_owner_and_business_email_is_deduplicated(self):
        with patch.dict(os.environ,{'OWNER_ALERT_EMAIL':'BUSINESS@example.invalid'}):
            self.mod.notify_owner_customer_reply(self.customer,'Hello')
        self.email.assert_called_once()

    def test_business_sender_included_when_reply_sync_uses_personal_inbox(self):
        with patch.object(self.mod,'inbound_email_config',return_value=('personal@example.invalid','')):
            emails,_=self.mod.owner_contact_form_recipients()
        self.assertEqual(self.mod.parse_email_list(emails),['personal@example.invalid','business@example.invalid'])

    def test_unknown_sms_still_alerts_owner(self):
        self.mod.notify_owner_customer_reply(None,'Unknown customer reply',sender='07700900456')
        self.assertEqual(self.email.call_count,2)
        self.sms.assert_called_once()
