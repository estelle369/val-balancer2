import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from config import TIER_SCORES
from database import add_or_update_member, load_members, delete_member
from utils.balancer import calculate_best_teams
from keep_alive import keep_alive

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("❌ 에러: .env 파일에서 DISCORD_TOKEN을 불러오지 못했습니다!")
    exit()

intents = discord.Intents.default()
intents.message_content = True


class ValBot(commands.Bot):

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ 슬래시 커맨드가 동기화되었습니다!")


bot = ValBot()


# --- [디스코드 드롭다운 UI 클래스] ---
class MemberSelectView(discord.ui.View):

    def __init__(self, members_dict):
        super().__init__(timeout=180)  # 3분간 응답 없으면 무효화

        options = []
        for uid, info in list(members_dict.items())[:25]:  # 디스코드 제한상 한 번에 최대 25명
            options.append(
                discord.SelectOption(
                    label=info['riot_id'],
                    value=uid,
                    description=f"티어: {info['tier']} ({info['score']}점)",
                )
            )

        # 유저가 0명이거나 부족할 때 min/max_values 에러 방지
        total_options = len(options)
        min_val = min(10, total_options) if total_options > 0 else 1
        max_val = min(10, total_options) if total_options > 0 else 1

        self.select_menu = discord.ui.Select(
            placeholder="오늘 내전에 참여할 10명을 선택해주세요!",
            min_values=min_val,
            max_values=max_val,
            options=options if options else [
                discord.SelectOption(label="등록된 유저 없음", value="none")
            ],
            disabled=total_options < 10 # 10명 미만이면 드롭다운 비활성화
        )
        self.select_menu.callback = self.select_callback
        self.add_item(self.select_menu)
        self.members_dict = members_dict

    async def select_callback(self, interaction: discord.Interaction):
        selected_ids = self.select_menu.values

        if len(selected_ids) < 10:
            await interaction.response.send_message(
                f"❌ 10명을 정확히 선택해주세요! (현재 선택: {len(selected_ids)}명)",
                ephemeral=True,
            )
            return

        selected_players = []
        for uid in selected_ids:
            info = self.members_dict[uid]
            selected_players.append({
                "id": uid,
                "riot_id": info["riot_id"],
                "tier": info["tier"],
                "score": info["score"],
            })

        # 밸런싱 알고리즘 계산
        team_a, team_b, score_a, score_b, diff = calculate_best_teams(
            selected_players
        )

        embed = discord.Embed(
            title="⚔️ 발로란트 내전 5:5 최적 팀 밸런스",
            description=f"**두 팀 점수 차이:** {diff}점",
            color=discord.Color.gold(),
        )

        team_a_text = "\n".join(
            [f"• <@{p['id']}> (**{p['riot_id']}**) - {p['tier']}" for p in team_a]
        )
        team_b_text = "\n".join(
            [f"• <@{p['id']}> (**{p['riot_id']}**) - {p['tier']}" for p in team_b]
        )

        embed.add_field(
            name=f"🔵 A 팀 (총점: {score_a}점 / 평균: {score_a//5}점)",
            value=team_a_text,
            inline=False,
        )
        embed.add_field(
            name=f"🔴 B 팀 (총점: {score_b}점 / 평균: {score_b//5}점)",
            value=team_b_text,
            inline=False,
        )

        await interaction.response.send_message(embed=embed)


@bot.event
async def on_ready():
    print(f"✅ {bot.user.name} 봇이 성공적으로 로그인했습니다!")


# --- [명령어 1: 유저 등록] ---
@bot.tree.command(
    name="등록", description="발로란트 닉네임과 티어를 등록/수정합니다."
)
@app_commands.describe(
    riot_id="라이엇 닉네임#태그 (예: Hide on bush#KR1)", tier="티어 선택"
)
@app_commands.choices(
    tier=[app_commands.Choice(name=t, value=t) for t in TIER_SCORES.keys()]
)
async def register(
    interaction: discord.Interaction,
    riot_id: str,
    tier: app_commands.Choice[str],
):
    user_id = str(interaction.user.id)
    selected_tier = tier.value
    score = add_or_update_member(user_id, riot_id, selected_tier)

    embed = discord.Embed(title="✅ 등록 완료", color=discord.Color.green())
    embed.add_field(name="디스코드 유저", value=f"<@{user_id}>", inline=False)
    embed.add_field(name="이름", value=riot_id, inline=True)
    embed.add_field(name="티어", value=f"{selected_tier} ({score}점)", inline=True)

    await interaction.response.send_message(embed=embed)


# --- [명령어 2: 등록 목록 조회] ---
@bot.tree.command(name="목록", description="등록된 모든 유저 목록을 조회합니다.")
async def member_list(interaction: discord.Interaction):
    members = load_members()
    if not members:
        await interaction.response.send_message(
            "❌ 아직 등록된 유저가 없습니다. `/등록` 명령어로 등록해 주세요!"
        )
        return

    embed = discord.Embed(
        title="📋 내전 등록 유저 목록", color=discord.Color.blue()
    )
    text = ""
    for user_id, info in members.items():
        text += f"<@{user_id}> | **{info['riot_id']}** | {info['tier']} ({info['score']}점)\n"

    embed.description = text
    await interaction.response.send_message(embed=embed)


# --- [명령어 3: 유저 삭제] ---
@bot.tree.command(name="삭제", description="본인의 등록 정보를 삭제합니다.")
async def unregister(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    if delete_member(user_id):
        await interaction.response.send_message(
            "✅ 등록 정보가 성공적으로 삭제되었습니다."
        )
    else:
        await interaction.response.send_message("❌ 등록된 정보가 없습니다.")


# --- [명령어 4: 드롭다운으로 10명 선택 후 내전 팀 구성] ---
@bot.tree.command(
    name="내전",
    description="등록된 유저 중 10명을 드롭다운으로 선택해 팀을 구성합니다.",
)
async def create_match(interaction: discord.Interaction):
    members = load_members()

    # 인원이 0명이거나 10명 미만일 때 사전 차단
    if not members or len(members) < 10:
        current_count = len(members) if members else 0
        await interaction.response.send_message(
            f"❌ 등록된 인원이 부족합니다! 최소 10명이 필요합니다. (현재: {current_count}명)\n"
            f"`/등록` 명령어로 참여자를 추가해 주세요."
        )
        return

    view = MemberSelectView(members)
    await interaction.response.send_message(
        "👇 아래 드롭다운 메뉴에서 오늘 내전에 참가할 **10명**을 선택해 주세요!", view=view
    )


# --- [실행부] ---
if __name__ == "__main__":
    # 1. Render 포트 스캔 대응 웹서버 백그라운드 실행
    keep_alive()

    # 2. 디스코드 봇 로그인 실행
    bot.run(TOKEN)