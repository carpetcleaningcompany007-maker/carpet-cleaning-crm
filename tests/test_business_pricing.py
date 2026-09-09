import unittest
import test_customer_conversation as fixture

class BusinessPricingTests(unittest.TestCase):
    setUp=fixture.CustomerConversationTests.setUp
    tearDown=fixture.CustomerConversationTests.tearDown
    def payload(self):
        with self.mod.app.app_context():
            data=self.mod.pricing()
        return dict(method='first_room',first_price='75',other_price='45',onsite_price='45',minimum_charge='90',notes='Owner checks fabric',**{'price_'+str(i):str(item['price']) for i,item in enumerate(data['domestic'])})
    def test_save_shared_prices_and_ai_context(self):
        form=self.payload();form['price_0']='80'
        self.assertEqual(self.client.post('/settings/prices',data=form).status_code,302)
        with self.mod.app.app_context():
            context=self.mod.business_pricing_context()
            self.assertEqual(context['catalogue']['room_rules']['first_price'],75)
            self.assertEqual(context['catalogue']['domestic'][0]['price'],80)
            self.assertEqual(context['minimum_charge'],90)
        self.assertEqual(self.client.get('/settings/prices').status_code,200)
    def test_invalid_price_does_not_partially_save(self):
        form=self.payload();form['price_0']='-1'
        with self.mod.app.app_context():before=self.mod.pricing()
        self.assertEqual(self.client.post('/settings/prices',data=form).status_code,400)
        with self.mod.app.app_context():self.assertEqual(self.mod.pricing(),before)
    def test_zero_and_auth(self):
        form=self.payload();form['minimum_charge']='0';form['other_price']='0'
        self.assertEqual(self.client.post('/settings/prices',data=form).status_code,302)
        with self.mod.app.app_context():self.assertEqual(self.mod.settings()['minimum_charge'],0)
        with self.client.session_transaction() as session:session.clear()
        self.assertEqual(self.client.get('/settings/prices').status_code,302)
