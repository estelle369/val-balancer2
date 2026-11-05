val-balancer2

디스코드 기반 발로란트 내전 자동 모집 및 MMR 기반 5v5 팀 밸런싱 봇 프로젝트입니다.

📌 Project Overview

val-balancer2는 디스코드 채널 내에서 참가 신청을 받고, 등록된 유저의 티어 및 MMR 데이터를 바탕으로 공정한 5v5 팀을 자동으로 생성 및 매칭해주는 비동기 기반 디스코드 봇입니다.

프로젝트명: val-balancer2

개발 환경: Python 3.12+

주요 특징: 디바운싱 및 동시성 제어를 통한 API Rate Limit 최소화, Aiven Cloud MySQL 세션 지속 유지, 자동 이모지 반응 기반 모집 시스템

🛠 Tech Stack

Language: Python 3.12+

Framework / Library: discord.py, asyncio, PyMySQL, Flask

Database: Aiven Cloud MySQL

Deployment: Render (with keep_alive.py)

📁 Project Structure

val-balancer2/
├── main.py              # 비동기 이벤트 리스너, 디바운싱 타이머, 이모지 감지, 슬래시 커맨드 통합
├── database.py          # Aiven MySQL CRUD 및 커넥션 관리 (@tasks.loop 5분 핑 쿼리 포함)
├── config.py            # 티어별 MMR 점수 매핑 테이블 및 설정값 관리
├── keep_alive.py        # Render 24시간 무중단 가동용 Flask 웹 서버
└── utils/
    └── balancer.py      # MMR 점수 기반 5v5 최적 팀 분배 알고리즘 (calculate_best_teams)


✨ Implemented Features & Technical Assets

자동 모집 및 결과 분리 시스템

지정된 채널에서 반응 이모지(✅, ❌)를 통한 자동 모집 진행

In-Place Edit 방식을 적용하여 메시지 도배 방지 및 전용 결과 채널 출력 분리

MMR 기반 5v5 최적 팀 밸런싱

utils/balancer.py 알고리즘을 통한 티어별 점수산출 및 밸런스 균형 알고리즘 수행

대리 참가 및 DB 유저 관리

ProxySelectMenu 드롭다운 메뉴를 지원하여 대리 참가/취소 기능 제공

Aiven Cloud MySQL 연동을 통한 유저 데이터 연동

트러블슈팅 및 성능 최적화

디스코드 UI 제한 방어: Dropdown 항목 수 25개 제한을 options[:25] 슬라이싱 기법 및 예외 문구 처리로 안전하게 방어

동시성 & Rate Limit 극복: asyncio.Lock 제어 및 0.8~1초 디바운싱(Debouncing) 패턴으로 API 호출 수 80% 이상 감소

PaaS 세션 끊김 방지: 5분 주기 SELECT 1; 핑 쿼리 루프와 keep_alive.py Flask 서버 결합으로 24시간 무중단 가동 달성

📝 Recent Change Log

2026.10: 초기 버전 구축 완료, 동시성 락 및 디바운싱 최적화 적용 완료