import os
import re
import json
import sqlite3
import datetime
import csv
import io
import requests

# GitHub 환경변수(금고) 우선 사용, 로컬 실행 시 전달된 키 사용
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "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")
TARGET_DB_ID = "353a73c83d0780568544f053bfdca3bf"
ISSUE_DB_ID = "353a73c83d07800a8aead62083146a44"
YOUTUBE_API_KEY = "AIzaSyBFPe0eYPI99YfeH-P89OPJvUAMgOzXLKc"
LOG_SHEET_ID = "18UkL2pTTnpuGVqrafQC2uKP2C6juda_4ViJejYoXt80"

DB_FILE = "tracker.db"
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
    conn.commit()
    conn.close()

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
        body = {"page_size": 100}
        if next_cursor:
            body["start_cursor"] = next_cursor
            
        res = requests.post(url, headers=NOTION_HEADERS, data=json.dumps(body))
        data = res.json()
        
        if "results" not in data:
            print(f"⚠️ 노션 타겟 채널 API 응답 에러: {data.get('message', res.text)}")
            break
            
        for p in data.get("results", []):
            page_id = p["id"]
            props = p.get("properties", {})
            title_list = props.get("채널명", {}).get("title", [])
            c_name = title_list[0].get("plain_text", "알수없음") if title_list else "알수없음"
            subs = props.get("구독자수", {}).get("number", 0) or 0
            bias = props.get("정치성향", {}).get("select", {}).get("name", "미배치") if props.get("정치성향", {}).get("select") else "미배치"
            
            is_copy = props.get("카피 벤치마킹", {}).get("checkbox", False)
            is_target = props.get("수집대상", {}).get("checkbox", False)

            channels[page_id] = {
                "channel_name": c_name,
                "subscribers": subs,
                "political_bias": bias,
                "is_copy": is_copy,
                "is_target": is_target
            }
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")
    return channels

def fetch_issue_videos(channel_meta_map):
    url = f"https://api.notion.com/v1/databases/{ISSUE_DB_ID}/query"
    pages = []
    has_more = True
    next_cursor = None

    while has_more:
        body = {"page_size": 100}
        if next_cursor:
            body["start_cursor"] = next_cursor
            
        res = requests.post(url, headers=NOTION_HEADERS, data=json.dumps(body))
        data = res.json()
        
        if "results" not in data:
            print(f"⚠️ 노션 이슈 영상 API 응답 에러: {data.get('message', res.text)}")
            break
            
        pages.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    video_items = []
    now_kst = datetime.datetime.now(KST)
    target_channel_names = {v["channel_name"]: page_id for page_id, v in channel_meta_map.items()}

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
            txt_name = txt_list[0].get("plain_text", "")
            
        if not matched_c_meta and txt_name:
            for c_name, pid in target_channel_names.items():
                if c_name and (c_name in txt_name or txt_name in c_name):
                    matched_c_meta = channel_meta_map[pid]
                    break

        c_name = matched_c_meta["channel_name"] if matched_c_meta else (txt_name or "알수없음")
        subs = matched_c_meta["subscribers"] if matched_c_meta else 0
        bias = matched_c_meta["political_bias"] if matched_c_meta else "미배치"

        fmt = p.get("포맷", {}).get("select", {}).get("name", "🔴 롱폼") if p.get("포맷", {}).get("select") else "🔴 롱폼"
        dur = p.get("영상 길이", {}).get("rich_text", [{}])[0].get("plain_text", "") if p.get("영상 길이", {}).get("rich_text") else ""
        collect_method = p.get("수집 방식", {}).get("select", {}).get("name", "") if p.get("수집 방식", {}).get("select") else ""
        
        if matched_c_meta and matched_c_meta.get("is_target", False):
            purpose_val = "일반 수집대상"
        elif (matched_c_meta and matched_c_meta.get("is_copy", False)) or ("직접" in collect_method or "스크랩" in collect_method):
            purpose_val = "카피 벤치마킹"
        else:
            purpose_val = "일반 수집대상" if "자동" in collect_method else "기타 수집"
        
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
    details = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={','.join(batch)}&key={YOUTUBE_API_KEY}"
        res = requests.get(url).json()
        for item in res.get("items", []):
            st = item.get("statistics", {})
            details[item["id"]] = {
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0))
            }
    return details

def record_and_prepare_data(video_items, yt_stats):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now_kst = datetime.datetime.now(KST)
    kst_now_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        SELECT video_id, views, logged_at 
        FROM video_metrics 
        WHERE (video_id, logged_at) IN (
            SELECT video_id, MAX(logged_at) 
            FROM video_metrics 
            WHERE views > 0
            GROUP BY video_id
        )
    """)
    last_views_map = {row[0]: row[1] for row in cursor.fetchall()}

    processed = []
    db_rows = []

    for item in video_items:
        vid = item["video_id"]
        stats = yt_stats.get(vid, {"views": 0, "likes": 0, "comments": 0})
        
        up_dt = item["upload_dt_kst"]
        hrs = max(round((now_kst - up_dt).total_seconds() / 3600.0, 1), 0.1)
        vph = round(stats["views"] / hrs)
        sub_rate = round((stats["views"] / item["subscribers"] * 100), 1) if item["subscribers"] > 0 else 0

        up_str = up_dt.strftime("%m/%d %H:%M")
        time_ago_str = format_hours_to_korean(hrs)

        prev_view = last_views_map.get(vid)
        if prev_view is not None and stats["views"] > prev_view:
            recent_growth = stats["views"] - prev_view
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

        db_rows.append((
            kst_now_str, item["page_id"], vid, item["channel_name"], item["title"],
            stats["views"], stats["likes"], stats["comments"], hrs, vph,
            item["subscribers"], item["format"], item["duration"],
            item["purpose"], item["political_bias"], item["thumbnail"]
        ))

    cursor.executemany("""
        INSERT INTO video_metrics 
        (logged_at, notion_id, video_id, channel_name, title, views, likes, comments, hours_elapsed, vph, subscribers, format_tag, duration, purpose, political_bias, thumbnail)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, db_rows)
    conn.commit()

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
    now_str = now_kst.strftime("%Y. %m. %d. %p %I:%M:%S").replace("AM", "오전").replace("PM", "오후")
    last_update = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    json_data = json.dumps(data, ensure_ascii=False)

    html_template = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>정치1황 실시간 벤치마킹 대시보드</title>
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
        .card-title { font-size: 14px; font-weight: 700; line-height: 1.4; margin-bottom: 10px; color: var(--text-main); text-decoration: none; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; height: 38px; }
        .card-title:hover { color: #2563eb; }
        
        .meta-badges { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
        .badge-chip { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; }
        .chip-channel { background: #eff6ff; color: #1d4ed8; }
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
        <h1>👑 정치1황 실시간 벤치마킹 대시보드</h1>
        <div class="header-meta">
            <div class="live-time">현재 시간: __NOW_STR__</div>
            <div>마지막 업데이트: __LAST_UPDATE__ (0분 경과)</div>
        </div>
    </div>

    <div class="notice-card">
        <div>
            <div>🔥 <strong>시간당:</strong> 업로드 이후 1시간 평균 조회수 (확산 속도)</div>
            <div>📈 <strong>구독자 대비:</strong> 현재 조회수 ÷ 채널 구독자 수 비율 (100% 돌파 시 알고리즘 노출)</div>
            <div>🚨 <strong>급상승 알림:</strong> 최근 1시간 내 급격히 조회수가 폭등한 영상에 붉은색 알림이 점멸합니다.</div>
        </div>
        <div class="stats-summary">
            <div class="total">검색된 영상: <span id="filtered-count">0</span>개</div>
            <div id="purpose-summary">수집: 0개 | 카피 벤치마킹: 0개 | 일반 수집대상: 0개</div>
            <div id="format-summary">롱폼: 0개 | 쇼츠: 0개</div>
            <div id="bias-summary">성향별 []</div>
        </div>
    </div>

    <div class="keywords-bar" id="keywords-container">
        <span>🔥 영상 핵심 키워드:</span>
    </div>

    <div class="controls-bar">
        <input type="text" id="search-input" class="search-box" placeholder="검색어 입력..." oninput="activeKeyword = ''; renderCards();">
        <label class="checkbox-label"><input type="checkbox" id="exclude-major" onchange="renderCards()"> 🚫 대형 미디어 제외</label>
        <label class="checkbox-label"><input type="checkbox" id="burst-only" onchange="renderCards()"> 🚨 급상승 영상만</label>
        
        <select id="filter-purpose" onchange="renderCards()">
            <option value="all">전체 수집 목적</option>
            <option value="카피 벤치마킹">카피 벤치마킹</option>
            <option value="일반 수집대상">일반 수집대상</option>
        </select>

        <select id="filter-format" onchange="renderCards()">
            <option value="all">전체 포맷</option>
            <option value="롱폼">롱폼</option>
            <option value="쇼츠">쇼츠</option>
        </select>

        <!-- 전체 기간은 7일(168H), 기본 선택값은 2일(48H)로 지정 -->
        <select id="filter-hours" onchange="renderCards()">
            <option value="168">전체 기간 (7일)</option>
            <option value="72">3일 (72H) 이내</option>
            <option value="48" selected>2일 (48H) 이내</option>
            <option value="24">1일 (24H) 이내</option>
            <option value="all">제한 없음 (전체 누적)</option>
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
            <option value="vph" selected>⚡ 시간당 조회수 순</option>
            <option value="views">🔥 총 조회수 순</option>
            <option value="sub_rate">📈 구독자 대비 비율 순</option>
            <option value="recent">🕒 최신 등록 순</option>
        </select>
    </div>

    <div class="grid" id="cards-container"></div>

    <script>
        const rawVideos = __RAW_VIDEOS__;
        const majorKeywords = ["MBC", "JTBC", "SBS", "KBS", "YTN", "채널A", "MBN", "연합뉴스", "TV조선", "조선일보", "동아일보", "중앙일보"];
        let activeKeyword = "";

        function toggleKeyword(kw) {
            const searchInput = document.getElementById("search-input");
            if (activeKeyword === kw) {
                activeKeyword = "";
                searchInput.value = "";
            } else {
                activeKeyword = kw;
                searchInput.value = kw;
            }
            renderCards();
        }

        function updateKeywordTags(currentFiltered) {
            const words = [];
            currentFiltered.forEach(v => {
                const clean = (v.title || "").replace(/[^\\w\\s가-힣]/g, " ");
                clean.split(/\\s+/).forEach(w => {
                    if (w.length >= 2 && !["영상", "뉴스", "오늘", "속보", "논란", "단독", "풀영상", "이유", "결국"].includes(w)) {
                        words.push(w);
                    }
                });
            });

            const counts = {};
            words.forEach(w => { counts[w] = (counts[w] || 0) + 1; });
            const sortedKws = Object.entries(counts).sort((a,b) => b[1] - a[1]).slice(0, 10);

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

            let filtered = rawVideos.filter(v => {
                const title = (v.title || "").toLowerCase();
                const chName = (v.channel_name || "");
                const vPurpose = (v.purpose || "기타 수집");
                const vFormat = (v.format || "");
                const vBias = (v.political_bias || "미배치");

                if (search && !title.includes(search) && !chName.toLowerCase().includes(search)) return false;
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
            const copyCnt = filtered.filter(v => v.purpose === "카피 벤치마킹").length;
            const normalCnt = filtered.filter(v => v.purpose === "일반 수집대상").length;
            const longCnt = filtered.filter(v => v.format.includes("롱폼")).length;
            const shortCnt = filtered.filter(v => v.format.includes("쇼츠")).length;

            const biasObj = {};
            filtered.forEach(v => {
                const b = v.political_bias || "미배치";
                biasObj[b] = (biasObj[b] || 0) + 1;
            });
            const biasStr = Object.entries(biasObj).map(([k, v]) => `${k}: ${v}개`).join(" | ");

            document.getElementById("filtered-count").innerText = total;
            document.getElementById("purpose-summary").innerText = `수집: ${total}개 | 카피 벤치마킹: ${copyCnt}개 | 일반 수집대상: ${normalCnt}개`;
            document.getElementById("format-summary").innerText = `롱폼: ${longCnt}개 | 쇼츠: ${shortCnt}개`;
            document.getElementById("bias-summary").innerText = `성향별 [ ${biasStr} ]`;

            updateKeywordTags(filtered);

            const container = document.getElementById("cards-container");
            container.innerHTML = "";

            if (filtered.length === 0) {
                container.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 60px; color: #94a3b8; font-size: 16px;">조건에 일치하는 영상이 없습니다. (상단 기간 필터를 [전체 기간]으로 변경해 보세요)</div>';
                return;
            }

            const displayLimit = Math.min(filtered.length, 150);

            for (let index = 0; index < displayLimit; index++) {
                const v = filtered[index];
                const rank = index + 1;
                const badgeClass = rank <= 3 ? "rank-top" : "rank-normal";
                const chartId = "chart-" + v.video_id;

                let surgeHtml = "";
                if ((v.recent_growth || 0) >= 3000) {
                    surgeHtml = `
                        <div class="surge-box">
                            🚨 최근 1시간 급상승 (+${(v.recent_growth || 0).toLocaleString()}회)
                        </div>
                    `;
                }

                const card = document.createElement("div");
                card.className = "card";
                card.innerHTML = `
                    <div class="thumb-wrap">
                        <span class="rank-badge ${badgeClass}">${rank}위</span>
                        <img src="${v.thumbnail}" alt="썸네일" loading="lazy" onerror="this.src='https://i.ytimg.com/vi/${v.video_id}/hqdefault.jpg'">
                        <span class="duration-badge">${v.duration || v.format}</span>
                    </div>
                    <div class="card-body">
                        <a href="https://youtu.be/${v.video_id}" target="_blank" class="card-title">${v.title}</a>
                        
                        <div style="font-size: 12px; color: #64748b; margin-bottom: 8px;">
                            🕒 ${v.up_str} (${v.time_ago_str}) | 👥 구독자 ${(v.subscribers || 0).toLocaleString()}명
                        </div>

                        <div class="meta-badges">
                            <span class="badge-chip chip-channel">📺 ${v.channel_name}</span>
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

        renderCards();
    </script>
</body>
</html>"""

    final_html = html_template.replace("__NOW_STR__", now_str).replace("__LAST_UPDATE__", last_update).replace("__RAW_VIDEOS__", json_data)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(final_html)
    with open("dashboard.html", "w", encoding="utf-8") as f:
        f.write(final_html)
    print(f"✨ [대시보드 렌더링 완료] index.html 및 dashboard.html 생성 성공")

def main():
    print("▶️ 파이프라인 시작: 타겟 채널 및 최근 영상 수집")
    init_sqlite()
    sync_history_from_google_sheet()
    
    channels = fetch_target_channels()
    print(f"📌 노션 타겟 채널 {len(channels)}개 로드 완료")

    issue_videos = fetch_issue_videos(channels)
    print(f"📌 노션 영상 총 {len(issue_videos)}개 로드 완료")

    video_ids = [v["video_id"] for v in issue_videos]
    yt_stats = get_videos_details(video_ids)

    processed_data = record_and_prepare_data(issue_videos, yt_stats)
    generate_rich_dashboard(processed_data)

if __name__ == "__main__":
    main()
