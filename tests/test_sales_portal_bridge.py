import base64
import io
import json
import os
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
from flask import Flask
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from sales_portal_bridge import register_sales_portal, PATH

class SalesPortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        app = Flask(__name__)
        with patch('sales_portal_bridge.serialization.load_pem_public_key', return_value=self.key.public_key()):
            register_sales_portal(app, self.tmp.name + '/test.db', lambda: 'crm-configured-model')
        self.client = app.test_client()
        self.body = json.dumps({'instructions': 'Draft only', 'input': 'Fictional test', 'text': {'format': {'type': 'json_schema', 'name': 'sales_reply'}}, 'tools': [{'type': 'web_search'}], 'store': True, 'max_output_tokens': 999999}).encode()

    def tearDown(self):
        self.tmp.cleanup()

    def headers(self, body=None, stamp=None):
        stamp = str(stamp or int(time.time()))
        nonce = str(uuid.uuid4())
        signed = ('POST\n' + PATH + '\n' + stamp + '\n' + nonce + '\n').encode() + (body or self.body)
        sig = self.key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        return {'Content-Type': 'application/json', 'X-Portal-Time': stamp, 'X-Portal-Nonce': nonce, 'X-Portal-Signature': base64.b64encode(sig).decode()}

    def test_unsigned_tampered_and_expired_requests_are_rejected(self):
        self.assertEqual(self.client.post(PATH, data=self.body).status_code, 401)
        self.assertEqual(self.client.post(PATH, data=self.body+b' ', headers=self.headers()).status_code, 401)
        self.assertEqual(self.client.post(PATH, data=self.body, headers=self.headers(stamp=int(time.time())-300)).status_code, 401)

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only-key'})
    def test_signed_request_reuses_model_and_rejects_replay(self):
        headers = self.headers()
        with patch('sales_portal_bridge.urllib.request.urlopen', return_value=io.BytesIO(b'{"status":"completed","output":[]}')) as call:
            self.assertEqual(self.client.post(PATH, data=self.body, headers=headers).status_code, 200)
            payload = json.loads(call.call_args.args[0].data)
            self.assertEqual(payload['model'], 'crm-configured-model')
            self.assertFalse(payload['store'])
            self.assertNotIn('tools', payload)
            self.assertEqual(payload['max_output_tokens'], 1800)
            self.assertEqual(self.client.post(PATH, data=self.body, headers=headers).status_code, 409)
            self.assertEqual(call.call_count, 1)

    def test_large_request_is_rejected(self):
        self.assertEqual(self.client.post(PATH, data=b'x'*40001).status_code, 413)

if __name__ == '__main__':
    unittest.main()
