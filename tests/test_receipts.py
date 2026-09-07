import io,os,unittest,json
from unittest.mock import patch,MagicMock
from PIL import Image
import test_customer_conversation as fixture

class ReceiptTests(unittest.TestCase):
    def setUp(self):
        fixture.CustomerConversationTests.setUp(self)
        self.mod.app.config['UPLOAD_FOLDER']=os.path.join(self.tmp.name,'uploads')
    tearDown=fixture.CustomerConversationTests.tearDown
    def picture(self):
        data=io.BytesIO();Image.new('RGB',(20,20),'white').save(data,'PNG');data.seek(0);return data
    def upload(self):
        with patch.object(self.mod,'receipt_ai_extract',side_effect=RuntimeError('Enter details manually.')):
            return self.client.post('/receipts',data={'receipt':(self.picture(),'receipt.png')},content_type='multipart/form-data')
    def test_upload_duplicate_review_save_and_private_original(self):
        first=self.upload();self.assertEqual(first.status_code,302)
        second=self.upload();self.assertEqual(first.location.split('?')[0],second.location)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM purchase_receipts',one=True)['n'],1)
        rid=self.mod.q('SELECT id FROM purchase_receipts',one=True)['id']
        self.assertEqual(self.mod.q('SELECT count(*) n FROM expenses',one=True)['n'],0)
        self.assertEqual(self.mod.app.test_client().get(f'/receipts/{rid}/file').status_code,302)
        original=self.client.get(f'/receipts/{rid}/file');self.assertEqual(original.mimetype,'image/png');original.close()
        values={'supplier':'Cleaning supplier','receipt_date':'2026-09-07','items':'Detergent','total':'24.00','vat':'4.00','currency':'GBP','notes':'Checked'}
        self.client.post(first.location,data=values);self.client.post(first.location,data=values)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM expenses',one=True)['n'],1)
    def test_uncertain_amount_is_not_saved_and_invalid_file_rejected(self):
        url=self.upload().location
        response=self.client.post(url,data={'supplier':'Shop','receipt_date':'2026-09-07','total':'NaN','vat':'','currency':'GBP'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM expenses',one=True)['n'],0)
        self.client.post('/receipts',data={'receipt':(io.BytesIO(b'<html>bad</html>'),'receipt.jpg')},content_type='multipart/form-data')
        self.assertEqual(self.mod.q('SELECT count(*) n FROM purchase_receipts',one=True)['n'],1)
    def test_ai_extraction_keeps_unknowns_for_review(self):
        self.upload();receipt=self.mod.q('SELECT * FROM purchase_receipts',one=True)
        result=dict(supplier='Shop',receipt_date='',items='Cleaning liquid',total='12.00',vat='',currency='GBP',notes='Date and VAT unreadable')
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'output_text':json.dumps(result)}).encode()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(self.mod.urllib.request,'urlopen',return_value=response) as api:
            self.mod.receipt_ai_extract(receipt)
        payload=json.loads(api.call_args.args[0].data)
        self.assertFalse(payload['store']);self.assertEqual(payload['input'][0]['content'][1]['type'],'input_image')
        saved=self.mod.q('SELECT * FROM purchase_receipts',one=True)
        self.assertEqual(saved['vat'],'');self.assertEqual(saved['status'],'Needs review')
        self.assertIsNone(saved['expense_id'])

    def test_upload_returns_before_ai_and_export_is_safe(self):
        with patch.object(self.mod,'receipt_ai_extract') as ai:
            result=self.client.post('/receipts',data={'receipt':(self.picture(),'receipt.png')},content_type='multipart/form-data')
            self.assertEqual(result.status_code,302);ai.assert_not_called()
        self.mod.run("UPDATE purchase_receipts SET supplier='=FORMULA',total='20' ")
        result=self.client.get('/receipts/export.csv')
        self.assertEqual(result.status_code,200)
        self.assertIn(b"'=FORMULA",result.data)
        self.assertIn(b'Original file',result.data)

    def test_iphone_receipt_upload_is_accepted(self):
        from pillow_heif import register_heif_opener
        register_heif_opener()
        data=io.BytesIO();Image.new('RGB',(64,64),'white').save(data,format='HEIF');data.seek(0)
        result=self.client.post('/receipts',data={'receipt':(data,'IMG_001.HEIC')},content_type='multipart/form-data')
        self.assertEqual(result.status_code,302)
        self.assertEqual(self.mod.q('SELECT mime_type FROM purchase_receipts',one=True)['mime_type'],'image/heic')

    def test_background_failure_keeps_original_and_shows_retry(self):
        self.upload();receipt=self.mod.q('SELECT * FROM purchase_receipts',one=True)
        self.mod.run("UPDATE purchase_receipts SET status='Reading' WHERE id=?",(receipt['id'],))
        with patch.object(self.mod,'receipt_ai_extract',side_effect=RuntimeError('AI connection unavailable')):
            self.mod.read_receipt_background(receipt['id'])
        updated=self.mod.q('SELECT * FROM purchase_receipts',one=True)
        self.assertEqual(updated['status'],'Needs review')
        self.assertEqual(updated['ai_error'],'AI connection unavailable')
        self.assertTrue(os.path.isfile(os.path.join(self.mod.app.config['UPLOAD_FOLDER'],'receipts',updated['filename'])))
