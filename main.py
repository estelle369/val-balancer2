import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from config import TIER_SCORES
from database import add_or_update_member, load_members, delete_member, init_db, get_db_connection
from utils.balancer import calculate_best_teams
from keep_alive import keep_alive

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("❌ 에러: .env 파일에서 DISCORD_TOKEN을 불러오지 못했습니다!")
    exit()

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.members = True


# --- [5분마다 Aiven DB 자동 꺼짐 방지 핑 함수] ---
@tasks.loop(minutes=5)
async def keep_db_alive():
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1;")
        conn.close()
    except Exception as e:
        print(f"⚠️ DB Ping 실패: {e}")


class ValBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ 슬래시 커맨드가 동기화되었습니다!")


bot = ValBot()


# --- [대리 참가/취소용 드롭다운 메뉴] ---
class ProxySelectMenu(discord.ui.Select):
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

        await self.parent_view.refresh_message_ui()


class ProxyView(discord.ui.View):
    def __init__(self, parent_view, action_type="add"):
        super().__init__(timeout=60)
        self.add_item(ProxySelectMenu(parent_view, action_type))


# --- [특정 채팅/메시지 기반 이모지 내전 모집 관리 클래스] ---
class TargetMessageMatchManager(discord.ui.View):
    def __init__(self, target_message: discord.Message):
        super().__init__(timeout=None)
        self.target_message = target_message
        self.participants = []  # dict 리스트
        self.lock = asyncio.Lock()
        self.debounce_task = None

    async def refresh_message_ui(self):
        """선택된 타겟 메시지의 본문을 수정하여 현재 참가자 명단을 실시간 반영합니다."""
        if not self.target_message:
            return

        is_full = len(self.participants) >= 10
        mentions = []
        for i, p in enumerate(self.participants):
            user_display = p["name"] if p.get("is_guest") else f"<@{p['id']}> (**{p['name']}**)"
            mentions.append(f"{i+1}. {user_display} ({p['tier']}, {p['score']}점)")
        
        user_list = "\n".join(mentions) if mentions else "현재 참가자가 없습니다."
        
        # 원래 메시지의 내용에 명단 정보를 보기 좋게 합쳐서 업데이트
        updated_content = (
            f"🎯 **[지정된 내전 모집 채팅]**\n"
            f"상태: {'모집 완료! (밸런스 진행중)' if is_full else f'모집 중 ({len(self.participants)}/10)'}\n\n"
            f"**[현재 참가자 명단]**\n{user_list}\n\n"
            f"*(이 메시지에 ✅(참가) / ❌(취소) 이모지를 눌러 참가하세요!)*"
        )

        try:
            if is_full:
                await self.target_message.edit(content=updated_content, view=None)
                await self.send_balance_result(self.target_message.channel)
            else:
                await self.target_message.edit(content=updated_content, view=self)
        except discord.HTTPException as e:
            print(f"메시지 수정 에러: {e}")

    def schedule_debounced_refresh(self):
        if self.debounce_task and not self.debounce_task.done():
            self.debounce_task.cancel()
        self.debounce_task = asyncio.create_task(self._delayed_refresh())

    async def _delayed_refresh(self):
        await asyncio.sleep(1.0)
        await self.refresh_message_ui()

    async def handle_reaction_change(self, payload: discord.RawReactionActionEvent, is_add: bool):
        if payload.message_id != self.target_message.id:
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

            await self.safe_remove_reaction(payload.emoji, member)

        if len(self.participants) >= 10:
            if self.debounce_task and not self.debounce_task.done():
                self.debounce_task.cancel()
            await self.refresh_message_ui()
        else:
            self.schedule_debounced_refresh()

    async def safe_remove_reaction(self, emoji, member):
        try:
            await self.target_message.remove_reaction(emoji, member)
        except Exception:
            pass

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

    @discord.ui.button(label="대리참가 ➕", style=discord.ButtonStyle.secondary, custom_id="proxy_join_btn_target")
    async def proxy_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ProxyView(self, action_type="add")
        await interaction.response.send_message("대리로 참가시킬 유저를 선택하세요:", view=view, ephemeral=True)

    @discord.ui.button(label="대리취소 ➖", style=discord.ButtonStyle.secondary, custom_id="proxy_leave_btn_target")
    async def proxy_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ProxyView(self, action_type="remove")
        await interaction.response.send_message("명단에서 제외할 유저를 선택하세요:", view=view, ephemeral=True)


# 전역 활성 타겟 모집 매니저 관리
active_target_manager = None

@bot.event
async def on_ready():
    print(f"✅ {bot.user.name} 봇이 성공적으로 로그인했습니다!")
    if not keep_db_alive.is_running():
        keep_db_alive.start()


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    global active_target_manager
    if active_target_manager:
        await active_target_manager.handle_reaction_change(payload, is_add=True)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    global active_target_manager
    if active_target_manager:
        await active_target_manager.handle_reaction_change(payload, is_add=False)


# --- [기존 명령어 1~4: 등록, 대리등록, 목록, 삭제] ---
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


@bot.tree.command(name="삭제", description="본인의 등록 정보를 삭제합니다.")
async def unregister(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    if delete_member(user_id):
        await interaction.response.send_message("✅ 등록 정보가 성공적으로 삭제되었습니다.")
    else:
        await interaction.response.send_message("❌ 등록된 정보가 없습니다.")


# --- [신규 기능: 특정 채팅 메시지를 지정하여 내전 모집 시작] ---
@bot.tree.command(name="내전참가모집", description="특정 메시지 ID(또는 링크)를 지정하여 해당 채팅을 내전 참가 모집판으로 만듭니다.")
@app_commands.describe(메시지id="모집판으로 지정할 디스코드 메시지 ID 또는 링크")
async def start_target_match(interaction: discord.Interaction, 메시지id: str):
    global active_target_manager

    # 메시지 ID 추출 (링크로 입력된 경우 대비)
    msg_id_str = 메시지id.strip()
    if "/" in msg_id_str:
        msg_id_str = msg_id_str.split("/")[-1]

    try:
        target_msg = await interaction.channel.fetch_message(int(msg_id_str))
    except Exception:
        await interaction.response.send_message("❌ 해당 메시지를 찾을 수 없습니다. 올바른 메시지 ID나 링크를 입력해주세요.", ephemeral=True)
        return

    # 매니저 생성 및 등록
    manager = TargetMessageMatchManager(target_msg)
    active_target_manager = manager

    # 타겟 메시지에 기본 이모지 추가
    await target_msg.add_reaction("✅")
    await target_msg.add_reaction("❌")

    # 초기 상태 반영
    await manager.refresh_message_ui()
    await interaction.response.send_message(f"✅ 성공적으로 해당 채팅을 내전 모집판으로 지정했습니다!", ephemeral=True)


if __name__ == "__main__":
    init_db()
    keep_alive()
    bot.run(TOKEN)