import datetime as dt
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from collection_gate import KST, collection_decision


class CollectionGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "tracker.db"
        self.now = dt.datetime(2026, 10, 30, 13, 37, tzinfo=KST)

    def record(self, value):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("CREATE TABLE video_metrics (logged_at TEXT)")
            if value is not None:
                conn.execute("INSERT INTO video_metrics VALUES (?)", (value,))

    def test_recent_korean_timestamp_skips_backup(self):
        self.record("2026-10-30 13:17:00")
        self.assertFalse(collection_decision(self.database, self.now)[0])

    def test_threshold_and_old_measurement_collect(self):
        self.record("2026-10-30 12:47:00")
        self.assertTrue(collection_decision(self.database, self.now)[0])

    def test_utc_timestamp_is_compared_to_same_instant(self):
        self.record("2026-10-30T04:17:00+00:00")
        self.assertFalse(collection_decision(self.database, self.now)[0])

    def test_empty_or_missing_database_collects(self):
        self.assertTrue(collection_decision(self.database, self.now)[0])
        self.assertFalse(self.database.exists())
        self.record(None)
        self.assertTrue(collection_decision(self.database, self.now)[0])

    def test_code_push_and_force_allow_immediate_collection(self):
        self.record("2026-10-30 13:36:00")
        self.assertTrue(collection_decision(self.database, self.now, event="push")[0])
        self.assertTrue(collection_decision(self.database, self.now, force=True)[0])

    def test_corrupt_or_future_timestamp_fails_instead_of_silent_skip(self):
        self.record("2026-10-30 14:37:00")
        with self.assertRaises(ValueError):
            collection_decision(self.database, self.now)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE video_metrics SET logged_at = 'invalid'")
        with self.assertRaises(ValueError):
            collection_decision(self.database, self.now)


if __name__ == "__main__":
    unittest.main()
