name: YouTube Trend Auto Tracker

on:
  schedule:
    # 한국 시간 기준 매 2시간마다 자동 실행 (무료 시간 여유 확보)
    - cron: '0 */2 * * *'
  workflow_dispatch: # 관리자가 필요할 때 수동으로 즉시 실행 버튼 지원

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  track-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - name: 저장소 코드 가져오기
        uses: actions/checkout@v4

      - name: 파이썬 설치
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: 필요 라이브러리 설치
        run: |
          pip install -r requirements.txt

      - name: 트래커 스크립트 실행
        run: |
          python tracker.py

      - name: 누적 DB 파일 GitHub에 커밋 및 저장
        run: |
          git config --global user.name "github-actions[bot]"
          git config --global user.email "github-actions[bot]@users.noreply.github.com"
          git add tracker.db
          git diff --quiet && git diff --staged --quiet || git commit -m "Auto-update tracker database"
          git push
        continue-on-error: true

      - name: Pages 배포 설정
        uses: actions/configure-pages@v4

      - name: index.html 웹 업로드 준비
        uses: actions/upload-pages-artifact@v3
        with:
          path: '.'

      - name: GitHub Pages 웹 배포
        id: deployment
        uses: actions/deploy-pages@v4
