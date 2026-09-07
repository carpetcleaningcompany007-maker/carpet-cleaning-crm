import json, os, unittest
from unittest.mock import patch, MagicMock
import test_customer_conversation as fixture

class ConversationAITests(unittest.TestCase):
    setUp=fixture.CustomerConversationTests.setUp
    tearDown=fixture.CustomerConversationTests.tearDown

    def response(self, body='Thanks, I can explain the options.'):
        reply=MagicMock()
        reply.__enter__.return_value.read.return_value=json.dumps({'output_text':json.dumps({'subject':'Your enquiry','body':body,'needs_manual_response':False,'manual_reason':''}),'usage':{}}).encode()
        return reply

    def test_draft_uses_history_and_never_sends(self):
        mod=self.mod
        mod.ai_settings_row()
        mod.run('UPDATE ai_settings SET enabled=1 WHERE id=1')
        mod.log_sms_event(self.customer,None,'Test','inbound','','','Could you explain the options?',direction='inbound')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(mod.urllib.request,'urlopen',return_value=self.response()) as api,patch.object(mod,'send_env_email') as email,patch.object(mod,'send_clicksend_env_sms') as sms:
            draft=mod.generate_ai_customer_reply(self.customer,channel='EMAIL',conversation_mode=True)
            email.assert_not_called();sms.assert_not_called()
        payload=json.loads(api.call_args.args[0].data)
        self.assertIn('UNTRUSTED DATA',payload['instructions'])
        context=json.loads(draft['source_context_json'])
        self.assertEqual(context['draft_mode'],'conversation')
        self.assertEqual(context['recent_conversation'][0]['body'],'Could you explain the options?')
        self.assertEqual(mod.conversation_ai_draft(self.customer)['id'],draft['id'])
        page=self.client.get(f'/customers/{self.customer}/conversation')
        self.assertIn(b'Thanks, I can explain the options.',page.data)
        with patch.object(mod,'_send_env_email',return_value=(True,'Sent')):
            self.client.post(f'/customers/{self.customer}/conversation',data={'ai_draft_id':draft['id'],'channel':'Email','subject':'My subject','body':'My edited reply'})
        saved=mod.q('SELECT * FROM ai_drafts WHERE id=?',(draft['id'],),one=True)
        self.assertEqual(saved['body'],'My edited reply');self.assertEqual(saved['status'],'Sent')

    def test_long_draft_becomes_email_and_dismiss_is_persistent(self):
        mod=self.mod;mod.ai_settings_row();mod.run('UPDATE ai_settings SET enabled=1 WHERE id=1')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(mod.urllib.request,'urlopen',return_value=self.response('Long reply ’ '*100)):
            draft=mod.generate_ai_customer_reply(self.customer,channel='SMS',conversation_mode=True)
        self.assertEqual(draft['channel'],'EMAIL')
        self.client.post(f'/customers/{self.customer}/conversation/draft',data={'action':'dismiss'})
        self.assertIsNone(mod.conversation_ai_draft(self.customer))
        self.assertEqual(mod.q('SELECT status FROM ai_drafts WHERE id=?',(draft['id'],),one=True)['status'],'Discarded')

    def test_background_prepares_once_without_sending(self):
        mod=self.mod;mod.ai_settings_row();mod.run('UPDATE ai_settings SET enabled=1 WHERE id=1')
        mod.log_sms_event(self.customer,None,'Test','inbound','','','Can you help?',direction='inbound')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(mod.urllib.request,'urlopen',return_value=self.response()) as api,patch.object(mod,'send_env_email') as email,patch.object(mod,'send_clicksend_env_sms') as sms:
            mod.prepare_conversation_suggestions();mod.prepare_conversation_suggestions()
            self.assertEqual(api.call_count,1)
            email.assert_not_called();sms.assert_not_called()
