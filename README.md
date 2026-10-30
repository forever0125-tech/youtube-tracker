# youtube-tracker

## 외부 예약 보완

GitHub의 매시간 17분 예약을 유지하고 cron-job.org에서 매시간 37분에
동일 워크플로를 호출합니다. PC와 Aside가 꺼져 있어도 서버에서 실행됩니다.
작업이 접수된 뒤 최신 main의 DB를 확인하여 50분 이내에 수집한 경우에는
수집·배포를 모두 건너뜁니다. 기존 공통 concurrency 그룹으로 중복 실행을
직렬화합니다. 코드 push나 수동 force=true는 즉시 수집합니다.

cron-job.org 설정:

- 이름: YouTube Tracker · 매시간 수집 보완
- URL: https://api.github.com/repos/forever0125-tech/youtube-tracker/actions/workflows/tracker.yml/dispatches
- 요청 방식: POST
- 시간대: Asia/Seoul
- 예약: 매시간 37분 (37 * * * *)
- 헤더: Accept = application/vnd.github+json
- 헤더: Content-Type = application/json
- 헤더: X-GitHub-Api-Version = 2022-11-28
- 헤더: Authorization = Bearer <전용 GitHub 토큰>
- 본문: {"ref":"main","inputs":{"force":false}}

토큰은 이 저장소에만 접근하는 fine-grained 토큰을 만들고 Actions: Read and
write 권한만 부여합니다. 토큰 값은 cron-job.org의 Authorization 헤더에만
직접 입력하고 Git, README, 채팅, 스크린샷에 저장하지 않습니다.
만료 시 교체하고 cron-job.org의 실패 알림으로 호출 실패를 확인합니다.

예약 호출의 성공은 GitHub의 접수 성공입니다. 실제 수집·배포 성공은
GitHub Actions의 tracker 단계, 배포 결과, 사이트의 최근 실제 수집 시각으로
검증합니다. 외부 예약도 GitHub 실행 서버 전체 장애까지 해결하지는 않습니다.
