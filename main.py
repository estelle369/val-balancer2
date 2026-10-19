import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Select
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
intents.reactions = True  # 이모지 반응 감지를 위해 필수
intents.guilds = True
intents.members = True


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

        async with self.parent_view.lock:
            if self.action_type == "add":
                if len(self.parent_view.participants) >= 10:
                    await interaction.followup.send("이미 10명 모집이 완료되었습니다.", ephemeral=True)
                    return
                
                info = members[selected_id]
                # 중복 체크
                if not any(p["id"] == selected_id for p in self.parent_view.participants):
                    self.parent_view.participants.append({
                        "id": selected_id,
                        "name": info["riot_id"],
                        "tier": info["tier"],
                        "score": info["score"],
                        "is_guest": str(selected_id).startswith("guest_")
                    })
                    await interaction.followup.send(f"✅ **{info['riot_id']}** 님이 대리로 참가되었습니다.", ephemeral=True)
                else:
                    await interaction.followup.send("이미 참가 명단에 있는 유저입니다.", ephemeral=True)
                    return
            else:
                self.parent_view.participants = [p for p in self.parent_view.participants if p["id"] != selected_id]
                await interaction.followup.send("✅ 명단에서 정상적으로 제외되었습니다.", ephemeral=True)

        # 상태 반영 및 UI 갱신
        await self.parent_view.refresh_message_ui()


class ProxyView(View):
    def __init__(self, parent_view, action_type="add"):
        super().__init__(timeout=60)
        self.add_item(ProxySelectMenu(parent_view, action_type))


# --- [이모지 반응 및 대리 기능을 통합한 내전 모집 View 클래스] ---
class MatchRecruitView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.participants = []  # dict 리스트 (id, name, tier, score, is_guest)
        self.message = None
        self.lock = asyncio.Lock()  # 동시성(Race Condition) 제어용 락
        self.debounce_task = None   # 레이트 리밋 방지용 디바운스 태스크

    def build_content_and_embed(self):
        """현재 명단 상태를 바탕으로 메시지 텍스트와 임베드를 빌드합니다."""
        is_full = len(self.participants) >= 10
        
        if is_full:
            content = "✅ **10명 모집이 완료되었습니다! 팀 밸런싱을 진행합니다.**"
            embed = discord.Embed(
                title="⚔️ 발로란트 내전 5:5 최적 팀 밸런스",
                color=discord.Color.gold(),
            )
        else:
            mentions = []
            for i, p in enumerate(self.participants):
                user_display = p["name"] if p.get("is_guest") else f"<@{p['id']}> (**{p['name']}**)"
                mentions.append(f"{i+1}. {user_display} ({p['tier']}, {p['score']}점)")
            
            user_list = "\n".join(mentions) if mentions else "현재 참가자가 없습니다."
            content = f"📢 **오늘 내전 참가자 모집중! ({len(self.participants)}/10)**\n\n**[현재 참가자 명단]**\n{user_list}"
            
            embed = discord.Embed(
                title="🎮 디스코드 내전 참가 모집",
                description="아래 **✅(참가)** 이모지를 누르거나 하단 버튼을 이용해 참가하세요!\n(취소는 **❌**)",
                color=discord.Color.blue()
            )
            embed.add_field(name=f"참가자 명단 ({len(self.participants)} / 10)", value=user_list, inline=False)
            
        return content, embed

    async def refresh_message_ui(self):
        """디바운스를 적용하여 빈번한 메시지 수정을 방지하고 안전하게 UI를 갱신합니다."""
        if not self.message:
            return

        content, embed = self.build_content_and_embed()
        is_full = len(self.participants) >= 10

        try:
            if is_full:
                await self.message.edit(content=content, view=None)
                await self.send_balance_result(self.message.channel)
            else:
                await self.message.edit(content=content, view=self)
        except discord.HTTPException as e:
            print(f"메시지 갱신 중 에러 발생: {e}")

    def schedule_debounced_refresh(self):
        """짧은 시간 내 다중 반응이 올 경우 1초간 대기 후 마지막 상태만 반영 (디바운스)"""
        if self.debounce_task and not self.debounce_task.done():
            self.debounce_task.cancel()
        
        self.debounce_task = asyncio.create_task(self._delayed_refresh())

    async def _delayed_refresh(self):
        await asyncio.sleep(1.0)
        await self.refresh_message_ui()

    async def handle_reaction_change(self, payload: discord.RawReactionActionEvent, is_add: bool):
        """이모지 추가/제거 이벤트 처리 로직"""
        if not self.message or payload.message_id != self.message.id:
            return
        if payload.user_id == bot.user.id:
            return

        emoji = str(payload.emoji)
        if emoji not in ["✅", "❌"]:
            return

        guild = bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            return

        user_id = str(member.id)
        members = load_members()

        async with self.lock:
            if emoji == "✅" and is_add:
                if user_id not in members:
                    await self.safe_remove_reaction(payload.emoji, member)
                    return
                if len(self.participants) >= 10:
                    await self.safe_remove_reaction(payload.emoji, member)
                    return
                if not any(p["id"] == user_id for p in self.participants):
                    info = members[user_id]
                    self.participants.append({
                        "id": user_id,
                        "name": info["riot_id"],
                        "tier": info["tier"],
                        "score": info["score"],
                        "is_guest": False
                    })
            elif emoji == "❌" or (emoji == "✅" and not is_add):
                self.participants = [p for p in self.participants if p["id"] != user_id]

            # 유저가 누른 이모지는 깔끔하게 즉시 제거하여 다음 반응을 원활하게 유도
            await self.safe_remove_reaction(payload.emoji, member)

        # 10명이 되면 즉시 마감 및 밸런싱 실행, 아니면 디바운스 갱신
        if len(self.participants) >= 10:
            if self.debounce_task and not self.debounce_task.done():
                self.debounce_task.cancel()
            await self.refresh_message_ui()
        else:
            self.schedule_debounced_refresh()

    async def safe_remove_reaction(self, emoji, member):
        try:
            await self.message.remove_reaction(emoji, member)
        except Exception:
            pass

    async def send_balance_result(self, channel):
        """기존 밸런스 알고리즘을 호출하여 팀 구성 결과를 전송합니다."""
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
                text_list.append(f"• {display} ({p['tier']})")
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

    # --- [하단 편의 버튼 (대리 참가/취소용)] ---
    @discord.ui.button(label="대리참가 ➕", style=discord.ButtonStyle.secondary, custom_id="proxy_join_btn")
    async def proxy_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ProxyView(self, action_type="add")
        await interaction.response.send_message("대리로 참가시킬 유저를 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="대리취소 ➖", style=discord.ButtonStyle.secondary, custom_id="proxy_leave_btn")
    async def proxy_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ProxyView(self, action_type="remove")
        await interaction.response.send_message("명단에서 제외할 유저를 선택하세요:", view=view, ephemeral=True)


# 전역 활성 내전 뷰 관리 (이모지 반응 연동을 위해 참조)
active_recruit_view = None

@bot.event
async def on_ready():
    print(f"✅ {bot.user.name} 봇이 성공적으로 로그인했습니다!")


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    global active_recruit_view
    if active_recruit_view:
        await active_recruit_view.handle_reaction_change(payload, is_add=True)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    global active_recruit_view
    if active_recruit_view:
        await active_recruit_view.handle_reaction_change(payload, is_add=False)


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


# --- [명령어 5: 내전 모집 (이모지 반응형)] ---
@bot.tree.command(name="내전", description="✅(참가) 및 ❌(취소) 이모지 반응으로 10명을 모집하여 자동으로 팀을 구성합니다.")
async def create_match(interaction: discord.Interaction):
    global active_recruit_view
    
    # 이전 활성 모집글이 있다면 정리
    if active_recruit_view and active_recruit_view.message:
        try:
            await active_recruit_view.message.clear_reactions()
        except Exception:
            pass

    view = MatchRecruitView()
    active_recruit_view = view

    content, embed = view.build_content_and_embed()
    await interaction.response.send_message(content=content, embed=embed, view=view)
    
    msg = await interaction.original_response()
    view.message = msg

    # 기본 참가/취소용 이모지 자동 추가
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")


if __name__ == "__main__":
    init_db()
    keep_alive()
    bot.run(TOKEN)