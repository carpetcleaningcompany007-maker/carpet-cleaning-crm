import json
from unittest import mock
import unittest
import test_customer_xero_exports as fixture


class XeroQuoteTests(unittest.TestCase):
    tearDown = fixture.CustomerXeroExportTests.tearDown
    # Reuse only setup; unittest also exercises existing contact integration tests.
    def setUp(self):
        fixture.CustomerXeroExportTests.setUp(self)
        self.quote = self.mod.run("""INSERT INTO quotes(customer_id,quote_number,title,quote_date,valid_until,status,total)
            VALUES (?,'Q-TEST','Clean 3 rugs','2026-09-28','2026-10-28','Draft',135)""", (self.first_id,))
        self.mod.run("INSERT INTO quote_lines(quote_id,item_name,quantity,line_total) VALUES (?,'Rug cleaning',3,135)", (self.quote,))
        self.path = f'/quotes/{self.quote}/xero'
        self.remote = {'QuoteID':'remote-quote','QuoteNumber':'QU-0084','Status':'DRAFT','Total':135,
                       'CurrencyCode':'GBP','Contact':{'ContactID':'contact-id'}}
        for name, value in [('refresh_xero_token_if_needed', ('unused','tenant-id')),
                            ('ensure_xero_contact_for_customer', 'contact-id')]:
            patcher = mock.patch.object(self.mod, name, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)
        self.api = mock.patch.object(self.mod, 'xero_api_request', side_effect=self.fake_api).start()
        self.addCleanup(mock.patch.stopall)

    def fake_api(self, url, method='GET', **kwargs):
        if url.endswith('/Accounts'):
            return {'Accounts':[{'Code':'200','Name':'Sales','Status':'ACTIVE','Type':'REVENUE'}]}
        if url.endswith('/TaxRates'):
            return {'TaxRates':[{'TaxType':'NONE','Name':'No VAT','EffectiveRate':0,'Status':'ACTIVE','CanApplyToRevenue':True}]}
        return {'Quotes':[dict(self.remote)] if method == 'POST' else []}

    def post(self, **data):
        return self.client.post(self.path, data={'_csrf_token':'test-csrf', **data})

    def prepare(self):
        _, fingerprint = self.mod.xero_quote_source(self.quote)
        response = self.post(action='prepare',account='200',tax='NONE',fingerprint=fingerprint)
        self.assertEqual(response.status_code,302)

    def test_review_is_read_only_and_requires_explicit_tax(self):
        page = self.client.get(self.path)
        self.assertEqual(page.status_code,200)
        self.assertIn(b'Choose the tax treatment',page.data)
        self.assertFalse(any(c.kwargs.get('method') == 'POST' for c in self.api.call_args_list))
        self.assertIsNone(self.mod.q('SELECT * FROM xero_quote_exports',one=True))
        self.assertIn(b'Choose a valid Xero',self.post(action='prepare',account='200').data)

    def test_create_draft_once_preserves_total_and_dates(self):
        self.prepare()
        self.post(action='create'); self.post(action='create')
        writes = [c for c in self.api.call_args_list if c.kwargs.get('method') == 'POST']
        self.assertEqual(len(writes),1)
        payload = writes[0].kwargs['payload']['Quotes'][0]
        self.assertEqual(payload['Status'],'DRAFT')
        self.assertEqual(payload['LineAmountTypes'],'Inclusive')
        self.assertEqual(payload['LineItems'][0]['UnitAmount'],135)
        self.assertEqual(payload['LineItems'][0]['TaxType'],'NONE')
        self.assertEqual(payload['Date'],'2026-09-28')
        self.assertEqual(payload['ExpiryDate'],'2026-10-28')
        self.assertNotIn('Terms',payload)
        page = self.client.get(self.path)
        self.assertIn(b'QU-0084',page.data)
        self.assertIn(b'DRAFT',page.data)

    def test_changed_quote_requires_new_review(self):
        self.prepare()
        self.mod.run('UPDATE quotes SET total=150 WHERE id=?',(self.quote,))
        self.assertIn(b'changed',self.post(action='create').data)
        self.assertFalse(any(c.kwargs.get('method') == 'POST' for c in self.api.call_args_list))

    def test_changed_tenant_blocks_creation(self):
        self.prepare()
        with mock.patch.object(self.mod,'refresh_xero_token_if_needed',return_value=('unused','different')):
            self.assertIn(b'organisation changed',self.post(action='create').data)
        self.assertFalse(any(c.kwargs.get('method') == 'POST' for c in self.api.call_args_list))

    def test_uncertain_create_never_reposts_and_can_reconcile(self):
        self.prepare()
        original = self.fake_api
        def timeout(url, method='GET', **kwargs):
            if method == 'POST': raise TimeoutError('network timeout')
            return original(url,method,**kwargs)
        self.api.side_effect = timeout
        self.post(action='create'); self.post(action='create')
        self.assertEqual(sum(c.kwargs.get('method') == 'POST' for c in self.api.call_args_list),1)
        export = self.mod.q('SELECT * FROM xero_quote_exports WHERE quote_id=?',(self.quote,),one=True)
        self.remote['Reference'] = export['reference']
        self.api.side_effect = lambda *a, **kw: {'Quotes':[self.remote]}
        self.post(action='check')
        saved=self.mod.q('SELECT * FROM xero_quote_exports WHERE quote_id=?',(self.quote,),one=True)
        self.assertEqual(saved['state'],'Created')

    def test_wrong_total_is_saved_for_review_without_second_create(self):
        self.prepare(); self.remote['Total']=162
        self.post(action='create'); self.post(action='create')
        saved=self.mod.q('SELECT * FROM xero_quote_exports WHERE quote_id=?',(self.quote,),one=True)
        self.assertEqual(saved['state'],'Review required')
        self.assertEqual(saved['xero_quote_id'],'remote-quote')
        self.assertEqual(sum(c.kwargs.get('method') == 'POST' for c in self.api.call_args_list),1)

    def test_missing_scope_stops_before_contact_mutation(self):
        self.prepare()
        self.api.side_effect = RuntimeError('insufficient_scope')
        self.assertIn(b'not authorised',self.post(action='create').data)
        self.mod.ensure_xero_contact_for_customer.assert_not_called()

    def test_saved_state_blocks_prepare_overwrite(self):
        self.prepare(); self.post(action='create')
        before=dict(self.mod.q('SELECT * FROM xero_quote_exports WHERE quote_id=?',(self.quote,),one=True))
        self.post(action='prepare',account='200',tax='NONE')
        after=dict(self.mod.q('SELECT * FROM xero_quote_exports WHERE quote_id=?',(self.quote,),one=True))
        self.assertEqual(before,after)

    def test_unauthenticated_request_cannot_create(self):
        with self.client.session_transaction() as s: s.clear()
        self.assertEqual(self.post(action='create').status_code,302)
        self.api.assert_not_called()

    def test_two_workers_only_one_claim_can_submit(self):
        self.prepare()
        original = self.fake_api
        def competing_claim(url, method='GET', **kwargs):
            if url.endswith('?page=1'):
                self.mod.run("UPDATE xero_quote_exports SET state='Creating' WHERE quote_id=?", (self.quote,))
            return original(url, method, **kwargs)
        self.api.side_effect = competing_claim
        self.assertIn(b'already being created', self.post(action='create').data)
        self.mod.ensure_xero_contact_for_customer.assert_not_called()
        self.assertFalse(any(c.kwargs.get('method') == 'POST' for c in self.api.call_args_list))

    def test_create_never_calls_message_senders_or_uses_posted_amount(self):
        self.prepare()
        with mock.patch.object(self.mod, 'send_env_email') as email, mock.patch.object(self.mod, 'send_clicksend_env_sms') as sms:
            self.post(action='create', total='999', status='SENT', customer_id=str(self.second_id))
        email.assert_not_called(); sms.assert_not_called()
        write = next(c for c in self.api.call_args_list if c.kwargs.get('method') == 'POST')
        self.assertEqual(write.kwargs['payload']['Quotes'][0]['LineItems'][0]['UnitAmount'],135)
        self.assertEqual(write.kwargs['payload']['Quotes'][0]['Status'],'DRAFT')

    def test_county_change_invalidates_review(self):
        self.prepare()
        self.mod.run("UPDATE customers SET county='Worcestershire' WHERE id=?",(self.first_id,))
        self.assertIn(b'changed',self.post(action='create').data)
        self.mod.ensure_xero_contact_for_customer.assert_not_called()

    def test_migration_is_repeatable_and_preserves_export(self):
        self.prepare()
        self.mod.init_db()
        self.assertIsNotNone(self.mod.q('SELECT * FROM xero_quote_exports WHERE quote_id=?',(self.quote,),one=True))

    def test_check_action_cannot_create_prepared_quote(self):
        self.prepare()
        self.post(action='check')
        self.assertFalse(any(c.kwargs.get('method') == 'POST' for c in self.api.call_args_list))
        self.mod.ensure_xero_contact_for_customer.assert_not_called()
