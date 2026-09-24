import os
import unittest
from unittest.mock import patch
import test_website_form as fixture


class FormspreeCopyTests(unittest.TestCase):
    setUp = fixture.WebsiteFormTests.setUp
    tearDown = fixture.WebsiteFormTests.tearDown
    post_form = fixture.WebsiteFormTests.post_form

    def test_submission_saved_and_copy_references_same_crm_records(self):
        with patch.object(self.appmod, 'forward_website_form_to_formspree',
                          return_value=(True, 'Accepted')) as forward:
            response = self.post_form()
        result = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(result['automation']['formspree']['ok'])
        self.assertEqual(forward.call_args.kwargs,
                         {'lead_id': result['lead_id'], 'customer_id': result['customer_id']})

    def test_failure_keeps_enquiry_and_records_problem(self):
        with patch.object(self.appmod, 'http_post_form', side_effect=TimeoutError('Timed out')):
            response = self.post_form()
        result = response.get_json()
        self.assertTrue(result['ok'])
        self.assertFalse(result['automation']['formspree']['ok'])
        lead = self.appmod.q('SELECT id FROM intake_submissions WHERE id=?', (result['lead_id'],), one=True)
        self.assertIsNotNone(lead)
        note = self.appmod.q("SELECT note_text FROM customer_timeline WHERE customer_id=? AND note_text LIKE 'Formspree%'", (result['customer_id'],), one=True)
        self.assertIn('Timed out', note['note_text'])

    def test_provider_must_confirm_acceptance(self):
        for body, accepted in [('{"ok":true}', True), ('{"ok":false}', False), ('<html>captcha</html>', False)]:
            with self.subTest(body=body), patch.object(self.appmod, 'http_post_form', return_value=body):
                ok, detail = self.appmod.forward_website_form_to_formspree({'name': 'Test'}, 7, 8)
            self.assertEqual(ok, accepted)

    def test_explicit_disable_does_not_contact_formspree(self):
        with patch.dict(os.environ, {'WEBSITE_FORMSPREE_ENDPOINT': ''}), patch.object(self.appmod, 'http_post_form') as post:
            ok, detail = self.appmod.forward_website_form_to_formspree({'name': 'Test'})
        self.assertFalse(ok)
        post.assert_not_called()
