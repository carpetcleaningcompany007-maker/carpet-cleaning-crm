import importlib
import os
import tempfile
import unittest


class PublicLeadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        import app
        self.appmod = importlib.reload(app)
        self.app = self.appmod.app
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.appmod.init_db()

    def tearDown(self):
        self.ctx.pop()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_recent_lead_is_scored_and_saved(self):
        lead_id, action = self.appmod.save_public_lead({
            "business_name": "Example Hotel",
            "source_website": "Public hotel review",
            "source_url": "https://example.test/reviews/1",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Guest complained about stained carpets and dirty upholstery in multiple rooms.",
            "postcode": "SY8 1AA",
            "county": "Shropshire",
            "public_phone": "07802 563213",
        })
        row = self.appmod.q("SELECT * FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertEqual(action, "created")
        self.assertEqual(row["status"], "New")
        self.assertGreaterEqual(row["lead_score"], 60)
        self.assertEqual(row["lead_age_days"], 0)

    def test_duplicate_source_url_updates_existing_lead(self):
        payload = {
            "business_name": "Example Pub",
            "source_website": "Public pub review",
            "source_url": "https://example.test/reviews/duplicate",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Dirty carpet complaint.",
        }
        first_id, first_action = self.appmod.save_public_lead(payload)
        second_id, second_action = self.appmod.save_public_lead(payload)
        count = self.appmod.q("SELECT COUNT(*) AS c FROM public_leads", one=True)["c"]
        self.assertEqual(first_action, "created")
        self.assertEqual(second_action, "updated")
        self.assertEqual(first_id, second_id)
        self.assertEqual(count, 1)

    def test_old_public_post_is_expired(self):
        old_date = (self.appmod.uk_today() - self.appmod.timedelta(days=10)).isoformat()
        lead_id, _ = self.appmod.save_public_lead({
            "person_name": "Public Request",
            "source_website": "Reddit",
            "source_url": "https://reddit.example/post/old",
            "date_published": old_date,
            "summary": "Looking for carpet cleaner.",
        })
        row = self.appmod.q("SELECT status, lead_age_days FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertEqual(row["status"], "Expired")
        self.assertGreaterEqual(row["lead_age_days"], 10)

    def test_scan_records_blocked_sources_as_unavailable(self):
        result = self.appmod.run_public_lead_scan()
        unavailable = self.appmod.q("SELECT COUNT(*) AS c FROM lead_source_status WHERE status='Unavailable'", one=True)["c"]
        self.assertGreater(result["checked"], 0)
        self.assertGreater(unavailable, 0)

    def test_due_lead_generation_check_runs_once_per_day(self):
        first = self.appmod.run_due_lead_generation_check(force=True)
        second = self.appmod.run_due_lead_generation_check(force=False)
        completed = self.appmod.q("SELECT COUNT(*) AS c FROM lead_generation_log WHERE event_type='search_completed'", one=True)["c"]
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(completed, 1)

    def test_business_email_draft_does_not_quote_bad_review(self):
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "Example Inn",
            "source_website": "Public inn review",
            "source_url": "https://example.test/reviews/draft",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Guest wrote: the carpet was disgusting and embarrassing.",
            "public_email": "hello@exampleinn.test",
            "website": "https://exampleinn.test",
        })
        _subject, body, channel = self.appmod.save_generated_lead_draft(lead_id)
        self.assertEqual(channel, "Email")
        self.assertIn("professional carpet and upholstery cleaning", body.lower())
        self.assertNotIn("disgusting", body.lower())
        self.assertNotIn("embarrassing", body.lower())

    def test_email_send_is_blocked_for_duplicate_lead(self):
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "Duplicate Hotel",
            "source_website": "Public hotel review",
            "source_url": "https://example.test/reviews/email-block",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Stained carpet in reception.",
            "public_email": "hello@duplicatehotel.test",
            "website": "https://duplicatehotel.test",
        })
        self.appmod.save_generated_lead_draft(lead_id)
        self.appmod.run("UPDATE public_leads SET status='Approved', duplicate_of_id=123 WHERE id=?", (lead_id,))
        sent, msg = self.appmod.send_approved_lead_email(lead_id)
        self.assertFalse(sent)
        self.assertIn("duplicate", msg.lower())

    def test_daily_summary_does_not_repeat_leads(self):
        self.appmod.save_public_lead({
            "business_name": "Summary Pub",
            "source_website": "Public pub review",
            "source_url": "https://example.test/reviews/summary",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Dirty carpet complaint.",
        })
        first = self.appmod.generate_daily_lead_summary(mark_sent=True)
        second = self.appmod.generate_daily_lead_summary(mark_sent=True)
        self.assertEqual(first["count"], 1)
        self.assertEqual(second["count"], 0)

    def test_excluded_keyword_marks_lead_not_suitable(self):
        self.appmod.run("UPDATE lead_generation_settings SET excluded_keywords='competitor advert' WHERE id=1")
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "Advert Lead",
            "source_website": "Community forum",
            "source_url": "https://example.test/forum/advert",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Competitor advert for carpet cleaning.",
        })
        row = self.appmod.q("SELECT status FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertEqual(row["status"], "Not Suitable")

    def test_xero_cancel_sends_nothing(self):
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "No Xero Upload Ltd",
            "source_website": "Public business review",
            "source_url": "https://example.test/reviews/xero-cancel",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Stained carpet in office.",
        })
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        resp = client.post(f"/new-leads/{lead_id}/xero-confirm", data={"action": "cancel"}, follow_redirects=False)
        row = self.appmod.q("SELECT xero_contact_id, xero_action_status FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(row["xero_contact_id"], "")
        self.assertEqual(row["xero_action_status"], "")


if __name__ == "__main__":
    unittest.main()
