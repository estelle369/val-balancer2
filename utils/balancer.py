# utils/balancer.py
from itertools import combinations

def calculate_best_teams(selected_players: list):
    """
    selected_players: [{'id': '디스코드ID', 'riot_id': '닉네임#태그', 'tier': '골드 1', 'score': 700}, ...] (10명)
    반환값: (team_a, team_b, score_a, score_b, diff)
    """
    best_diff = float('inf')
    best_teams = None

    # 10명 중 5명을 뽑는 모든 조합 (252가지 중 대칭 제외 126가지)
    all_combinations = list(combinations(selected_players, 5))

    for team_a in all_combinations:
        # team_a에 속하지 않은 나머지 5명이 team_b
        team_b = [p for p in selected_players if p not in team_a]

        score_a = sum(p['score'] for p in team_a)
        score_b = sum(p['score'] for p in team_b)

        diff = abs(score_a - score_b)

        # 팀 점수 차이가 가장 적은 조합 갱신
        if diff < best_diff:
            best_diff = diff
            best_teams = (list(team_a), team_b, score_a, score_b, diff)

    return best_teams