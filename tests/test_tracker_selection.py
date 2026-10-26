import os
import unittest

os.environ.setdefault("NOTION_API_KEY", "test-only")

from tracker import classify_purposes, finished_youtube_video


class TrackerSelectionTests(unittest.TestCase):
    def test_all_checked_roles_are_kept(self):
        meta = {"is_target": True, "is_copy": True, "is_shorts_material": True}
        self.assertEqual(
            classify_purposes(meta),
            ["쇼츠 소재", "일반 수집대상", "카피 벤치마킹"],
        )

    def test_active_live_and_upcoming_wait(self):
        for state in ("live", "upcoming"):
            with self.subTest(state=state):
                self.assertFalse(finished_youtube_video({
                    "snippet": {"liveBroadcastContent": state},
                    "contentDetails": {"duration": "PT45M"},
                }))

    def test_started_live_waits_for_end_and_duration(self):
        item = {
            "snippet": {"liveBroadcastContent": "none"},
            "liveStreamingDetails": {"actualStartTime": "2026-10-26T00:00:00Z"},
            "contentDetails": {"duration": "PT45M"},
        }
        self.assertFalse(finished_youtube_video(item))
        item["liveStreamingDetails"]["actualEndTime"] = "2026-10-26T00:45:00Z"
        self.assertTrue(finished_youtube_video(item))
        item["contentDetails"]["duration"] = "PT0S"
        self.assertFalse(finished_youtube_video(item))

    def test_normal_uploaded_video_with_duration_is_ready(self):
        self.assertTrue(finished_youtube_video({
            "snippet": {"liveBroadcastContent": "none"},
            "contentDetails": {"duration": "PT4M31S"},
        }))
        self.assertFalse(finished_youtube_video({"snippet": {"liveBroadcastContent": "none"}}))


if __name__ == "__main__":
    unittest.main()
