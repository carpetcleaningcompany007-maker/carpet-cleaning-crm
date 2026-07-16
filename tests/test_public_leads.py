import importlib
import os
import tempfile
import unittest
from unittest import mock


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
            "review_text": "Exact guest review text: stained carpets and dirty upholstery in multiple rooms.",
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
        self.assertIn("Exact guest review text", row["source_text"])

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

    def test_existing_source_update_keeps_active_status_and_updates_source_text(self):
        payload = {
            "business_name": "Example Inn",
            "source_website": "Public inn review",
            "source_url": "https://example.test/reviews/source-refresh",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Needs manual source verification.",
        }
        lead_id, _ = self.appmod.save_public_lead(payload)
        self.appmod.run("UPDATE public_leads SET status='New', source_text='' WHERE id=?", (lead_id,))
        updated_id, action = self.appmod.save_public_lead({
            **payload,
            "exact_issue": "Dirty carpet in the bar area.",
            "review_text": "Exact source review text about dirty carpet in the bar area.",
            "summary": "Verified public source text saved.",
        })
        row = self.appmod.q("SELECT status, source_text, exact_issue, summary FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertEqual(action, "updated")
        self.assertEqual(updated_id, lead_id)
        self.assertEqual(row["status"], "New")
        self.assertIn("Exact source review text", row["source_text"])
        self.assertIn("Dirty carpet", row["exact_issue"])
        self.assertIn("Verified public source text", row["summary"])

    def test_source_proof_fields_are_saved_and_displayed(self):
        lead_id, action = self.appmod.save_public_lead({
            "business_name": "Proof Hotel",
            "source_website": "Public hotel review",
            "source_url": "https://example.test/search-result",
            "source_direct_url": "https://example.test/reviews/exact-review",
            "source_screenshot_url": "https://example.test/proof.png",
            "source_verification_note": "Exact review belongs to Proof Hotel and has been checked.",
            "date_published": self.appmod.uk_today().isoformat(),
            "review_text": "Exact proof review text about stained carpets.",
            "summary": "Stained carpets mentioned in a public review.",
        })
        row = self.appmod.q("SELECT * FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertEqual(action, "created")
        self.assertEqual(row["source_direct_url"], "https://example.test/reviews/exact-review")
        self.assertEqual(row["source_screenshot_url"], "https://example.test/proof.png")
        self.assertIn("Proof Hotel", row["source_verification_note"])

        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.get("/new-leads?status=New")
        html = response.get_data(as_text=True)
        self.assertIn("Open exact source", html)
        self.assertIn("Original search/listing page", html)
        self.assertIn("Screenshot proof", html)
        self.assertIn("Source screenshot proof", html)
        self.assertIn("Exact review belongs to Proof Hotel", html)

    def test_imported_source_correction_refreshes_stale_draft(self):
        lead_id, action = self.appmod.save_public_lead({
            "business_name": "Old Inn Name",
            "source_website": "Public hotel review",
            "source_url": "https://example.test/search-result-correction",
            "date_published": self.appmod.uk_today().isoformat(),
            "review_text": "Exact proof review text about dirty carpets.",
            "summary": "Dirty carpets mentioned in a public review.",
        })
        self.assertEqual(action, "created")
        self.appmod.save_generated_lead_draft(lead_id)

        result = self.appmod.ingest_public_leads_json("""[{
            "business_name": "Correct Hotel Name",
            "source_website": "Public hotel review",
            "source_url": "https://example.test/search-result-correction",
            "source_direct_url": "https://example.test/reviews/exact-correction",
            "date_published": "%s",
            "review_text": "Corrected exact source proof text about dirty carpets.",
            "summary": "Corrected source proof belongs to the corrected hotel."
        }]""" % self.appmod.uk_today().isoformat())

        self.assertEqual(result["updated"], 1)
        row = self.appmod.q("SELECT business_name, draft_message FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertEqual(row["business_name"], "Correct Hotel Name")
        self.assertIn("Correct Hotel Name", row["draft_message"])
        self.assertNotIn("Old Inn Name", row["draft_message"])

    def test_old_public_post_is_expired(self):
        old_date = (self.appmod.uk_today() - self.appmod.timedelta(days=400)).isoformat()
        lead_id, _ = self.appmod.save_public_lead({
            "person_name": "Public Request",
            "source_website": "Reddit",
            "source_url": "https://reddit.example/post/old",
            "date_published": old_date,
            "summary": "Looking for carpet cleaner.",
        })
        row = self.appmod.q("SELECT status, lead_age_days FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertEqual(row["status"], "Expired")
        self.assertGreaterEqual(row["lead_age_days"], 400)

    def test_scan_records_blocked_sources_as_unavailable(self):
        result = self.appmod.run_public_lead_scan()
        unavailable = self.appmod.q("SELECT COUNT(*) AS c FROM lead_source_status WHERE status='Unavailable'", one=True)["c"]
        self.assertGreater(result["checked"], 0)
        self.assertGreater(unavailable, 0)

    def test_live_search_rss_saves_needs_checking_candidate(self):
        pub_date = self.appmod.uk_today().strftime("%a, %d %b %Y 10:00:00 GMT")
        rss = f"""<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0"><channel>
          <item>
            <title>Looking for carpet cleaner in Ludlow</title>
            <link>https://community.example/post/carpet-cleaner-ludlow</link>
            <description>Need carpet cleaner for stained carpet in Ludlow this week.</description>
            <pubDate>{pub_date}</pubDate>
          </item>
        </channel></rss>"""
        with mock.patch.object(self.appmod, "http_get_text", return_value=rss):
            result = self.appmod.run_public_lead_scan()

        lead = self.appmod.q("SELECT * FROM public_leads WHERE source_website='Live web search RSS candidates'", one=True)
        status = self.appmod.q("SELECT * FROM lead_source_status WHERE source_key='live_search_rss' ORDER BY id DESC LIMIT 1", one=True)
        self.assertIsNotNone(lead)
        self.assertEqual(lead["status"], "Needs Checking")
        self.assertEqual(lead["lead_age_days"], 0)
        self.assertIn("Needs checking", lead["summary"])
        self.assertIn("Need carpet cleaner for stained carpet", lead["source_text"])
        self.assertGreaterEqual(result["created"], 1)
        self.assertEqual(status["status"], "Live")

    def test_new_leads_card_shows_source_review_link_and_exact_text(self):
        self.appmod.save_public_lead({
            "business_name": "Source Proof Inn",
            "source_website": "Public inn review",
            "source_url": "https://example.test/reviews/source-proof",
            "date_published": self.appmod.uk_today().isoformat(),
            "review_text": "Exact saved review text about stained carpets in the bar area.",
            "summary": "Hospitality prospect with carpet staining.",
        })
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.get("/new-leads?status=New")
        html = response.get_data(as_text=True)
        self.assertIn("Open source / search result", html)
        self.assertIn("Exact saved review text about stained carpets", html)
        self.assertIn("Copy source text", html)

    def test_refresh_sources_backfills_from_raw_payload_and_regenerates_drafts(self):
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "Backfill Pub",
            "source_website": "Public pub review",
            "source_url": "https://example.test/reviews/backfill",
            "date_published": self.appmod.uk_today().isoformat(),
            "review_text": "Exact source text to backfill.",
            "summary": "Dirty carpet in entrance.",
        })
        self.appmod.run("UPDATE public_leads SET source_text='', draft_message='Old draft text' WHERE id=?", (lead_id,))
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.post("/new-leads/refresh-sources")
        row = self.appmod.q("SELECT source_text, draft_message FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn("Exact source text to backfill", row["source_text"])
        self.assertNotIn("Old draft text", row["draft_message"])

    def test_wider_search_uses_expanded_window_and_threshold(self):
        row = self.appmod.lead_scan_settings(mode="wide")
        self.assertGreaterEqual(row["search_radius_miles"], 150)
        self.assertGreaterEqual(row["selected_date_range_days"], 365)
        self.assertGreaterEqual(row["post_max_age_days"], 365)
        self.assertGreaterEqual(row["review_max_age_days"], 365)
        self.assertLessEqual(row["minimum_lead_score"], 25)

    def test_wider_search_button_runs_scan(self):
        pub_date = self.appmod.uk_today().strftime("%a, %d %b %Y 10:00:00 GMT")
        rss = f"""<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0"><channel>
          <item>
            <title>Dirty carpet hotel review in Birmingham</title>
            <link>https://reviews.example/hotel-dirty-carpet-birmingham</link>
            <description>Hotel review says stained carpet and dirty upholstery in Birmingham.</description>
            <pubDate>{pub_date}</pubDate>
          </item>
        </channel></rss>"""
        with mock.patch.object(self.appmod, "http_get_text", return_value=rss):
            with self.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["logged_in"] = True
                response = client.post("/new-leads/run-wide")
        self.assertEqual(response.status_code, 302)
        lead = self.appmod.q("SELECT * FROM public_leads WHERE source_url=?", ("https://reviews.example/hotel-dirty-carpet-birmingham",), one=True)
        self.assertIsNotNone(lead)
        self.assertEqual(lead["status"], "Needs Checking")

    def test_lead_settings_accept_twelve_month_search_window(self):
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.post("/lead-generation-settings", data={
                "search_radius_miles": "75",
                "counties": "Shropshire",
                "post_max_age_days": "365",
                "review_max_age_days": "365",
                "selected_date_range_days": "365",
                "enabled_sources": ["live_search_rss"],
                "search_frequency": "Daily",
                "maximum_leads_per_day": "25",
                "minimum_lead_score": "40",
                "follow_up_timing_days": "3",
            })
        self.assertEqual(response.status_code, 302)
        row = self.appmod.lead_generation_settings()
        self.assertEqual(row["selected_date_range_days"], 365)
        self.assertEqual(row["post_max_age_days"], 365)
        self.assertEqual(row["review_max_age_days"], 365)

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
            "location": "Ludlow",
            "source_website": "Public inn review",
            "source_url": "https://example.test/reviews/draft",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Guest wrote: the carpet was disgusting and embarrassing.",
            "public_email": "hello@exampleinn.test",
            "website": "https://exampleinn.test",
        })
        subject, body, channel = self.appmod.save_generated_lead_draft(lead_id)
        self.assertEqual(channel, "Email")
        self.assertIn("professional carpet and upholstery cleaning", body.lower())
        self.assertIn("example inn", subject.lower())
        self.assertNotIn("ludlow", subject.lower())
        self.assertIn("publicly available guest feedback", body.lower())
        self.assertIn("free demonstration", body.lower())
        self.assertNotIn("disgusting", body.lower())
        self.assertNotIn("embarrassing", body.lower())

    def test_generate_drafts_button_populates_visible_leads(self):
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "Example Holiday Cottage",
            "source_website": "Public holiday cottage review",
            "source_url": "https://example.test/reviews/draft-button",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Lounge carpet needs replacing and smells musty.",
            "location": "Leominster",
            "public_email": "hello@examplecottage.test",
            "website": "https://examplecottage.test",
        })
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.post("/new-leads/generate-drafts", data={"status": "New"})
        self.assertEqual(response.status_code, 302)
        lead = self.appmod.q("SELECT draft_message FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertIn("cost-effective alternative to replacement", lead["draft_message"])

    def test_generate_drafts_button_refreshes_existing_drafts(self):
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "Example Pub",
            "source_website": "Expedia search result",
            "source_url": "https://example.test/reviews/refresh-draft",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Stained carpet in the bar area.",
            "location": "Hereford",
        })
        self.appmod.run("UPDATE public_leads SET draft_message='Old draft text' WHERE id=?", (lead_id,))
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.post("/new-leads/generate-drafts", data={"status": "New"})
        self.assertEqual(response.status_code, 302)
        lead = self.appmod.q("SELECT draft_message FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertNotIn("Old draft text", lead["draft_message"])
        self.assertNotIn("public post", lead["draft_message"].lower())

    def test_new_leads_page_auto_generates_visible_drafts(self):
        lead_id, _ = self.appmod.save_public_lead({
            "person_name": "Public Request",
            "source_website": "Facebook public post",
            "source_url": "https://example.test/social/request",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Looking for carpet cleaner for dog wee on carpet.",
            "location": "Hereford",
        })
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.get("/new-leads?status=New")
        self.assertEqual(response.status_code, 200)
        lead = self.appmod.q("SELECT draft_message FROM public_leads WHERE id=?", (lead_id,), one=True)
        self.assertIn("pet odour", lead["draft_message"].lower())

    def test_business_review_message_without_email_is_not_called_public_post(self):
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "Example Pub",
            "source_website": "Expedia search result",
            "source_url": "https://example.test/reviews/pub-no-email",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Dirty carpet in the bar area.",
            "location": "Hereford",
        })
        _subject, body, channel = self.appmod.save_generated_lead_draft(lead_id)
        self.assertEqual(channel, "Message")
        self.assertNotIn("public post", body.lower())
        self.assertIn("example pub", body.lower())

    def test_business_review_preview_without_email_uses_full_letter(self):
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "Example Pub",
            "source_website": "Expedia search result",
            "source_url": "https://example.test/reviews/pub-preview-no-email",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Stained carpet in the bar area.",
            "location": "Hereford",
        })
        self.appmod.save_generated_lead_draft(lead_id)
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.get(f"/new-leads/{lead_id}/preview")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("My name is Paul and I own The Carpet Cleaning Company", html)
        self.assertIn("hotels, pubs, inns, wedding venues", html)
        self.assertIn("free demonstration", html)

    def test_lead_email_preview_is_branded(self):
        lead_id, _ = self.appmod.save_public_lead({
            "business_name": "Example Hotel",
            "source_website": "Public hotel review",
            "source_url": "https://example.test/reviews/preview",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Stained carpet in reception.",
            "location": "Shrewsbury",
            "public_email": "hello@examplehotel.test",
            "website": "https://examplehotel.test",
        })
        self.appmod.save_generated_lead_draft(lead_id)
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.get(f"/new-leads/{lead_id}/preview")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("The Carpet Cleaning Company", html)
        self.assertIn("Professional carpet & upholstery cleaning", html)
        self.assertIn("email-screen-preview", html)
        self.assertNotIn("background:#071524;padding:22px 30px", html)
        self.assertIn("background:#fffaf0", html)
        self.assertIn("Professional carpet and upholstery cleaning for businesses that want cleaner", html)
        self.assertNotIn("Professional carpet and upholstery cleaning in your area.", html)
        self.assertNotIn("Location</td>", html)
        self.assertIn("Shropshire, Herefordshire, Worcestershire and the surrounding areas", html)
        self.assertIn("free demonstration", html)
        self.assertIn("hero-carpet-cleaning.webp", html)
        self.assertIn("Freshly cleaned carpet", html)
        self.assertIn("View our Facebook page", html)
        self.assertIn("Read our Google reviews", html)

    def test_new_leads_card_has_screen_preview_link(self):
        self.appmod.save_public_lead({
            "business_name": "Example Venue",
            "source_website": "Public review",
            "source_url": "https://example.test/reviews/card-preview",
            "date_published": self.appmod.uk_today().isoformat(),
            "summary": "Dirty carpet in entrance.",
        })
        with self.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
            response = client.get("/new-leads?status=New")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Screen Preview", response.get_data(as_text=True))

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
