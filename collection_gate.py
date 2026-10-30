"""Avoid duplicate hourly collection when cron-job.org and GitHub both fire."""

import argparse
from contextlib import closing
import datetime as dt
import os
from pathlib import Path
import sqlite3

KST = dt.timezone(dt.timedelta(hours=9))


def collection_decision(database, now, event="workflow_dispatch", force=False,
                        minimum_minutes=50):
    if force or event == "push":
        return True, "코드 변경 또는 강제 실행: 수집합니다."
    database = Path(database)
    if not database.exists():
        return True, "이전 수집 DB 없음: 수집합니다."
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT MAX(logged_at) FROM video_metrics").fetchone()
    if not row or not row[0]:
        return True, "이전 측정 기록 없음: 수집합니다."
    measured = dt.datetime.fromisoformat(row[0])
    if measured.tzinfo is None:
        measured = measured.replace(tzinfo=KST)
    age = (now - measured).total_seconds() / 60
    if age < -5:
        raise ValueError("수집 기록이 현재보다 5분 이상 미래입니다. 시간 설정을 확인하세요.")
    if age < minimum_minutes:
        return False, f"최근 수집 {max(age, 0):.1f}분 전: 중복 수집을 건너뜁니다."
    return True, f"최근 수집 {age:.1f}분 전: 수집합니다."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="tracker.db")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    collect, reason = collection_decision(args.database, dt.datetime.now(KST),
                                          event=args.event, force=args.force)
    print(reason)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"collect={str(collect).lower()}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"### 수집 실행 확인\n\n{reason}\n")


if __name__ == "__main__":
    main()
