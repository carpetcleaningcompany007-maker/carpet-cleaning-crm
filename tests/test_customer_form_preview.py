import os
import tempfile
import unittest
from unittest.mock import patch


class CustomerFormPreviewTests(unittest.TestCase):
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

    def test_preview_is_read_only_and_uses_current_fields(self):
        before = self.mod.db().total_changes
        with patch.object(self.mod, 'send_env_email') as email, patch.object(self.mod, 'send_clicksend_env_sms') as sms, patch.object(self.mod, 'send_owner_customer_message_copy') as owner:
            response = self.client.post(f'/customers/{self.customer}/send-contact-form', data={'preview':'1','name':'Edited Name','email':'edited@example.invalid','preferred_date':'2026-10-20','agreed_quote_price':'125'})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn('Hi Edited Name', data['email_body'])
            self.assertIn('preferred_date=2026-10-20', data['sms_body'])
            self.assertEqual(data['email_to'], 'edited@example.invalid')
            self.assertIn('<a href=', data['email_html'])
            email.assert_not_called(); sms.assert_not_called(); owner.assert_not_called()
            self.assertEqual(before, self.mod.db().total_changes)

    def test_edits_match_delivery_and_do_not_change_defaults(self):
        payload = {'email_subject':'Temporary subject','email_body':'Email only <script>alert(1)</script>','sms_body':'Text only','send_email':'1','send_sms':'1'}
        url = f'/customers/{self.customer}/send-contact-form'
        preview = self.client.post(url, data=dict(payload, preview='1')).get_json()
        self.assertNotIn('<script>', preview['email_html'])
        with patch.object(self.mod, 'CUSTOMER_FORM_SENDING_PAUSED', False), patch.object(self.mod, 'send_env_email', return_value=(True,'Sent')) as email, patch.object(self.mod, 'send_clicksend_env_sms', return_value=(True,'Sent')) as sms, patch.object(self.mod, 'send_owner_customer_message_copy'):
            self.assertEqual(self.client.post(url,data=payload).status_code,302)
            self.assertEqual(email.call_args.args[1:4], (preview['subject'],preview['email_text'],preview['email_html']))
            self.assertEqual(sms.call_args.args[1],preview['sms_body'])
        fresh = self.client.post(url,data={'preview':'1'}).get_json()
        self.assertNotEqual(fresh['subject'],payload['email_subject'])
        self.assertIn('Please fill in this quick contact form',fresh['sms_body'])
        self.assertIn('Please fill in this quick contact form',fresh['email_body'])

    def test_preview_requires_login(self):
        with self.client.session_transaction() as session:
            session.clear()
        response = self.client.post(f'/customers/{self.customer}/send-contact-form',data={'preview':'1'})
        self.assertEqual(response.status_code,302)

    def test_saved_footer_is_previewed_once_and_not_appended_again(self):
        self.mod.run("UPDATE settings SET email_footer_html=? WHERE id=1", ('<p>Kind regards<br>Paul<br>07802 563213</p>',))
        url=f'/customers/{self.customer}/send-contact-form'
        data={'send_email':'1','email_body':'Please complete your form.\n\nKind regards,\nPaul\nThe Carpet Cleaning Company'}
        preview=self.client.post(url,data=dict(data,preview='1')).get_json()
        self.assertEqual(preview['email_html'].count('Kind regards'),1)
        self.assertEqual(preview['email_text'].count('Kind regards'),1)
        self.assertNotIn('Kind regards',preview['email_body'])
        with patch.object(self.mod,'CUSTOMER_FORM_SENDING_PAUSED',False), patch.object(self.mod,'send_env_email',return_value=(True,'Sent')) as email, patch.object(self.mod,'send_owner_customer_message_copy'):
            self.client.post(url,data=data)
            self.assertFalse(email.call_args.kwargs['append_footer'])
            self.assertEqual(email.call_args.args[3],preview['email_html'])
