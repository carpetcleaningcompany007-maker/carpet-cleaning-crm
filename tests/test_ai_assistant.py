import json,os,unittest
from unittest.mock import MagicMock,patch
import test_customer_conversation as fixture


class AiAssistantTests(unittest.TestCase):
    def setUp(self):
        fixture.CustomerConversationTests.setUp(self)
        self.mod.app.config['UPLOAD_FOLDER']=os.path.join(self.tmp.name,'uploads')

    tearDown=fixture.CustomerConversationTests.tearDown

    def ai_response(self,**overrides):
        result={'title':'Useful result','summary':'Summary for Paul','sections':[{'heading':'Next step','body':'Check this draft.'}],'warning':'','job_note':'','suggested_status':'','suggested_amount':'','suggested_payment_method':''}
        result.update(overrides)
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'output_text':json.dumps(result),'usage':{'input_tokens':10,'output_tokens':10}}).encode()
        return response

    def test_hub_lists_nine_tools_without_stain_analyser(self):
        page=self.client.get('/ai-assistant')
        self.assertEqual(page.status_code,200)
        for text in (b'Build a quote',b'Write a customer reply',b'Voice job entry',b'Analyse commercial leads',b'Nothing is ever sent'):
            self.assertIn(text,page.data)
        self.assertNotIn(b'Stain analysis',page.data)

    def test_reply_is_draft_only_and_sends_nothing(self):
        before=self.mod.q('SELECT count(*) n FROM communications',one=True)['n']
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(self.mod.urllib.request,'urlopen',return_value=self.ai_response()):
            page=self.client.post('/ai-assistant',data={'tool_key':'reply','customer_id':str(self.customer),'notes':'Reply briefly'})
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Draft only',page.data)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM communications',one=True)['n'],before)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM ai_assistant_runs',one=True)['n'],1)

    def test_every_tool_runs_as_a_private_result(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(self.mod.urllib.request,'urlopen',side_effect=lambda *args,**kwargs:self.ai_response()):
            for tool_key in self.mod.AI_ASSISTANT_TOOLS:
                page=self.client.post('/ai-assistant',data={'tool_key':tool_key,'notes':'Test privately'})
                self.assertEqual(page.status_code,200,tool_key)
                self.assertIn(b'Draft only',page.data,tool_key)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM ai_assistant_runs',one=True)['n'],len(self.mod.AI_ASSISTANT_TOOLS))

    def test_voice_update_changes_nothing_until_confirmed(self):
        job_id=self.mod.run("INSERT INTO jobs(customer_id,title,status,amount,notes) VALUES (?,?, 'Booked',0,'')",(self.customer,'Carpet clean'))
        response=self.ai_response(job_note='Clean completed and customer paid cash.',suggested_status='Completed',suggested_amount='165',suggested_payment_method='Cash')
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(self.mod.urllib.request,'urlopen',return_value=response):
            page=self.client.post('/ai-assistant',data={'tool_key':'voice','job_id':str(job_id),'notes':'Job completed, 165 paid cash'})
        self.assertEqual(page.status_code,200)
        job=self.mod.q('SELECT * FROM jobs WHERE id=?',(job_id,),one=True)
        self.assertEqual(job['status'],'Booked');self.assertEqual(job['amount'],0)
        run_id=self.mod.q('SELECT id FROM ai_assistant_runs',one=True)['id']
        saved=self.client.post(f'/ai-assistant/{run_id}/save-job-note',data={'apply_suggestions':'1'})
        self.assertEqual(saved.status_code,302)
        job=self.mod.q('SELECT * FROM jobs WHERE id=?',(job_id,),one=True)
        self.assertEqual(job['status'],'Completed');self.assertEqual(job['amount'],165)
        self.assertIn('paid cash',job['notes'])


if __name__=='__main__':unittest.main()
