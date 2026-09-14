import os
import pymysql
from dotenv import load_dotenv
from config import TIER_SCORES

load_dotenv()

def get_db_connection():
    """Aiven MySQL 서버 연결 함수"""
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 24875)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "defaultdb"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def init_db():
    """앱 실행 시 DB 접속 확인 및 members 테이블 자동 생성"""
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql = """
            CREATE TABLE IF NOT EXISTS members (
                user_id VARCHAR(32) PRIMARY KEY,
                riot_id VARCHAR(64) NOT NULL,
                tier VARCHAR(32) NOT NULL,
                score INT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            );
            """
            cursor.execute(sql)
            print("✅ Aiven MySQL DB 연결 및 테이블 확인 완료!")
    finally:
        connection.close()

def load_members():
    """Aiven DB에서 모든 등록 유저 불러오기"""
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT user_id, riot_id, tier, score FROM members")
            rows = cursor.fetchall()
            
            members = {}
            for row in rows:
                members[row["user_id"]] = {
                    "riot_id": row["riot_id"],
                    "tier": row["tier"],
                    "score": row["score"]
                }
            return members
    finally:
        connection.close()

def add_or_update_member(discord_id: str, riot_id: str, tier: str):
    """유저 등록 및 수정 (이미 존재하면 UPDATE, 없으면 INSERT)"""
    score = TIER_SCORES.get(tier, 500)
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql = """
            INSERT INTO members (user_id, riot_id, tier, score)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                riot_id = VALUES(riot_id),
                tier = VALUES(tier),
                score = VALUES(score);
            """
            cursor.execute(sql, (str(discord_id), riot_id, tier, score))
            return score
    finally:
        connection.close()

def delete_member(discord_id: str) -> bool:
    """Aiven DB에서 유저 등록 정보 삭제"""
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql = "DELETE FROM members WHERE user_id = %s"
            affected = cursor.execute(sql, (str(discord_id),))
            return affected > 0
    finally:
        connection.close()