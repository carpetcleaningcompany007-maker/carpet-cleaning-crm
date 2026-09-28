import unittest
from unittest import mock
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
import test_customer_xero_exports as fixture

class SimpleCustomerDetailsTests(unittest.TestCase):
    tearDown = fixture.CustomerXeroExportTests.tearDown
    def setUp(self):
        fixture.CustomerXeroExportTests.setUp(self)
        self.job = self.mod.run("INSERT INTO jobs(customer_id,title,notes,status) VALUES (?,?,?,?)", (self.first_id,'Clean','Existing job note','Booked'))
        self.lead = self.mod.run("INSERT INTO intake_submissions(customer_id,job_id,name,phone,email,full_address,what3words,additional_notes,status) VALUES (?,?,?,?,?,?,?,?,?)", (self.first_id,self.job,'Alice Jones','07802563213','alice@real.test','1 High Street','old.three.words','Original notes','Booked'))
        with self.app.test_request_context('/'):
            self.url = self.mod.booking_form_url(self.mod.q('SELECT * FROM customers WHERE id=?',(self.first_id,),one=True))
        self.url = urlsplit(self.url).path+'?'+urlsplit(self.url).query
        with self.client.session_transaction() as session: session.clear()

    def test_minimal_mobile_form_and_read_only_get(self):
        before=self.mod.db().total_changes
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        soup=BeautifulSoup(response.data,'html.parser')
        fields=[x.get('name') for x in soup.select('form input:not([type=hidden]):not([type=checkbox]),form textarea')]
        self.assertEqual(fields,['name','phone','email','full_address','what3words','access_info'])
        self.assertIsNone(soup.select_one('[name=what3words]').get('required'))
        self.assertEqual(len(soup.select('button[type=submit]')),1)
        self.assertIn('width=device-width',soup.select_one('meta[name=viewport]')['content'])
        self.assertIn('@media(max-width:600px)',response.get_data(as_text=True))
        self.assertNotIn('Private note',response.get_data(as_text=True))
        self.assertEqual(before,self.mod.db().total_changes)

    @mock.patch('app.send_contact_form_owner_alerts')
    def test_signed_link_preserves_contacts_job_and_blank_values(self, alert):
        response=self.client.post(self.url,data={'name':'','full_address':'','what3words':'','access_info':'','privacy_acknowledgement':'1','customer_id':self.second_id,'job_id':'999','email':''})
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Your details have been saved',response.data)
        c=self.mod.q('SELECT * FROM customers WHERE id=?',(self.first_id,),one=True)
        self.assertEqual((c['first_name'],c['address'],c['email'],c['phone']),('Alice','1 High Street','alice@real.test','07802563213'))
        lead=self.mod.q('SELECT * FROM intake_submissions WHERE id=?',(self.lead,),one=True)
        self.assertEqual((lead['customer_id'],lead['job_id'],lead['what3words'],lead['status']),(self.first_id,self.job,'old.three.words','Booked'))
        self.assertEqual(self.mod.q('SELECT address FROM customers WHERE id=?',(self.second_id,),one=True)['address'],'2 Broad Street')

    @mock.patch('app.send_contact_form_owner_alerts')
    def test_notes_and_updated_address_on_signed_update_link(self,alert):
        token=self.mod.signed_intake_update_token(self.lead)
        response=self.client.post('/booking-form?update_token='+token,data={'name':'Alice Brown','full_address':'3 New Road','what3words':'new.three.words','access_info':'Side gate; dog indoors','privacy_acknowledgement':'1','customer_id':self.second_id})
        self.assertEqual(response.status_code,200)
        c=self.mod.q('SELECT * FROM customers WHERE id=?',(self.first_id,),one=True)
        self.assertEqual((c['last_name'],c['address']),('Brown','3 New Road'))
        self.assertIn('Private note',c['notes']); self.assertIn('Side gate',c['notes'])
        job=self.mod.q('SELECT * FROM jobs WHERE id=?',(self.job,),one=True)
        self.assertIn('Existing job note',job['notes']); self.assertIn('Side gate',job['notes'])

    def test_unsigned_and_invalid_links_cannot_read_or_update(self):
        before=self.mod.db().total_changes
        response=self.client.get('/booking-form?customer_id='+str(self.first_id))
        self.assertNotIn(b'Alice',response.data)
        self.assertEqual(self.client.post('/booking-form',data={'customer_id':self.first_id,'name':'Wrong','privacy_acknowledgement':'1'}).status_code,400)
        self.assertEqual(self.client.get('/booking-form?details_token=invalid').status_code,404)
        self.assertEqual(self.client.get('/booking-form?update_token=invalid').status_code,404)
        self.assertEqual(before,self.mod.db().total_changes)

    def test_privacy_required_without_losing_link(self):
        before=self.mod.db().total_changes
        response=self.client.post(self.url,data={'name':'New name'})
        self.assertIn(b'Please tick',response.data)
        self.assertEqual(before,self.mod.db().total_changes)

    @mock.patch('app.send_contact_form_owner_alerts')
    def test_customer_without_intake_keeps_existing_contact(self,alert):
        with self.app.test_request_context('/'):
            url=self.mod.booking_form_url(self.mod.q('SELECT * FROM customers WHERE id=?',(self.second_id,),one=True))
        path=urlsplit(url).path+'?'+urlsplit(url).query
        response=self.client.post(path,data={'name':'Bob Smith','phone':'01584876570','full_address':'','privacy_acknowledgement':'1'})
        self.assertEqual(response.status_code,200)
        lead=self.mod.q('SELECT * FROM intake_submissions WHERE customer_id=?',(self.second_id,),one=True)
        self.assertEqual(lead['full_address'],'2 Broad Street')
        self.assertEqual(lead['email'],'bob@real.test')

    def test_expired_signed_customer_link_is_rejected(self):
        with mock.patch.object(self.mod.URLSafeTimedSerializer,'loads',side_effect=self.mod.SignatureExpired('expired')):
            self.assertEqual(self.client.get(self.url).status_code,404)

    @mock.patch('app.send_contact_form_owner_alerts')
    def test_contacts_prefilled_optional_and_editable(self,alert):
        soup=BeautifulSoup(self.client.get(self.url).data,'html.parser')
        self.assertEqual(soup.select_one('[name=phone]')['value'],'07802563213')
        self.assertFalse(soup.select_one('[name=phone]').has_attr('required'))
        self.assertFalse(soup.select_one('[name=email]').has_attr('required'))
        self.client.post(self.url,data={'phone':'01584876570','email':'corrected@example.test','privacy_acknowledgement':'1'})
        c=self.mod.q('SELECT * FROM customers WHERE id=?',(self.first_id,),one=True)
        self.assertEqual((c['phone'],c['email']),('01584876570','corrected@example.test'))

    def test_missing_contacts_required_on_server_and_form(self):
        self.mod.run("UPDATE customers SET phone='',email='' WHERE id=?",(self.first_id,))
        self.mod.run("UPDATE intake_submissions SET phone='',email='' WHERE id=?",(self.lead,))
        soup=BeautifulSoup(self.client.get(self.url).data,'html.parser')
        self.assertTrue(soup.select_one('[name=phone]').has_attr('required'))
        self.assertTrue(soup.select_one('[name=email]').has_attr('required'))
        before=self.mod.db().total_changes
        response=self.client.post(self.url,data={'privacy_acknowledgement':'1'})
        self.assertIn(b'where missing',response.data)
        self.assertEqual(before,self.mod.db().total_changes)
