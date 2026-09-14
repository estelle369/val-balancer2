import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from config import TIER_SCORES
# database 모듈에서 init_db 추가 임포트
from database import add_or_update_member, load_members, delete_member, init_db
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


# --- [디스코드 버튼 기반 내전 모집 UI 클래스] ---
class MatchRecruitView(discord.ui.View):

    def __init__(self, members_dict):
        super().__init__(timeout=None)  # 모집 완료 전까지 타임아웃 없이 유지
        self.members_dict = members_dict
        self.participants = []  # 참가 등록한 discord.Member 객체 리스트

    @discord.ui.button(label="참가 ⚔️", style=discord.ButtonStyle.success, custom_id="match_join_btn")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)

        # 1. DB 등록 여부 확인 (미등록 유저 차단)
        if user_id not in self.members_dict:
            await interaction.response.send_message(
                "❌ 등록되지 않은 유저입니다. `/등록` 명령어로 먼저 등록해 주세요!",
                ephemeral=True
            )
            return

        # 2. 중복 참가 확인
        if interaction.user in self.participants:
            await interaction.response.send_message("이미 참가 명단에 있습니다!", ephemeral=True)
            return

        # 3. 인원 초과 확인
        if len(self.participants) >= 10:
            await interaction.response.send_message("이미 10명 모집이 완료되었습니다.", ephemeral=True)
            return

        self.participants.append(interaction.user)

        # 🎯 10명이 모두 모였을 때 -> 자동 밸런싱 실행
        if len(self.participants) == 10:
            # 모집 완료 메시지 업데이트 (버튼 제거)
            await interaction.response.edit_message(
                content="✅ **10명 모집이 완료되었습니다! 팀 밸런싱을 진행합니다.**",
                view=None
            )

            # DB 정보 추출 및 밸런스 계산에 필요한 데이터 포맷팅
            selected_players = []
            for member in self.participants:
                uid = str(member.id)
                info = self.members_dict[uid]
                selected_players.append({
                    "id": uid,
                    "riot_id": info["riot_id"],
                    "tier": info["tier"],
                    "score": info["score"],
                })

            # 밸런싱 알고리즘 계산 (기존 balancer 모듈 호출)
            team_a, team_b, score_a, score_b, diff = calculate_best_teams(selected_players)

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

            # 채널에 최종 결과 Embed 송출
            await interaction.channel.send(embed=embed)

        else:
            # 10명이 채워지기 전 실시간 명단 업데이트
            user_mentions = "\n".join([f"{i+1}. {p.mention} (**{self.members_dict[str(p.id)]['riot_id']}**)" for i, p in enumerate(self.participants)])
            await interaction.response.edit_message(
                content=f"📢 **오늘 내전 참가자 모집중! ({len(self.participants)}/10)**\n\n**[현재 참가자 명단]**\n{user_mentions}",
                view=self
            )

    @discord.ui.button(label="취소 ❌", style=discord.ButtonStyle.danger, custom_id="match_leave_btn")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user not in self.participants:
            await interaction.response.send_message("참가 신청을 하지 않은 상태입니다.", ephemeral=True)
            return

        self.participants.remove(interaction.user)

        user_mentions = "\n".join([f"{i+1}. {p.mention} (**{self.members_dict[str(p.id)]['riot_id']}**)" for i, p in enumerate(self.participants)]) if self.participants else "없음"
        
        await interaction.response.edit_message(
            content=f"📢 **오늘 내전 참가자 모집중! ({len(self.participants)}/10)**\n\n**[현재 참가자 명단]**\n{user_mentions}",
            view=self
        )


@bot.event
async def on_ready():
    print(f"✅ {bot.user.name} 봇이 성공적으로 로그인했습니다!")


# --- [명령어 1: 유저 등록] ---
@bot.tree.command(
    name="등록", description="이름과 티어를 등록/수정합니다."
)
@app_commands.describe(
    이름="이름을 입력해 주세요", tier="티어 선택"
)
@app_commands.choices(
    tier=[app_commands.Choice(name=t, value=t) for t in TIER_SCORES.keys()]
)
async def register(
    interaction: discord.Interaction,
    이름: str,
    tier: app_commands.Choice[str],
):
    user_id = str(interaction.user.id)
    selected_tier = tier.value
    # 입력받은 '이름' 변수를 기존 DB 함수(add_or_update_member)의 riot_id 자리에 전달
    score = add_or_update_member(user_id, 이름, selected_tier)

    embed = discord.Embed(title="✅ 등록 완료", color=discord.Color.green())
    embed.add_field(name="디스코드 유저", value=f"<@{user_id}>", inline=False)
    embed.add_field(name="이름", value=이름, inline=True)
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


# --- [명령어 4: [참가]/[취소] 버튼 모집 후 10명 완성 시 자동 내전 팀 구성] ---
@bot.tree.command(
    name="내전",
    description="참가/취소 버튼으로 10명을 모집하여 자동으로 팀을 구성합니다.",
)
async def create_match(interaction: discord.Interaction):
    members = load_members()

    view = MatchRecruitView(members)
    await interaction.response.send_message(
        "📢 **오늘 내전 참가자 모집중! (0/10)**\n아래 **[참가 ⚔️]** 버튼을 눌러주세요!",
        view=view
    )


# --- [실행부] ---
if __name__ == "__main__":
    # 1. Aiven 클라우드 DB 접속 확인 및 테이블 자동 생성
    init_db()

    # 2. Render 포트 스캔 대응 웹서버 백그라운드 실행
    keep_alive()

    # 3. 디스코드 봇 로그인 실행
    bot.run(TOKEN)