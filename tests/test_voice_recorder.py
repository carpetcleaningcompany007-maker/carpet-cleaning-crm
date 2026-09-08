import io,json,os,unittest
from unittest.mock import patch,MagicMock
import test_customer_conversation as fixture

class VoiceRecorderTests(unittest.TestCase):
    setUp=fixture.CustomerConversationTests.setUp
    tearDown=fixture.CustomerConversationTests.tearDown

    def test_microphone_on_default_ai_customer_and_message_screens(self):
        for path in ('/ai-assistant','/customers',f'/customers/{self.customer}/conversation','/invoices'):
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'aria-label="Record voice to type"',response.data)
            self.assertIn(b'voice-recorder.js',response.data)
        page=self.client.get('/ai-assistant')
        self.assertIn(b'Record voice instead of typing',page.data)
        self.assertNotIn(b'window.SpeechRecognition',page.data)

    def test_transcription_returns_text_without_saving_records(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps({'text':'Clean the lounge for seventy five pounds.'}).encode()
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(self.mod.urllib.request,'urlopen',return_value=response) as api:
            result=self.client.post('/api/voice/transcribe',data={'audio':(io.BytesIO(b'RIFFaudio-data'),'recording.wav')},content_type='multipart/form-data')
        self.assertEqual(result.status_code,200)
        self.assertIn('lounge',result.json['text'])
        req=api.call_args.args[0]
        self.assertEqual(req.full_url,'https://api.openai.com/v1/audio/transcriptions')
        self.assertIn(b'gpt-4o-mini-transcribe',req.data)
        self.assertIn(b'RIFFaudio-data',req.data)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM invoices',one=True)['n'],0)
        self.assertEqual(self.mod.q('SELECT count(*) n FROM communications',one=True)['n'],0)

    def test_bad_or_missing_audio_is_not_forwarded(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'test'}),patch.object(self.mod.urllib.request,'urlopen') as api:
            for data in ({},{'audio':(io.BytesIO(b'bad'),'file.html')},{'audio':(io.BytesIO(b''),'recording.wav')}):
                self.assertEqual(self.client.post('/api/voice/transcribe',data=data,content_type='multipart/form-data').status_code,400)
            api.assert_not_called()

    def test_transcription_requires_login_and_csrf(self):
        self.mod.app.config['TESTING']=False
        self.assertEqual(self.client.post('/api/voice/transcribe').status_code,400)
        self.mod.app.config['TESTING']=True
        self.assertEqual(self.mod.app.test_client().post('/api/voice/transcribe').status_code,302)
