import os
import threading
from flask import Flask

app = Flask(__name__)


@app.route('/')
def home():
  return "OK", 200


def run_flask():
  # Render가 제공하는 PORT 환경변수를 우선 사용 (기본 10000)
  port = int(os.environ.get("PORT", 10000))
  # Werkzeug 서빙 로거 및 멀티스레드 바인딩 안정화
  app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def keep_alive():
  # Flask 서버 스레드 생성 및 실행
  server_thread = threading.Thread(target=run_flask, daemon=True)
  server_thread.start()
  print("🌐 [Keep-Alive] Flask 서버 스레드가 시작되었습니다.")