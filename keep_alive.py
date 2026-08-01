import os
import logging
from threading import Thread
from flask import Flask

# Flask 서버 기본 로그 출력 줄이기 (선택)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
    # Render가 주입하는 PORT 환경변수를 가져옵니다. (기본값 8080)
    port = int(os.environ.get("PORT", 8080))
    print(f"🌐 Flask 웹 서버가 {port} 포트에서 시작됩니다...")
    # 0.0.0.0 으로 바인딩해야 외부(Render 포트 스캔)에서 접근 가능합니다.
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True # 메인 스레드 종료 시 함께 종료
    t.start()