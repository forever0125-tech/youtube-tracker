# ==============================================================================
# YouTube Benchmarking Tracker v3.7
# - 카드 내 [📺 채널명] 클릭 시 검색창을 비우고 100% 채널 단위로만 단독 필터링
# - 상단 키워드 바 제외 단어 강화(전계완, 생중계, 화면출처 등) 및 20개 노출 확장
# - 노션 [쇼츠 소재] 체크박스 연동 및 대시보드 목적 필터 탑재
# - 10분 주기 대시보드 브라우저 자동 새로고침 탑재
# - YouTube API 403 소진 시 보조 키 자동 전환(Failover)
# ==============================================================================

import os
import re
import json
import sqlite3
import datetime
import csv
import io
import time
import requests

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")

# 로컬 PC config.json 지원
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
if not NOTION_API_KEY and os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            NOTION_API_KEY = cfg.get("NOTION_API_KEY", "")
    except Exception:
        pass

if not NOTION_API_KEY:
    NOTION_API_KEY = "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"

TARGET_DB_ID = "353a73c83d0780568544f053bfdca3bf"
ISSUE_DB_ID = "353a73c83d07800a8aead62083146a44"

# 유튜브 기본 키 + 보조 키 풀 (403 에러 발생 시 자동 스위칭)
YOUTUBE_API_KEYS = [
    "AIzaSyBFPe0eYPI99YfeH-P89OPJvUAMgOzXLKc",
    "AIzaSyDVTGEQXH33HX5kwrIFjYBBip0xgPgl1BI"
]
current_yt_key_index = 0

LOG_SHEET_ID = "18UkL2pTTnpuGVqrafQC2uKP2C6juda_4ViJejYoXt80"
DB_FILE = "tracker.db"
MAX_HISTORY_BUCKETS_PER_VIDEO = 24
KST = datetime.timezone(datetime.timedelta(hours=9))

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

def init_sqlite():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_metrics (
            logged_at TEXT,
            notion_id TEXT,
            video_id TEXT,
            channel_name TEXT,
            title TEXT,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            hours_elapsed REAL,
            vph INTEGER,
            subscribers INTEGER,
            format_tag TEXT,
            duration TEXT,
            purpose TEXT,
            political_bias TEXT,
            thumbnail TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_metrics_video_time ON video_metrics(video_id, logged_at)")
    conn.commit()
    conn.close()

def compact_history(conn, buckets_per_video=MAX_HISTORY_BUCKETS_PER_VIDEO):
    """Keep one sample per elapsed-hour bucket and only the newest buckets per video."""
    cursor = conn.cursor()
    before = cursor.execute("SELECT COUNT(*) FROM video_metrics").fetchone()[0]
    cursor.execute("""
        DELETE FROM video_metrics
        WHERE rowid NOT IN (
            SELECT rowid FROM (
                SELECT
                    rowid,
                    ROW_NUMBER() OVER (
                        PARTITION BY video_id, CAST(ROUND(hours_elapsed) AS INTEGER)
                        ORDER BY logged_at DESC, rowid DESC
                    ) AS duplicate_rank,
                    DENSE_RANK() OVER (
                        PARTITION BY video_id
                        ORDER BY CAST(ROUND(hours_elapsed) AS INTEGER) DESC
                    ) AS bucket_rank
                FROM video_metrics
            )
            WHERE duplicate_rank = 1 AND bucket_rank <= ?
        )
    """, (buckets_per_video,))
    conn.commit()
    after = cursor.execute("SELECT COUNT(*) FROM video_metrics").fetchone()[0]
    deleted = before - after
    if deleted:
        conn.execute("VACUUM")
    print(f"🧹 [DB 자동 정리] {deleted:,}개 중복/과거 기록 제거 · {after:,}개 유지")

def sync_history_from_google_sheet():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM video_metrics")
    total_logs = cursor.fetchone()[0]

    if total_logs < 200:
        csv_urls = [
            f"https://docs.google.com/spreadsheets/d/{LOG_SHEET_ID}/gviz/tq?tqx=out:csv",
            f"https://docs.google.com/spreadsheets/d/{LOG_SHEET_ID}/export?format=csv"
        ]
        for url in csv_urls:
            try:
                res = requests.get(url, timeout=15)
                if res.status_code == 200 and "html" not in res.headers.get("Content-Type", "").lower():
                    csv_text = res.content.decode("utf-8-sig", errors="ignore")
                    reader = csv.reader(io.StringIO(csv_text))
                    rows_to_insert = []
                    for row in reader:
                        if len(row) >= 7:
                            clean_views = re.sub(r"[^\d]", "", str(row[3]))
                            clean_hours = re.sub(r"[^\d.]", "", str(row[6]))
                            if clean_views.isdigit() and clean_hours:
                                try:
                                    rows_to_insert.append((
                                        row[0].strip(), row[1].strip(), row[2].strip(), "", "",
                                        int(clean_views), 0, 0, float(clean_hours),
                                        0, 0, "", "", "", "", ""
                                    ))
                                except Exception:
                                    continue

                    if rows_to_insert:
                        cursor.executemany("""
                            INSERT INTO video_metrics 
                            (logged_at, notion_id, video_id, channel_name, title, views, likes, comments, hours_elapsed, vph, subscribers, format_tag, duration, purpose, political_bias, thumbnail)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, rows_to_insert)
                        conn.commit()
                        break
            except Exception:
                continue
    conn.close()

def extract_youtube_id(url):
    if not url: return None
    match = re.search(r"(?:youtu\.be\/|youtube\.com\/(?:embed\/|v\/|watch\?v=|watch\?.+&v=|shorts\/|live\/))([a-zA-Z0-9_-]{11})", url)
    return match.group(1) if match else None

def format_hours_to_korean(hours_float):
    total_minutes = int(round(hours_float * 60))
    h = total_minutes // 60
    m = total_minutes % 60
    if h == 0:
        return f"{m}분 전"
    if m == 0:
        return f"{h}시간 전"
    return f"{h}시간 {m}분 전"

def fetch_target_channels():
    url = f"https://api.notion.com/v1/databases/{TARGET_DB_ID}/query"
    channels = {}
    has_more = True
    next_cursor = None
    while has_more:
        payload = {"page_size": 100}
        if next_cursor: payload["start_cursor"] = next_cursor
        res = requests.post(url, headers=NOTION_HEADERS, json=payload).json()
        for p in res.get("results", []):
            page_id = p["id"]
            props = p.get("properties", {})
            c_name = props.get("채널명", {}).get("title", [{}])[0].get("plain_text", "알수없음").strip()
            subs = props.get("구독자수", {}).get("number", 0) or 0
            bias = props.get("정치성향", {}).get("select", {}).get("name", "미배치") if props.get("정치성향", {}).get("select") else "미배치"
            
            is_copy = props.get("카피 벤치마킹", {}).get("checkbox", False)
            is_target = props.get("수집대상", {}).get("checkbox", False)
            is_shorts_material = props.get("쇼츠 소재", {}).get("checkbox", False)

            channels[page_id] = {
                "channel_name": c_name,
                "subscribers": subs,
                "political_bias": bias,
                "is_copy": is_copy,
                "is_target": is_target,
                "is_shorts_material": is_shorts_material
            }
        has_more = res.get("has_more", False)
        next_cursor = res.get("next_cursor")
    return channels

def fetch_issue_videos(channel_meta_map):
    url = f"https://api.notion.com/v1/databases/{ISSUE_DB_ID}/query"
    pages = []
    has_more = True
    next_cursor = None

    now_kst = datetime.datetime.now(KST)
    seven_days_ago_iso = (now_kst - datetime.timedelta(days=7)).date().isoformat()

    payload = {
        "filter": {
            "property": "업로드 일시",
            "date": {
                "on_or_after": seven_days_ago_iso
            }
        },
        "page_size": 100
    }

    while has_more:
        if next_cursor: payload["start_cursor"] = next_cursor
        res = requests.post(url, headers=NOTION_HEADERS, json=payload).json()
        pages.extend(res.get("results", []))
        has_more = res.get("has_more", False)
        next_cursor = res.get("next_cursor")

    video_items = []
    target_channel_names = {v["channel_name"].replace(" ", "").lower(): v for v in channel_meta_map.values()}

    for page in pages:
        p = page.get("properties", {})
        v_url = p.get("영상 링크", {}).get("url")
        up_date_str = p.get("업로드 일시", {}).get("date", {}).get("start") if p.get("업로드 일시", {}).get("date") else None
        title_list = p.get("영상 제목", {}).get("title", [])
        title = title_list[0].get("plain_text", "무제") if title_list else "무제"
        
        vid = extract_youtube_id(v_url)
        if not vid: continue

        up_dt = now_kst - datetime.timedelta(hours=24)
        if up_date_str:
            try:
                clean_date = up_date_str.replace("Z", "+00:00")
                if "T" in clean_date:
                    up_dt = datetime.datetime.fromisoformat(clean_date)
                else:
                    up_dt = datetime.datetime.fromisoformat(clean_date + "T00:00:00+09:00")
                if up_dt.tzinfo is None:
                    up_dt = up_dt.replace(tzinfo=KST)
                else:
                    up_dt = up_dt.astimezone(KST)
            except Exception:
                pass

        rel_channels = p.get("출처 채널", {}).get("relation", [])
        matched_c_meta = None
        if rel_channels:
            rel_id = rel_channels[0].get("id")
            if rel_id in channel_meta_map:
                matched_c_meta = channel_meta_map[rel_id]

        txt_name = ""
        txt_list = p.get("수집 채널명", {}).get("rich_text", [])
        if txt_list:
            txt_name = txt_list[0].get("plain_text", "").strip()
            
        if not matched_c_meta and txt_name:
            clean_txt = txt_name.replace(" ", "").lower()
            for c_clean, meta in target_channel_names.items():
                if c_clean in clean_txt or clean_txt in c_clean:
                    matched_c_meta = meta
                    break

        c_name = matched_c_meta["channel_name"] if matched_c_meta else (txt_name or "알수없음")
        subs = matched_c_meta["subscribers"] if matched_c_meta else 0
        bias = matched_c_meta["political_bias"] if matched_c_meta else "미배치"

        fmt = p.get("포맷", {}).get("select", {}).get("name", "🔴 롱폼") if p.get("포맷", {}).get("select") else "🔴 롱폼"
        dur = p.get("영상 길이", {}).get("rich_text", [{}])[0].get("plain_text", "") if p.get("영상 길이", {}).get("rich_text") else ""
        collect_method = p.get("수집 방식", {}).get("select", {}).get("name", "") if p.get("수집 방식", {}).get("select") else ""
        
        if matched_c_meta and matched_c_meta.get("is_shorts_material", False):
            purpose_val = "쇼츠 소재"
        elif matched_c_meta and matched_c_meta.get("is_target", False):
            purpose_val = "일반 수집대상"
        elif (matched_c_meta and matched_c_meta.get("is_copy", False)) or ("직접" in collect_method or "스크랩" in collect_method):
            purpose_val = "카피 벤치마킹"
        else:
            purpose_val = "기타 수집"
        
        cover = page.get("cover", {})
        thumb = cover.get("external", {}).get("url", "") if cover else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

        video_items.append({
            "page_id": page["id"],
            "video_id": vid,
            "title": title,
            "channel_name": c_name,
            "subscribers": subs,
            "political_bias": bias,
            "upload_dt_kst": up_dt,
            "format": fmt,
            "duration": dur,
            "purpose": purpose_val,
            "thumbnail": thumb
        })
    return video_items

def get_videos_details(video_ids):
    global current_yt_key_index
    details = {}
    if not video_ids: return details

    print(f"📥 유튜브 API에서 영상 {len(video_ids)}개의 실시간 통계 조회 중...")
    
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        success = False

        while not success and current_yt_key_index < len(YOUTUBE_API_KEYS):
            active_key = YOUTUBE_API_KEYS[current_yt_key_index]
            url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={','.join(batch)}&key={active_key}"

            try:
                res = requests.get(url, timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    for item in data.get("items", []):
                        st = item.get("statistics", {})
                        details[item["id"]] = {
                            "views": int(st.get("viewCount", 0)),
                            "likes": int(st.get("likeCount", 0)),
                            "comments": int(st.get("commentCount", 0))
                        }
                    success = True
                    break
                elif res.status_code == 403:
                    print(f"⚠️ 유튜브 API 키 {current_yt_key_index + 1}번 할당량 소진! 다음 보조 키로 즉각 전환합니다...")
                    current_yt_key_index += 1
                    if current_yt_key_index >= len(YOUTUBE_API_KEYS):
                        print("❌ 준비된 모든 유튜브 API 키의 할당량이 소진되었습니다.")
                        break
                else:
                    time.sleep(1)
            except Exception:
                time.sleep(1.5)
                break

    return details

def record_and_prepare_data(video_items, yt_stats):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now_kst = datetime.datetime.now(KST)
    kst_now_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        SELECT video_id, views, likes, comments, logged_at 
        FROM video_metrics 
        WHERE (video_id, logged_at) IN (
            SELECT video_id, MAX(logged_at) 
            FROM video_metrics 
            WHERE views > 0
            GROUP BY video_id
        )
    """)
    last_views_map = {row[0]: {"views": row[1], "likes": row[2], "comments": row[3]} for row in cursor.fetchall()}

    processed = []
    db_rows = []

    for item in video_items:
        vid = item["video_id"]
        stats = yt_stats.get(vid)
        
        if not stats or stats.get("views", 0) == 0:
            if vid in last_views_map:
                stats = last_views_map[vid]
            else:
                stats = {"views": 0, "likes": 0, "comments": 0}
        
        up_dt = item["upload_dt_kst"]
        hrs = max(round((now_kst - up_dt).total_seconds() / 3600.0, 1), 0.1)
        vph = round(stats["views"] / hrs)
        sub_rate = round((stats["views"] / item["subscribers"] * 100), 1) if item["subscribers"] > 0 else 0

        up_str = up_dt.strftime("%m/%d %H:%M")
        time_ago_str = format_hours_to_korean(hrs)

        prev_info = last_views_map.get(vid)
        if prev_info and stats["views"] > prev_info["views"]:
            recent_growth = stats["views"] - prev_info["views"]
        else:
            recent_growth = vph

        row_data = {
            "page_id": item["page_id"],
            "video_id": vid,
            "title": item["title"],
            "channel_name": item["channel_name"],
            "subscribers": item["subscribers"],
            "political_bias": item["political_bias"],
            "format": item["format"],
            "duration": item["duration"],
            "purpose": item["purpose"],
            "thumbnail": item["thumbnail"],
            "views": stats["views"],
            "likes": stats["likes"],
            "comments": stats["comments"],
            "hours_elapsed": hrs,
            "time_ago_str": time_ago_str,
            "vph": vph,
            "recent_growth": recent_growth,
            "sub_rate": sub_rate,
            "up_str": up_str,
            "logged_at": kst_now_str
        }
        processed.append(row_data)

        if stats["views"] > 0:
            db_rows.append((
                kst_now_str, item["page_id"], vid, item["channel_name"], item["title"],
                stats["views"], stats["likes"], stats["comments"], hrs, vph,
                item["subscribers"], item["format"], item["duration"],
                item["purpose"], item["political_bias"], item["thumbnail"]
            ))

    if db_rows:
        cursor.executemany("""
            INSERT INTO video_metrics 
            (logged_at, notion_id, video_id, channel_name, title, views, likes, comments, hours_elapsed, vph, subscribers, format_tag, duration, purpose, political_bias, thumbnail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, db_rows)
        conn.commit()

    compact_history(conn)

    cursor.execute("""
        SELECT video_id, hours_elapsed, views, logged_at
        FROM video_metrics
        WHERE views > 0
        ORDER BY hours_elapsed ASC
    """)
    all_history = cursor.fetchall()
    conn.close()

    history_map = {}
    for vid, h, v, log_t in all_history:
        if vid not in history_map:
            history_map[vid] = []
        int_h = int(round(h))
        h_str = f"{int_h}시간"
        
        if history_map[vid] and history_map[vid][-1]["h"] == h_str:
            history_map[vid][-1]["v"] = v
        else:
            history_map[vid].append({"h": h_str, "v": v, "raw_h": h})

    for p in processed:
        h_data = history_map.get(p["video_id"], [])
        if len(h_data) <= 1:
            cur_h = int(round(p["hours_elapsed"]))
            init_h = max(0, cur_h - 1)
            init_v = max(0, int(p["views"] - p["vph"]))
            h_data = [{"h": f"{init_h}시간", "v": init_v}, {"h": f"{cur_h}시간", "v": p["views"]}]
        
        p["chart_data"] = h_data[-8:]

    return processed

def generate_rich_dashboard(data):
    now_kst = datetime.datetime.now(KST)
    last_update_ts = int(now_kst.timestamp() * 1000)
    last_update_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    json_data = json.dumps(data, ensure_ascii=False)

    html_template = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>정치1황 실시간 벤치마킹 대시보드 v3.7</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-color: #f8fafc;
            --card-bg: #ffffff;
            --text-main: #0f172a;
            --text-sub: #64748b;
            --border-color: #e2e8f0;
            --badge-red: #ef4444;
            --badge-dark: #1e293b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Pretendard", "Segoe UI", Roboto, sans-serif; background: var(--bg-color); color: var(--text-main); padding: 24px; }
        
        .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; }
        .header h1 { font-size: 24px; font-weight: 800; display: flex; align-items: center; gap: 8px; }
        .header-meta { text-align: right; font-size: 13px; color: var(--text-sub); }
        .header-meta .live-time { font-size: 15px; font-weight: 700; color: var(--text-main); margin-bottom: 4px; }

        .notice-card { background: #fff; border: 1px solid var(--border-color); border-radius: 12px; padding: 16px 20px; margin-bottom: 16px; font-size: 13px; line-height: 1.6; color: var(--text-sub); display: flex; justify-content: space-between; }
        .notice-card strong { color: var(--text-main); }
        .stats-summary { text-align: right; font-size: 12px; color: var(--text-sub); }
        .stats-summary .total { font-size: 16px; font-weight: 800; color: var(--text-main); margin-bottom: 4px; }
        .stats-summary .total span { color: #ef4444; }

        .keywords-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; font-size: 13px; font-weight: 600; min-height: 32px; }
        .tag { background: #eff6ff; color: #2563eb; padding: 4px 10px; border-radius: 20px; font-size: 12px; cursor: pointer; text-decoration: none; border: 1px solid #dbeafe; transition: all 0.2s; }
        .tag:hover { background: #2563eb; color: #fff; }
        .tag.active { background: #2563eb; color: #fff; font-weight: 700; border-color: #1d4ed8; }

        .controls-bar { background: #fff; border: 1px solid var(--border-color); border-radius: 12px; padding: 12px 16px; margin-bottom: 24px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
        .search-box { padding: 8px 12px; border: 1px solid var(--border-color); border-radius: 6px; font-size: 13px; width: 180px; }
        select { padding: 8px 12px; border: 1px solid var(--border-color); border-radius: 6px; font-size: 13px; background: #fff; cursor: pointer; }
        .checkbox-label { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 500; cursor: pointer; color: var(--text-main); }

        .active-filter-badge {
            background: #2563eb;
            color: #ffffff;
            font-size: 12px;
            font-weight: 700;
            padding: 6px 12px;
            border-radius: 6px;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
            box-shadow: 0 2px 4px rgba(37,99,235,0.2);
        }
        .active-filter-badge:hover {
            background: #1d4ed8;
        }

        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }
        .card { background: var(--card-bg); border-radius: 12px; border: 1px solid var(--border-color); overflow: hidden; display: flex; flex-direction: column; transition: transform 0.2s, box-shadow 0.2s; position: relative; }
        .card:hover { transform: translateY(-4px); box-shadow: 0 10px 20px rgba(0,0,0,0.06); }
        
        .thumb-wrap { position: relative; width: 100%; padding-top: 56.25%; background: #000; overflow: hidden; }
        .thumb-wrap img { position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover; }
        
        .rank-badge { position: absolute; top: 8px; left: 8px; padding: 4px 10px; font-size: 13px; font-weight: 800; border-radius: 6px; color: #fff; z-index: 2; }
        .rank-top { background: var(--badge-red); }
        .rank-normal { background: var(--badge-dark); }

        .duration-badge { position: absolute; bottom: 8px; right: 8px; background: rgba(0,0,0,0.8); color: #fff; font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; }

        .card-body { padding: 14px; display: flex; flex-direction: column; flex-grow: 1; }
        .card-title { font-size: 14px; font-weight: 700; line-height: 1.4; margin-bottom: 10px; color: var(--text-main); text-decoration: none; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; height: 38px; cursor: pointer; }
        .card-title:hover { color: #2563eb; }
        
        .meta-badges { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
        .badge-chip { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; }
        
        .chip-channel { background: #eff6ff; color: #1d4ed8; cursor: pointer; transition: all 0.2s; border: 1px solid #dbeafe; }
        .chip-channel:hover { background: #2563eb; color: #ffffff; border-color: #1d4ed8; }

        .chip-format { background: #fee2e2; color: #b91c1c; }
        .chip-bias { background: #f1f5f9; color: #475569; }

        .surge-box {
            background: #fff1f2;
            border: 1px solid #fecdd3;
            color: #e11d48;
            font-size: 12px;
            font-weight: 700;
            padding: 6px 10px;
            border-radius: 6px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .metrics-block { display: flex; flex-direction: column; gap: 5px; margin-bottom: 8px; font-size: 13px; }
        .speed-line { color: #ef4444; font-weight: 700; font-size: 14px; display: flex; align-items: center; gap: 4px; }
        .views-line { font-size: 15px; font-weight: 800; color: #0f172a; display: flex; justify-content: space-between; align-items: baseline; }
        .sub-rate { font-size: 12px; font-weight: 600; color: #d946ef; }
        .engagement-line { display: flex; gap: 12px; font-size: 12px; color: var(--text-sub); }

        .chart-box { width: 100%; height: 110px; margin-top: auto; border-top: 1px dashed var(--border-color); padding-top: 6px; }
    </style>
</head>
<body>

    <div class="header">
        <h1>👑 정치1황 실시간 벤치마킹 대시보드 <span style="font-size:12px; color:#94a3b8; font-weight:normal;">v3.7</span></h1>
        <div class="header-meta">
            <div class="live-time" id="live-clock">현재 시간: 계산 중...</div>
            <div id="update-status">마지막 업데이트: __LAST_UPDATE_STR__ (0분 경과)</div>
        </div>
    </div>

    <div class="notice-card">
        <div>
            <div>🔥 <strong>시간당:</strong> 업로드 이후 1시간 평균 조회수 (확산 속도)</div>
            <div>📈 <strong>구독자 대비:</strong> 현재 조회수 ÷ 채널 구독자 수 비율 (100% 돌파 시 알고리즘 노출)</div>
            <div>🚨 <strong>급상승 알림:</strong> 최근 1시간 내 급격히 조회수가 폭등한 영상에 붉은색 알림이 점멸합니다.</div>
            <div>💡 <strong>팁:</strong> 카드의 <strong>[📺 채널명]</strong>을 누르면 해당 채널의 영상만 즉시 모아볼 수 있습니다.</div>
        </div>
        <div class="stats-summary">
            <div class="total">검색된 영상: <span id="filtered-count">0</span>개</div>
            <div id="purpose-summary">전체: 0개 | 쇼츠 소재: 0개 | 일반: 0개 | 카피: 0개</div>
            <div id="format-summary">롱폼: 0개 | 쇼츠: 0개</div>
            <div id="bias-summary">성향별 []</div>
        </div>
    </div>

    <div class="keywords-bar" id="keywords-container">
        <span>🔥 영상 핵심 키워드:</span>
    </div>

    <div class="controls-bar">
        <input type="text" id="search-input" class="search-box" placeholder="검색어 입력..." oninput="activeKeyword = ''; activeChannel = ''; renderCards();">
        <label class="checkbox-label"><input type="checkbox" id="exclude-major" onchange="renderCards()"> 🚫 대형 미디어 제외</label>
        <label class="checkbox-label"><input type="checkbox" id="burst-only" onchange="renderCards()"> 🚨 급상승 영상만</label>
        
        <select id="filter-purpose" onchange="renderCards()">
            <option value="all" selected>전체 수집 목적</option>
            <option value="쇼츠 소재">쇼츠 소재</option>
            <option value="일반 수집대상">일반 수집대상</option>
            <option value="카피 벤치마킹">카피 벤치마킹</option>
        </select>

        <select id="filter-format" onchange="renderCards()">
            <option value="all">전체 포맷</option>
            <option value="롱폼">롱폼</option>
            <option value="쇼츠">쇼츠</option>
        </select>

        <select id="filter-hours" onchange="renderCards()">
            <option value="168">전체 기간 (7일)</option>
            <option value="72">3일 (72H) 이내</option>
            <option value="48" selected>2일 (48H) 이내</option>
            <option value="24">1일 (24H) 이내</option>
        </select>

        <select id="filter-bias" onchange="renderCards()">
            <option value="all">모든 정치성향</option>
            <option value="좌파(친명)">좌파(친명)</option>
            <option value="우파">우파</option>
            <option value="좌파(친청)">좌파(친청)</option>
            <option value="중립">중립</option>
            <option value="미배치">미배치</option>
        </select>

        <select id="sort-order" onchange="renderCards()">
            <option value="vph">⚡ 시간당 조회수 순</option>
            <option value="views" selected>🔥 총 조회수 순</option>
            <option value="sub_rate">📈 구독자 대비 비율 순</option>
            <option value="recent">🕒 최신 등록 순</option>
        </select>

        <div id="channel-filter-tag" style="display: none;"></div>
    </div>

    <div class="grid" id="cards-container"></div>

    <script>
        const rawVideos = __RAW_VIDEOS__;
        const lastUpdateTs = __LAST_UPDATE_TS__;
        const majorKeywords = ["MBC", "JTBC", "SBS", "KBS", "YTN", "채널A", "MBN", "연합뉴스", "TV조선", "조선일보", "동아일보", "중앙일보"];
        let activeKeyword = "";
        let activeChannel = ""; 

        function startLiveClock() {
            function updateClock() {
                const now = new Date();
                const year = now.getFullYear();
                const month = String(now.getMonth() + 1).padStart(2, '0');
                const date = String(now.getDate()).padStart(2, '0');
                let hours = now.getHours();
                const minutes = String(now.getMinutes()).padStart(2, '0');
                const seconds = String(now.getSeconds()).padStart(2, '0');
                const ampm = hours >= 12 ? '오후' : '오전';
                hours = hours % 12;
                hours = hours ? hours : 12;

                document.getElementById("live-clock").innerText = 
                    `현재 시간: ${year}. ${month}. ${date}. ${ampm} ${String(hours).padStart(2, '0')}:${minutes}:${seconds}`;

                const diffMinutes = Math.floor((now.getTime() - lastUpdateTs) / 60000);
                document.getElementById("update-status").innerText = 
                    `마지막 업데이트: __LAST_UPDATE_STR__ (${Math.max(0, diffMinutes)}분 경과)`;
            }
            updateClock();
            setInterval(updateClock, 1000);
        }

        function toggleKeyword(kw) {
            const searchInput = document.getElementById("search-input");
            activeChannel = ""; 
            if (activeKeyword === kw) {
                activeKeyword = "";
                searchInput.value = "";
            } else {
                activeKeyword = kw;
                searchInput.value = kw;
            }
            renderCards();
        }

        function toggleChannelFilter(channelName) {
            document.getElementById("search-input").value = "";
            activeKeyword = "";

            if (activeChannel === channelName) {
                activeChannel = "";
            } else {
                activeChannel = channelName;
            }
            renderCards();
        }

        function updateKeywordTags(currentFiltered) {
            const excludeWords = new Set([
                "영상", "뉴스", "오늘", "속보", "논란", "단독", "풀영상", "이유", "결국", "충격", "진짜", 
                "누구", "모두", "어제", "내일", "지금", "방송", "라이브", "live", "다시보기", "전계완", "기자",
                "mbc", "mbc뉴스", "뉴스데스크", "kbs", "kbs뉴스", "sbs", "sbs뉴스", "ytn", "jtbc", "생중계", "화면출처",
                "채널a", "tv조선", "mbn", "연합뉴스", "조선일보", "동아일보", "중앙일보",
                "knn", "g1", "g1현장영상", "kbc", "tjb", "cjb", "ubc", "jtv", "ikbc"
            ]);

            const words = [];
            currentFiltered.forEach(v => {
                const clean = (v.title || "").replace(/[^a-zA-Z0-9가-힣\\s]/g, " ");
                clean.split(/\\s+/).forEach(w => {
                    const low = w.toLowerCase().trim();
                    if (low.length < 2) return;
                    if (/^\\d+$/.test(low)) return;
                    if (/^\\d+(년|월|일|시|분|초|대|회|부|탄|선)$/.test(low)) return;
                    if (excludeWords.has(low)) return;
                    if (low.includes("mbc") || low.includes("kbs") || low.includes("sbs") || low.includes("ytn") || low.includes("jtbc") || low.includes("knn")) return;

                    words.push(w);
                });
            });

            const counts = {};
            words.forEach(w => { counts[w] = (counts[w] || 0) + 1; });
            
            const sortedKws = Object.entries(counts).sort((a,b) => b[1] - a[1]).slice(0, 20);

            const kwContainer = document.getElementById("keywords-container");
            kwContainer.innerHTML = '<span>🔥 영상 핵심 키워드:</span>';
            sortedKws.forEach(([kw, cnt]) => {
                const isActive = (activeKeyword === kw) ? 'active' : '';
                const a = document.createElement("a");
                a.className = `tag ${isActive}`;
                a.innerText = `#${kw} (${cnt})`;
                a.onclick = () => toggleKeyword(kw);
                kwContainer.appendChild(a);
            });
        }

        function renderCards() {
            const search = (document.getElementById("search-input").value || "").trim().toLowerCase();
            const excludeMajor = document.getElementById("exclude-major").checked;
            const burstOnly = document.getElementById("burst-only").checked;
            const purpose = document.getElementById("filter-purpose").value;
            const format = document.getElementById("filter-format").value;
            const hours = document.getElementById("filter-hours").value;
            const bias = document.getElementById("filter-bias").value;
            const sortOrder = document.getElementById("sort-order").value;

            const chTagBox = document.getElementById("channel-filter-tag");
            if (activeChannel) {
                chTagBox.style.display = "inline-flex";
                chTagBox.className = "active-filter-badge";
                chTagBox.innerHTML = `📺 선택 채널: ${activeChannel} ✕`;
                chTagBox.onclick = () => toggleChannelFilter(activeChannel);
            } else {
                chTagBox.style.display = "none";
            }

            let filtered = rawVideos.filter(v => {
                const title = (v.title || "").toLowerCase();
                const chName = (v.channel_name || "");
                const vPurpose = (v.purpose || "기타 수집");
                const vFormat = (v.format || "");
                const vBias = (v.political_bias || "미배치");

                // 채널 필터 우선 검사 (순수 채널명 일치만 통과)
                if (activeChannel) {
                    if (chName !== activeChannel) return false;
                } else if (search) {
                    if (!title.includes(search) && !chName.toLowerCase().includes(search)) return false;
                }

                if (excludeMajor && majorKeywords.some(m => chName.toUpperCase().includes(m))) return false;
                if (burstOnly && (v.recent_growth || 0) < 3000) return false;
                
                if (purpose !== "all" && vPurpose !== purpose) return false;
                if (format !== "all" && !vFormat.includes(format)) return false;
                if (hours !== "all" && (v.hours_elapsed || 0) > parseFloat(hours)) return false;
                if (bias !== "all" && !vBias.includes(bias)) return false;

                return true;
            });

            filtered.sort((a, b) => {
                if (sortOrder === "vph") return (b.vph || 0) - (a.vph || 0);
                if (sortOrder === "views") return (b.views || 0) - (a.views || 0);
                if (sortOrder === "sub_rate") return (b.sub_rate || 0) - (a.sub_rate || 0);
                if (sortOrder === "recent") return (a.hours_elapsed || 0) - (b.hours_elapsed || 0);
                return 0;
            });

            const total = filtered.length;
            const shortsMatCnt = filtered.filter(v => v.purpose === "쇼츠 소재").length;
            const normalCnt = filtered.filter(v => v.purpose === "일반 수집대상").length;
            const copyCnt = filtered.filter(v => v.purpose === "카피 벤치마킹").length;
            const longCnt = filtered.filter(v => v.format.includes("롱폼")).length;
            const shortCnt = filtered.filter(v => v.format.includes("쇼츠")).length;

            const biasObj = {};
            filtered.forEach(v => {
                const b = v.political_bias || "미배치";
                biasObj[b] = (biasObj[b] || 0) + 1;
            });
            const biasStr = Object.entries(biasObj).map(([k, v]) => `${k}: ${v}개`).join(" | ");

            document.getElementById("filtered-count").innerText = total;
            document.getElementById("purpose-summary").innerText = `전체: ${total}개 | 쇼츠 소재: ${shortsMatCnt}개 | 일반: ${normalCnt}개 | 카피: ${copyCnt}개`;
            document.getElementById("format-summary").innerText = `롱폼: ${longCnt}개 | 쇼츠: ${shortCnt}개`;
            document.getElementById("bias-summary").innerText = `성향별 [ ${biasStr} ]`;

            updateKeywordTags(filtered);

            const container = document.getElementById("cards-container");
            container.innerHTML = "";

            if (filtered.length === 0) {
                container.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 60px; color: #94a3b8; font-size: 16px;">조건에 일치하는 영상이 없습니다.</div>';
                return;
            }

            const displayLimit = Math.min(filtered.length, 150);

            for (let index = 0; index < displayLimit; index++) {
                const v = filtered[index];
                const rank = index + 1;
                const badgeClass = rank <= 3 ? "rank-top" : "rank-normal";
                const chartId = "chart-" + v.video_id;

                const safeTitle = (v.title || "").replace(/"/g, '&quot;');
                const escapedChName = (v.channel_name || "").replace(/'/g, "\\'");

                let surgeHtml = "";
                if ((v.recent_growth || 0) >= 3000) {
                    surgeHtml = `
                        <div class="surge-box">
                            🚨 최근 1시간 급상승 (+${(v.recent_growth || 0).toLocaleString()}회)
                        </div>
                    `;
                }

                const isChActive = (activeChannel === v.channel_name);
                const chActiveStyle = isChActive 
                    ? "background: #2563eb !important; color: #ffffff !important; font-weight: 700; border-color: #1d4ed8 !important;" 
                    : "";

                const card = document.createElement("div");
                card.className = "card";
                card.innerHTML = `
                    <div class="thumb-wrap">
                        <span class="rank-badge ${badgeClass}">${rank}위</span>
                        <img src="${v.thumbnail}" alt="썸네일" loading="lazy" onerror="this.src='https://i.ytimg.com/vi/${v.video_id}/hqdefault.jpg'">
                        <span class="duration-badge">${v.duration || v.format}</span>
                    </div>
                    <div class="card-body">
                        <a href="https://youtu.be/${v.video_id}" target="_blank" class="card-title" title="${safeTitle}">${v.title}</a>
                        
                        <div style="font-size: 12px; color: #64748b; margin-bottom: 8px;">
                            🕒 ${v.up_str} (${v.time_ago_str}) | 👥 구독자 ${(v.subscribers || 0).toLocaleString()}명
                        </div>

                        <div class="meta-badges">
                            <span class="badge-chip chip-channel" style="${chActiveStyle}" onclick="toggleChannelFilter('${escapedChName}')" title="클릭 시 '${v.channel_name}' 채널 영상만 모아보기">📺 ${v.channel_name}</span>
                            <span class="badge-chip chip-format">${v.format}</span>
                            <span class="badge-chip chip-bias">${v.political_bias}</span>
                        </div>

                        ${surgeHtml}

                        <div class="metrics-block">
                            <div class="speed-line">🔥 시간당: +${(v.vph || 0).toLocaleString()}</div>
                            <div class="views-line">
                                <span>총 ${(v.views || 0).toLocaleString()}회</span>
                                <span class="sub-rate">📈 구독자 대비: ${v.sub_rate}%</span>
                            </div>
                            <div class="engagement-line">
                                <span>👍 좋아요: ${(v.likes || 0).toLocaleString()}</span>
                                <span>💬 댓글: ${(v.comments || 0).toLocaleString()}</span>
                            </div>
                        </div>

                        <div class="chart-box">
                            <canvas id="${chartId}"></canvas>
                        </div>
                    </div>
                `;
                container.appendChild(card);

                setTimeout(() => {
                    const ctx = document.getElementById(chartId);
                    if (!ctx) return;
                    const cData = v.chart_data || [];
                    const labels = cData.map(c => c.h);
                    const values = cData.map(c => c.v);

                    new Chart(ctx, {
                        type: 'line',
                        data: {
                            labels: labels,
                            datasets: [{
                                data: values,
                                borderColor: '#ef4444',
                                backgroundColor: 'transparent',
                                borderWidth: 2,
                                pointBackgroundColor: '#ef4444',
                                pointRadius: 3.5,
                                fill: false,
                                tension: 0.1
                            }]
                        },
                        options: {
                            responsive: true,
                            maintainAspectRatio: false,
                            plugins: { 
                                legend: { display: false }, 
                                tooltip: { 
                                    enabled: true,
                                    callbacks: {
                                        label: function(context) {
                                            return context.parsed.y.toLocaleString() + '회';
                                        }
                                    }
                                } 
                            },
                            scales: {
                                x: { 
                                    grid: { display: false }, 
                                    ticks: { font: { size: 9 }, color: '#94a3b8' } 
                                },
                                y: {
                                    position: 'right',
                                    grid: { color: '#f1f5f9' },
                                    ticks: {
                                        font: { size: 9 },
                                        color: '#64748b',
                                        maxTicksLimit: 5,
                                        callback: function(val) {
                                            if (val >= 10000) {
                                                const man = val / 10000;
                                                return (man % 1 === 0) ? man + '만' : man.toFixed(1) + '만';
                                            }
                                            return val.toLocaleString();
                                        }
                                    }
                                }
                            }
                        }
                    });
                }, 0);
            }
        }

        startLiveClock();
        renderCards();

        setInterval(function() {
            window.location.reload();
        }, 600000);
    </script>
</body>
</html>"""

    final_html = html_template.replace("__LAST_UPDATE_TS__", str(last_update_ts)).replace("__LAST_UPDATE_STR__", last_update_str).replace("__RAW_VIDEOS__", json_data)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(final_html)
    with open("dashboard.html", "w", encoding="utf-8") as f:
        f.write(final_html)
    print(f"✨ [대시보드 렌더링 완료 v3.7] index.html 및 dashboard.html 생성 성공")

def main():
    print("▶️ 파이프라인 시작: 타겟 채널 및 최근 영상 수집")
    init_sqlite()
    sync_history_from_google_sheet()
    
    channels = fetch_target_channels()
    print(f"📌 노션 타겟 채널 {len(channels)}개 로드 완료")

    issue_videos = fetch_issue_videos(channels)
    print(f"📌 노션 최근 7일 영상 총 {len(issue_videos)}개 선별 로드 완료")

    video_ids = [v["video_id"] for v in issue_videos]
    yt_stats = get_videos_details(video_ids)

    processed_data = record_and_prepare_data(issue_videos, yt_stats)
    generate_rich_dashboard(processed_data)

if __name__ == "__main__":
    main()
