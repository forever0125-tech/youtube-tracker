"""Read-only audit of checked target channels against recent Daily Issue rows."""

import argparse
import collections
import datetime
import json

import requests
import tracker


def checked_channel_ids():
    ids = {}
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        page = tracker.query_notion_database(tracker.TARGET_DB_ID, payload)
        for row in page["results"]:
            props = row["properties"]
            if not any(props.get(field, {}).get("checkbox") for field in ("수집대상", "쇼츠 소재", "카피 벤치마킹")):
                continue
            name = "".join(part.get("plain_text", "") for part in props.get("채널명", {}).get("title", [])).strip()
            channel_id = "".join(part.get("plain_text", "") for part in props.get("채널 ID", {}).get("rich_text", [])).strip()
            ids[name] = channel_id
        if not page.get("has_more"):
            break
        cursor = page["next_cursor"]
    return ids


def unavailable_youtube_channels(channel_ids):
    known = set()
    keys = tracker.YOUTUBE_API_KEYS
    ids = sorted({value for value in channel_ids.values() if value})
    for offset in range(0, len(ids), 50):
        batch = ids[offset:offset + 50]
        response = None
        for key in keys:
            response = requests.get("https://www.googleapis.com/youtube/v3/channels", params={
                "part": "id", "id": ",".join(batch), "key": key,
            }, timeout=25)
            if response.ok:
                break
        if response is None or not response.ok:
            raise RuntimeError("YouTube channel ID audit failed; no coverage result written")
        known.update(item["id"] for item in response.json().get("items", []))
    return {name: channel_id for name, channel_id in channel_ids.items() if channel_id not in known}


def latest_uploads(channel_ids, names):
    result = {}
    key = tracker.YOUTUBE_API_KEYS[0]
    for name in names:
        channel_id = channel_ids.get(name)
        if not channel_id:
            result[name] = "채널 ID 없음"
            continue
        channel = requests.get("https://www.googleapis.com/youtube/v3/channels", params={
            "part": "contentDetails", "id": channel_id, "key": key,
        }, timeout=25)
        channel.raise_for_status()
        items = channel.json().get("items", [])
        if not items:
            result[name] = "채널 ID 조회 불가"
            continue
        playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        upload = requests.get("https://www.googleapis.com/youtube/v3/playlistItems", params={
            "part": "contentDetails", "playlistId": playlist, "maxResults": 1, "key": key,
        }, timeout=25)
        upload.raise_for_status()
        videos = upload.json().get("items", [])
        result[name] = (videos[0].get("contentDetails", {}).get("videoPublishedAt")
                        if videos else "공개 업로드 없음")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    channels = tracker.fetch_target_channels()
    videos = tracker.fetch_issue_videos(channels)
    channel_ids = checked_channel_ids()
    invalid_ids = unavailable_youtube_channels(channel_ids)
    counts = collections.Counter(video["channel_name"] for video in videos)
    roles = {
        "수집대상": "is_target",
        "쇼츠 소재": "is_shorts_material",
        "카피 벤치마킹": "is_copy",
    }
    report = {
        "checked_at_kst": datetime.datetime.now(tracker.KST).isoformat(),
        "window": "데일리 이슈 수집함 최근 7일",
        "daily_video_rows": len(videos),
        "checked_unique_channels": len(channel_ids),
        "youtube_channel_ids_not_found": invalid_ids,
        "roles": {},
    }
    for label, key in roles.items():
        names = sorted({meta["channel_name"] for meta in channels.values() if meta[key]})
        absent = [name for name in names if counts[name] == 0]
        report["roles"][label] = {
            "checked_channels": len(names),
            "with_recent_video": len(names) - len(absent),
            "no_recent_video": absent,
            "video_rows": sum(counts[name] for name in names),
        }
    missing_names = sorted({name for role in report["roles"].values() for name in role["no_recent_video"]})
    report["latest_uploads_for_no_recent_channels"] = latest_uploads(channel_ids, missing_names)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
