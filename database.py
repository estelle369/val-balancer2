# database.py
import json
import os
from config import TIER_SCORES

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "members.json")

def load_members():
    """members.json 파일에서 유저 데이터 불러오기"""
    if not os.path.exists(DATA_PATH):
        return {}
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def save_members(data):
    """members.json 파일에 유저 데이터 저장하기"""
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def add_or_update_member(discord_id: str, riot_id: str, tier: str):
    """유저 등록 및 수정"""
    members = load_members()
    score = TIER_SCORES.get(tier, 500)  # 기본값 실버1(500)
    
    members[str(discord_id)] = {
        "riot_id": riot_id,
        "tier": tier,
        "score": score
    }
    save_members(members)
    return score

def delete_member(discord_id: str):
    """유저 삭제"""
    members = load_members()
    str_id = str(discord_id)
    if str_id in members:
        del members[str_id]
        save_members(members)
        return True
    return False