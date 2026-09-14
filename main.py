import os
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Select, button, Button
from dotenv import load_dotenv

from config import TIER_SCORES
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

# --- [대리 참가/취소용 드롭다운 메뉴] ---
class ProxySelectMenu(Select):
    def __init__(self, parent_view, action_type="add"):
        self.parent_view = parent_view
        self.action_type = action_type
        members = load_members()
        options = []

        if action_type == "add":
            current_ids = [p["id"] for p in self.parent_view.participants]
            for uid, info in members.items():
                if uid not in current_ids:
                    options.append(discord.SelectOption(
                        label=info["riot_id"],
                        value=uid,
                        description=f"티어: {info['tier']} ({info['score']}점)"
                    ))
            placeholder = "명단에 추가할 유저를 선택하세요..."
        else:
            for p in self.parent_view.participants:
                options.append(discord.SelectOption(
                    label=p["name"],
                    value=p["id"],
                    description=f"티어: {p['tier']} ({p['score']}점)"
                ))
            placeholder = "명단에서 제외할 유저를 선택하세요..."

        if not options:
            options = [discord.SelectOption(label="선택 가능한 유저가 없습니다.", value="none")]

        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        if self.values[0] == "none":
            await interaction.followup.send("선택 가능한 유저가 없습니다.", ephemeral=True)
            return

        selected_id = self.values[0]
        members = load_members()

        if self.action_type == "add":
            if len(self.parent_view.participants) >= 10:
                await interaction.followup.send("이미 10명 모집이 완료되었습니다.", ephemeral=True)
                return
            
            info = members[selected_id]
            self.parent_view.participants.append({
                "id": selected_id,
                "name": info["riot_id"],
                "tier": info["tier"],
                "score": info["score"],
                "is_guest": str(selected_id).startswith("guest_")
            })
            await interaction.followup.send(f"✅ **{info['riot_id']}** 님이 대리로 참가되었습니다.", ephemeral=True)
        else:
            self.parent_view.participants = [p for p in self.parent_view.participants if p["id"] != selected_id]
            await interaction.followup.send("✅ 명단에서 정상적으로 제외되었습니다.", ephemeral=True)

        # 🎯 대리 참가/취소 즉시 메인 모집 메시지 강제 업데이트!
        await self.parent_view.update_message_direct(interaction.message)

class ProxyView(View):
    def __init__(self, parent_view, action_type="add"):
        super().__init__(timeout=60)
        self.add_item(ProxySelectMenu(parent_view, action_type))

# --- [디스코드 버튼 기반 내전 모집 UI 클래스] ---
class MatchRecruitView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.participants = [] # dict 리스트 (id, name, tier, score, is_guest)
        self.message = None

    async def update_message_direct(self, fallback_msg=None):
        """드롭다운에서 대리참가/취소 시 원본 메시지를 실시간 갱신"""
        target_msg = self.message or fallback_msg
        
        if len(self.participants) == 10:
            content = "✅ **10명 모집이 완료되었습니다! 팀 밸런싱을 진행합니다.**"
            if target_msg:
                await target_msg.edit(content=content, view=None)
                await self.send_balance_result(target_msg.channel)
        else:
            mentions = []
            for i, p in enumerate(self.participants):
                user_display = p["name"] if p.get("is_guest") else f"<@{p['id']}> (**{p['name']}**)"
                mentions.append(f"{i+1}. {user_display}")
            
            user_list = "\n".join(mentions) if mentions else "현재 참가자가 없습니다."
            content = f"📢 **오늘 내전 참가자 모집중! ({len(self.participants)}/10)**\n\n**[현재 참가자 명단]**\n{user_list}"
            
            if target_msg:
                await target_msg.edit(content=content, view=self)

    async def update_message(self, interaction: discord.Interaction):
        self.message = interaction.message
        if len(self.participants) == 10:
            await self.process_matchmaking(interaction)
        else:
            mentions = []
            for i, p in enumerate(self.participants):
                user_display = p["name"] if p.get("is_guest") else f"<@{p['id']}> (**{p['name']}**)"
                mentions.append(f"{i+1}. {user_display}")
            
            user_list = "\n".join(mentions) if mentions else "현재 참가자가 없습니다."
            content = f"📢 **오늘 내전 참가자 모집중! ({len(self.participants)}/10)**\n\n**[현재 참가자 명단]**\n{user_list}"
            
            if interaction.response.is_done():
                await interaction.message.edit(content=content, view=self)
            else:
                await interaction.response.edit_message(content=content, view=self)

    async def process_matchmaking(self, interaction: discord.Interaction):
        content = "✅ **10명 모집이 완료되었습니다! 팀 밸런싱을 진행합니다.**"
        if interaction.response.is_done():
            await interaction.message.edit(content=content, view=None)
        else:
            await interaction.response.edit_message(content=content, view=None)
        await self.send_balance_result(interaction.channel)

    async def send_balance_result(self, channel):
        selected_players = []
        for p in self.participants:
            selected_players.append({
                "id": p["id"],
                "riot_id": p["name"],
                "tier": p["tier"],
                "score": p["score"]
            })

        team_a, team_b, score_a, score_b, diff = calculate_best_teams(selected_players)

        embed = discord.Embed(
            title="⚔️ 발로란트 내전 5:5 최적 팀 밸런스",
            description=f"**두 팀 점수 차이:** {diff}점",
            color=discord.Color.gold(),
        )

        def format_team(team):
            text_list = []
            for p in team:
                is_guest = str(p['id']).startswith("guest_")
                display = f"**{p['riot_id']}**" if is_guest else f"<@{p['id']}> (**{p['riot_id']}**)"
                text_list.append(f"• {display} - {p['tier']}")
            return "\n".join(text_list)

        embed.add_field(
            name=f"🔵 A 팀 (총점: {score_a}점 / 평균: {score_a//5}점)",
            value=format_team(team_a),
            inline=False,
        )
        embed.add_field(
            name=f"🔴 B 팀 (총점: {score_b}점 / 평균: {score_b//5}점)",
            value=format_team(team_b),
            inline=False,
        )

        await channel.send(embed=embed)

    @discord.ui.button(label="참가 ⚔️", style=discord.ButtonStyle.success, custom_id="match_join_btn")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        members = load_members()

        if user_id not in members:
            await interaction.response.send_message("❌ 등록되지 않은 유저입니다. `/등록` 명령어로 먼저 등록해 주세요!", ephemeral=True)
            return
        if any(p["id"] == user_id for p in self.participants):
            await interaction.response.send_message("이미 참가 명단에 있습니다!", ephemeral=True)
            return
        if len(self.participants) >= 10:
            await interaction.response.send_message("이미 10명 모집이 완료되었습니다.", ephemeral=True)
            return

        info = members[user_id]
        self.participants.append({
            "id": user_id,
            "name": info["riot_id"],
            "tier": info["tier"],
            "score": info["score"],
            "is_guest": False
        })
        await self.update_message(interaction)

    @discord.ui.button(label="취소 ❌", style=discord.ButtonStyle.danger, custom_id="match_leave_btn")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        if not any(p["id"] == user_id for p in self.participants):
            await interaction.response.send_message("참가 신청을 하지 않은 상태입니다.", ephemeral=True)
            return

        self.participants = [p for p in self.participants if p["id"] != user_id]
        await self.update_message(interaction)

    @discord.ui.button(label="대리참가 ➕", style=discord.ButtonStyle.secondary, custom_id="proxy_join_btn")
    async def proxy_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message  # 메시지 객체 저장
        view = ProxyView(self, action_type="add")
        await interaction.response.send_message("대리로 참가시킬 유저를 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="대리취소 ➖", style=discord.ButtonStyle.secondary, custom_id="proxy_leave_btn")
    async def proxy_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message  # 메시지 객체 저장
        view = ProxyView(self, action_type="remove")
        await interaction.response.send_message("명단에서 제외할 유저를 선택하세요:", view=view, ephemeral=True)


@bot.event
async def on_ready():
    print(f"✅ {bot.user.name} 봇이 성공적으로 로그인했습니다!")


# --- [명령어 1: 유저 등록 (본인)] ---
@bot.tree.command(name="등록", description="본인의 이름과 티어를 등록/수정합니다.")
@app_commands.describe(이름="본인 이름 입력", tier="티어 선택")
@app_commands.choices(tier=[app_commands.Choice(name=t, value=t) for t in TIER_SCORES.keys()])
async def register(interaction: discord.Interaction, 이름: str, tier: app_commands.Choice[str]):
    user_id = str(interaction.user.id)
    selected_tier = tier.value
    score = add_or_update_member(user_id, 이름, selected_tier)

    embed = discord.Embed(title="✅ 등록 완료", color=discord.Color.green())
    embed.add_field(name="디스코드 유저", value=f"<@{user_id}>", inline=False)
    embed.add_field(name="이름", value=이름, inline=True)
    embed.add_field(name="티어", value=f"{selected_tier} ({score}점)", inline=True)
    await interaction.response.send_message(embed=embed)


# --- [명령어 2: 유저 대리 등록 (타인/용병)] ---
@bot.tree.command(name="대리등록", description="다른 유저나 디스코드에 없는 용병의 이름을 등록합니다.")
@app_commands.describe(이름="등록할 유저 이름 (예: 홍길동)", tier="티어 선택", 대상_유저="디스코드 유저 지목 (용병일 경우 비워둠)")
@app_commands.choices(tier=[app_commands.Choice(name=t, value=t) for t in TIER_SCORES.keys()])
async def register_proxy(interaction: discord.Interaction, 이름: str, tier: app_commands.Choice[str], 대상_유저: discord.User = None):
    target_id = str(대상_유저.id) if 대상_유저 else f"guest_{이름}"
    display_user = f"<@{target_id}>" if 대상_유저 else f"**용병 (디스코드 미가입)**"
    selected_tier = tier.value
    
    score = add_or_update_member(target_id, 이름, selected_tier)

    embed = discord.Embed(title="✅ 대리 등록 완료", color=discord.Color.green())
    embed.add_field(name="대상 유저", value=display_user, inline=False)
    embed.add_field(name="이름", value=이름, inline=True)
    embed.add_field(name="티어", value=f"{selected_tier} ({score}점)", inline=True)
    await interaction.response.send_message(embed=embed)


# --- [명령어 3: 등록 목록 조회] ---
@bot.tree.command(name="목록", description="등록된 모든 유저 목록을 조회합니다.")
async def member_list(interaction: discord.Interaction):
    members = load_members()
    if not members:
        await interaction.response.send_message("❌ 아직 등록된 유저가 없습니다.")
        return

    embed = discord.Embed(title="📋 내전 등록 유저 목록", color=discord.Color.blue())
    text = ""
    for user_id, info in members.items():
        user_display = f"**용병**" if str(user_id).startswith("guest_") else f"<@{user_id}>"
        text += f"{user_display} | **{info['riot_id']}** | {info['tier']} ({info['score']}점)\n"

    embed.description = text
    await interaction.response.send_message(embed=embed)


# --- [명령어 4: 유저 삭제] ---
@bot.tree.command(name="삭제", description="본인의 등록 정보를 삭제합니다.")
async def unregister(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    if delete_member(user_id):
        await interaction.response.send_message("✅ 등록 정보가 성공적으로 삭제되었습니다.")
    else:
        await interaction.response.send_message("❌ 등록된 정보가 없습니다.")


# --- [명령어 5: 내전 모집] ---
@bot.tree.command(name="내전", description="참가/취소 버튼으로 10명을 모집하여 자동으로 팀을 구성합니다.")
async def create_match(interaction: discord.Interaction):
    view = MatchRecruitView()
    await interaction.response.send_message(
        "📢 **오늘 내전 참가자 모집중! (0/10)**\n\n**[현재 참가자 명단]**\n현재 참가자가 없습니다.",
        view=view
    )
    view.message = await interaction.original_response()


if __name__ == "__main__":
    init_db()
    keep_alive()
    bot.run(TOKEN)