import unittest
from unittest.mock import patch
import test_website_form as fixture


class FormspreeDisabledTests(unittest.TestCase):
    setUp = fixture.WebsiteFormTests.setUp
    tearDown = fixture.WebsiteFormTests.tearDown
    post_form = fixture.WebsiteFormTests.post_form

    def test_website_enquiry_never_forwards_to_formspree(self):
        with patch.object(self.appmod, 'forward_website_form_to_formspree',
                          side_effect=AssertionError('Formspree is not authorised')) as forward:
            response = self.post_form()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ok'])
        self.assertNotIn('formspree', response.get_json()['automation'])
        forward.assert_not_called()
