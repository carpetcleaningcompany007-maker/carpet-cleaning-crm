import os
import tempfile
import unittest
from unittest.mock import patch


class JobLateNoticeTests(unittest.TestCase):
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
        self.job = self.mod.run('INSERT INTO jobs(customer_id,title,status) VALUES (?,?,?)', (customer, 'Test job', 'Booked'))
        self.client = self.mod.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True

    def tearDown(self):
        self.ctx.pop()
        self.tmp.cleanup()

    def test_short_and_long_delays_reach_both_message_channels(self):
        for minutes, label in [('10', '10 minutes'), ('20', '20 minutes'), ('30', '30 minutes'), ('60', '1 hour'), ('120', '2 hours'), ('bad', '10 minutes'), ('999', '10 minutes')]:
            with self.subTest(minutes=minutes), patch.object(self.mod, 'send_rendered_customer_message', return_value=(True, 'Sent', 'test')) as send:
                response = self.client.post(f'/jobs/{self.job}/send-late-notice', data={'minutes': minutes, 'channel': 'both'})
                self.assertEqual(response.status_code, 302)
                self.assertEqual([call.args[1] for call in send.call_args_list], ['sms', 'email'])
                for call in send.call_args_list:
                    self.assertEqual(call.args[2], f'Running about {label} late')
                    self.assertIn(f'running about {label} late.', call.args[3])

    def test_sample_does_not_send(self):
        self.mod.run('UPDATE jobs SET notes=? WHERE id=?', ('Dashboard walkthrough sample — safe to delete', self.job))
        with patch.object(self.mod, 'send_rendered_customer_message') as send:
            self.client.post(f'/jobs/{self.job}/send-late-notice', data={'minutes': '120', 'channel': 'both'})
            send.assert_not_called()

