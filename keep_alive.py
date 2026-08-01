import os
from threading import Thread
from flask import Flask

# Flask 웹 서버 객체 생성
app = Flask('')


@app.route('/')
def main():
  # Render 스캔 및 웹 접속 테스트용 텍스트
  return "Bot is alive!"


def run():
  # Render가 부여하는 환경변수 PORT를 읽어오고, 없을 경우 기본값 10000 사용
  port = int(os.environ.get('PORT', 10000))
  # 외부 접근이 가능하도록 host를 '0.0.0.0'으로 지정하는 것이 매우 중요합니다!
  app.run(host='0.0.0.0', port=port)


def keep_alive():
  # 디스코드 봇 동작을 방해하지 않도록 별도 쓰레드에서 웹서버 실행
  server = Thread(target=run)
  server.daemon = True  # 메인 프로세스 종료 시 함께 종료되도록 설정
  server.start()