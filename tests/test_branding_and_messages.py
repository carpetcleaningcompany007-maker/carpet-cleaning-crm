import os
import tempfile
import unittest
from unittest.mock import patch


class BrandingAndMessagesTests(unittest.TestCase):
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
        self.mod.app.config['UPLOAD_FOLDER']=os.path.join(self.tmp.name,'uploads')
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

    def test_logo_upload_is_publicly_viewable_and_used_in_email(self):
        import io
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(32,32),'blue').save(image,'PNG');image.seek(0)
        response=self.client.post('/branding',data={'business_name':'Another Company','logo_file':(image,'logo.png')},content_type='multipart/form-data')
        self.assertEqual(response.status_code,302)
        path=self.mod.company_logo_path()
        self.assertIn('/branding/logo/brand-',path)
        html=self.mod.customer_form_preview_html('Hello')
        self.assertIn(path,html)
        self.assertIn('Another Company',html)
        public=self.mod.app.test_client()
        response=public.get(path)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.mimetype,'image/png')
        response.close()
        self.assertEqual(public.get('/branding').status_code,302)
        self.assertEqual(public.get('/branding/logo/crm.db').status_code,404)

    def test_invalid_upload_keeps_existing_logo(self):
        import io
        before=self.mod.company_logo_path()
        response=self.client.post('/branding',data={'logo_file':(io.BytesIO(b'<script>alert(1)</script>'),'logo.png')},content_type='multipart/form-data')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'valid PNG',response.data)
        self.assertEqual(before,self.mod.company_logo_path())

    def test_message_directory_search_and_default_logo(self):
        response=self.client.get('/customer-messages?search=Test')
        self.assertEqual(response.status_code,200)
        self.assertIn(f'/customers/{self.customer}/conversation'.encode(),response.data)
        self.assertIn(b'/static/site/carpet-pro-technician-logo.png',response.data)
        self.assertIn(b'Carpet Pro Cleaning',response.data)
        self.assertNotIn(b'History &amp; message',self.client.get('/customer-messages?search=absent').data)
        dashboard=self.client.get('/dashboard').data
        self.assertNotIn(b'class="message-customer-action"',dashboard)

    def test_dashboard_scopes_history_and_completes_next_job(self):
        mod=self.mod
        mod.run('UPDATE jobs SET job_date=?,job_time=? WHERE id=?',(mod.uk_today().isoformat(),'09:00',self.job))
        other=mod.run("INSERT INTO customers(first_name,last_name) VALUES ('Unscheduled','Customer')")
        mod.log_sms_event(self.customer,None,'Test','inbound','','','Scheduled message',direction='inbound')
        mod.log_sms_event(other,None,'Test','inbound','','','Unscheduled private message',direction='inbound')
        page=self.client.get('/dashboard').data
        self.assertIn(f'/customers/{self.customer}/conversation#sms-'.encode(),page)
        self.assertEqual([r['body'] for r in mod.customer_conversation_rows(today_only=True,scheduled_today=True)],['Scheduled message'])
        self.assertNotIn(b'Unscheduled private message',page)
        self.assertIn(b'On my way',page)
        self.assertIn(b'2 hours',page)
        self.client.post(f'/dashboard/job/{self.job}/note',data={'note':'Side gate open'})
        self.assertIn(b'Side gate open',self.client.get('/dashboard').data)
        self.assertEqual(mod.q('SELECT status FROM jobs WHERE id=?',(self.job,),one=True)['status'],'Booked')
        self.client.post(f'/today-run/job/{self.job}/complete',data={'work_carried_out':'Cleaned lounge','outcome':'Completed as planned','next_url':'/dashboard'})
        page=self.client.get('/dashboard').data
        self.assertIn(b'Finished today (1)',page)
        self.assertNotIn(b'Finish &amp; show next job',page)

    def test_dashboard_message_is_editable_draft_without_send(self):
        with patch.object(self.mod,'send_clicksend_env_sms') as send:
            response=self.client.get(f'/customers/{self.customer}/conversation?job={self.job}&action=late&minutes=120')
        send.assert_not_called()
        self.assertIn(b'2 hours late',response.data)
        self.assertIn(b'class="conversation-compose" open',response.data)
        self.assertIn(b'>Cancel</a>',response.data)
        self.assertEqual(self.mod.customer_conversation_rows(self.customer),[])
