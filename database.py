import os
import pymysql
from dotenv import load_dotenv
from config import TIER_SCORES

load_dotenv()

# --- [DB 연결 설정] ---
DB_HOST = os.getenv('DB_HOST')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')
DB_PORT = int(os.getenv('DB_PORT', 3306))

def get_db_connection():
    """Aiven MySQL 데이터베이스 연결 객체를 반환합니다."""
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def init_db():
    """앱 시작 시 members 및 guild_settings 테이블이 없으면 생성합니다."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # 1. 회원 정보 테이블
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS members (
                    user_id VARCHAR(255) PRIMARY KEY,
                    riot_id VARCHAR(255) NOT NULL,
                    tier VARCHAR(50) NOT NULL,
                    score INT NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            
            # 2. 서버별 채널 설정 테이블 (모집, 결과, A팀, B팀)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    target_channel_id BIGINT NULL,
                    result_channel_id BIGINT NULL,
                    a_team_voice_id BIGINT NULL,
                    b_team_voice_id BIGINT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
        conn.commit()
        conn.close()
        print("✅ 데이터베이스(members, guild_settings 테이블) 초기화 완료!")
    except Exception as e:
        print(f"❌ 데이터베이스 초기화 실패: {e}")

# --- [회원 관련 함수] ---

def load_members():
    """DB에서 모든 등록된 유저 정보를 불러와 딕셔너리 형태로 반환합니다."""
    members = {}
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT user_id, riot_id, tier, score FROM members")
            rows = cursor.fetchall()
            for row in rows:
                members[str(row['user_id'])] = {
                    "riot_id": row['riot_id'],
                    "tier": row['tier'],
                    "score": row['score']
                }
        conn.close()
    except Exception as e:
        print(f"⚠️ 회원 목록 불러오기 실패: {e}")
    return members

def add_or_update_member(user_id: str, riot_id: str, tier: str):
    """유저 정보를 등록 또는 수정하고 해당 티어의 점수를 반환합니다."""
    score = TIER_SCORES.get(tier, 0)
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO members (user_id, riot_id, tier, score)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE riot_id = %s, tier = %s, score = %s
            """, (user_id, riot_id, tier, score, riot_id, tier, score))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 회원 등록/수정 실패: {e}")
    return score

def delete_member(user_id: str) -> bool:
    """유저 정보를 DB에서 삭제합니다. 성공 시 True 반환"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM members WHERE user_id = %s", (user_id,))
            affected_rows = cursor.rowcount
        conn.commit()
        conn.close()
        return affected_rows > 0
    except Exception as e:
        print(f"⚠️ 회원 삭제 실패: {e}")
        return False

# --- [서버 채널 설정 관련 DB 함수] ---

def save_guild_setting(guild_id: int, column_name: str, value: int):
    """특정 서버의 채널 설정 값을 DB에 저장 또는 업데이트합니다."""
    allowed_columns = ['target_channel_id', 'result_channel_id', 'a_team_voice_id', 'b_team_voice_id']
    if column_name not in allowed_columns:
        print(f"⚠️ 허용되지 않는 설정 컬럼: {column_name}")
        return

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(f"""
                INSERT INTO guild_settings (guild_id, {column_name})
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE {column_name} = %s;
            """, (guild_id, value, value))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 서버 설정 저장 실패 ({column_name}): {e}")

def load_all_guild_settings():
    """모든 서버의 채널 설정 정보를 불러와 4개의 딕셔너리로 반환합니다."""
    target_channels = {}
    result_channels = {}
    a_team_voice_channels = {}
    b_team_voice_channels = {}
    
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT guild_id, target_channel_id, result_channel_id, a_team_voice_id, b_team_voice_id FROM guild_settings")
            rows = cursor.fetchall()
            for row in rows:
                g_id = int(row['guild_id'])
                if row['target_channel_id']:
                    target_channels[g_id] = int(row['target_channel_id'])
                if row['result_channel_id']:
                    result_channels[g_id] = int(row['result_channel_id'])
                if row['a_team_voice_id']:
                    a_team_voice_channels[g_id] = int(row['a_team_voice_id'])
                if row['b_team_voice_id']:
                    b_team_voice_channels[g_id] = int(row['b_team_voice_id'])
        conn.close()
    except Exception as e:
        print(f"⚠️ 서버 설정 불러오기 실패: {e}")
        
    return target_channels, result_channels, a_team_voice_channels, b_team_voice_channels