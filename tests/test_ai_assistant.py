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

    def test_hub_lists_tools_without_stain_analyser(self):
        page=self.client.get('/ai-assistant')
        self.assertEqual(page.status_code,200)
        for text in (b'Build a quote',b'Write a customer reply',b'Tidy a job note',b'Analyse commercial leads',b'Nothing is ever sent'):
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

    def test_edited_voice_note_is_saved_and_empty_edit_is_not_replaced(self):
        result={'job_note':'Original AI note','suggested_status':'','suggested_amount':''}
        run_id=self.mod.run('INSERT INTO ai_assistant_runs(tool_key,job_id,result_json) VALUES (?,?,?)',('voice',self.job,json.dumps(result)))
        self.client.post(f'/ai-assistant/{run_id}/save-job-note',data={'job_note':''})
        self.assertNotIn('Original AI note',self.mod.q('SELECT * FROM jobs WHERE id=?',(self.job,),one=True)['notes'] or '')
        self.client.post(f'/ai-assistant/{run_id}/save-job-note',data={'job_note':'My corrected job note'})
        note=self.mod.q('SELECT * FROM jobs WHERE id=?',(self.job,),one=True)['notes']
        self.assertIn('My corrected job note',note)
        self.assertNotIn('Original AI note',note)

    def test_history_reopens_saved_result_without_running_ai(self):
        result={'title':'Previous result','summary':'Saved summary','sections':[],'warning':'','job_note':''}
        run_id=self.mod.run('INSERT INTO ai_assistant_runs(tool_key,result_json) VALUES (?,?)',('reply',json.dumps(result)))
        with patch.object(self.mod,'run_ai_assistant') as generate:
            page=self.client.get(f'/ai-assistant?run={run_id}')
            generate.assert_not_called()
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Saved summary',page.data)
        self.assertIn(b'data-ai-preview',page.data)

    def test_invoice_review_saves_edits_once_without_sending(self):
        invoice={'customer_name':'Test Customer','invoice_date':'2026-09-08','due_date':'2026-09-15','vat':'','notes':'','lines':[{'description':'Lounge','quantity':'1','unit_price':'75'}]}
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(self.mod.urllib.request,'urlopen',return_value=self.ai_response(invoice=invoice)):
            page=self.client.post('/ai-assistant',data={'tool_key':'invoice','notes':'Invoice Test Customer for lounge cleaning at 75 pounds'})
        self.assertIn(b'Save draft invoice',page.data)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM invoices',one=True)['n'],0)
        rid=self.mod.q('SELECT id FROM ai_assistant_runs',one=True)['id']
        data={'customer_id':str(self.customer),'invoice_date':'2026-09-08','due_date':'2026-09-15','vat':'0','invoice_notes':'Reviewed','description':'Edited lounge clean','quantity':'2','unit_price':'80'}
        with patch.object(self.mod,'send_env_email') as email,patch.object(self.mod,'send_clicksend_env_sms') as sms:
            saved=self.client.post(f'/ai-assistant/{rid}/save-invoice',data=data)
            repeated=self.client.post(f'/ai-assistant/{rid}/save-invoice',data=data)
            email.assert_not_called();sms.assert_not_called()
        self.assertEqual(saved.location,repeated.location)
        row=self.mod.q('SELECT * FROM invoices',one=True)
        self.assertEqual(row['status'],'Draft');self.assertEqual(row['total'],160)
        self.assertIn('Edited lounge clean',row['payload_json'])
        self.assertEqual(self.mod.q('SELECT count(*) n FROM invoices',one=True)['n'],1)
        self.assertEqual(self.client.get(saved.location).status_code,200)

    def test_invoice_unknown_vat_and_invalid_amount_do_not_save(self):
        rid=self.mod.run('INSERT INTO ai_assistant_runs(tool_key,result_json) VALUES (?,?)',('invoice',json.dumps({'invoice':{}})))
        data={'customer_id':str(self.customer),'invoice_date':'2026-09-08','due_date':'2026-09-15','vat':'','description':'Keep my edited item','quantity':'1','unit_price':'80'}
        for field,value in [('vat',''),('unit_price','NaN'),('quantity','-1')]:
            invalid={**data,'vat':'0',field:value}
            page=self.client.post(f'/ai-assistant/{rid}/save-invoice',data=invalid)
            self.assertEqual(page.status_code,400)
            self.assertIn(b'Keep my edited item',page.data)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM invoices',one=True)['n'],0)

    def test_writing_assistance_is_preview_only_for_email_and_text(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(self.mod.urllib.request,'urlopen',return_value=self.ai_response(title='Your clean',sections=[{'heading':'Draft','body':'Improved wording'}])) as api,patch.object(self.mod,'send_env_email') as email,patch.object(self.mod,'send_clicksend_env_sms') as sms:
            for channel in ('Email','Text'):
                response=self.client.post(f'/customers/{self.customer}/writing-assistance',data={'channel':channel,'body':'Original dictation','subject':'Your clean','writing_action':'check'})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json['body'],'Improved wording')
                payload=json.loads(api.call_args.args[0].data)
                self.assertIn('Original dictation',payload['input'])
                self.assertIn('Correct spelling',payload['instructions'])
            email.assert_not_called();sms.assert_not_called()
        self.assertEqual(self.mod.q('SELECT count(*) n FROM communications',one=True)['n'],0)
        response=self.client.post(f'/customers/{self.customer}/writing-assistance',data={'channel':'Email','body':'','writing_action':'check'})
        self.assertEqual(response.status_code,400)

    def test_voice_pages_allow_microphone_but_keep_login_required(self):
        for url in ('/customers','/ai-assistant',f'/customers/{self.customer}/conversation'):
            self.assertIn('microphone=(self)',self.client.get(url).headers['Permissions-Policy'])
        self.assertEqual(self.mod.app.test_client().get('/ai-assistant').status_code,302)


if __name__=='__main__':unittest.main()
