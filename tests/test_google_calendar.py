import importlib
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo


class GoogleCalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        os.environ["CRM_DB_PATH"] = self.tmp.name
        os.environ["DISABLE_CRM_BACKGROUND_AUTOMATION"] = "1"
        sys.modules.pop("app", None)
        self.mod = importlib.import_module("app")
        self.mod.init_db()

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_booking_range_uses_london_dst_and_private_finish(self):
        summer_start, summer_end = self.mod.booking_datetime_range("2026-08-12", "09:30", "12:00", 30)
        winter_start, _ = self.mod.booking_datetime_range("2026-12-12", "09:30", "", 120)
        self.assertEqual(summer_start.utcoffset().total_seconds(), 3600)
        self.assertEqual(winter_start.utcoffset().total_seconds(), 0)
        self.assertEqual(summer_end.strftime("%H:%M"), "12:00")

    def test_tbc_cannot_be_synced(self):
        with self.assertRaisesRegex(ValueError, "exact start time"):
            self.mod.booking_datetime_range("2026-08-12", "Time to be confirmed", "", 120)

    def test_overlap_detection_excludes_linked_event(self):
        zone = ZoneInfo("Europe/London")
        events = [
            {
                "id": "other",
                "summary": "Existing job",
                "start": {"dateTime": "2026-08-12T10:00:00+01:00"},
                "end": {"dateTime": "2026-08-12T12:00:00+01:00"},
            },
            {
                "id": "linked",
                "start": {"dateTime": "2026-08-12T09:00:00+01:00"},
                "end": {"dateTime": "2026-08-12T13:00:00+01:00"},
            },
        ]
        clashes = self.mod.google_calendar_conflicts(
            events,
            datetime(2026, 8, 12, 11, 0, tzinfo=zone),
            datetime(2026, 8, 12, 12, 30, tzinfo=zone),
            "linked",
        )
        self.assertEqual([item["id"] for item in clashes], ["other"])

    def test_calendar_dashboard_and_booking_form_render(self):
        client = self.mod.app.test_client()
        with client.session_transaction() as session:
            session["logged_in"] = True
        response = client.get("/google-calendar")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Google Calendar", response.data)


if __name__ == "__main__":
    unittest.main()
