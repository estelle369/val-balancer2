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
        # 맵 추첨 Cog 파일 동적 로드
        try:
            await self.load_extension("cogs.valorant_map")
            print("✅ ValorantMapCog 로드 성공")
        except Exception as e:
            print(f"❌ Cog 로드 실패: {e}")

        await self.tree.sync()
        print("✅ 슬래시 커맨드가 동기화되었습니다!")


bot = ValBot()

# --- [전역 상태 관리] ---
target_channels = {}  # { guild_id: channel_id } (모집 채널)
result_channels = {}  # { guild_id: result_channel_id } (결과 출력 채널)

# 현재 진행 중인 내전 모집 데이터
# { message_id: { "channel": channel, "guild_id": guild_id, "message": message, "participants": [], "status_msg": None, "lock": Lock(), "debounce_task": Task } }
active_matches = {}


# --- [대리 참가/취소용 드롭다운 메뉴] ---
class ProxySelectMenu(discord.ui.Select):
    def __init__(self, match_data, action_type="add"):
        self.match_data = match_data
        self.action_type = action_type
        members = load_members()
        options = []

        if action_type == "add":
            current_ids = [p["id"] for p in self.match_data["participants"]]
            for uid, info in members.items():
                if uid not in current_ids:
                    options.append(discord.SelectOption(
                        label=info["riot_id"],
                        value=uid,
                        description=f"티어: {info['tier']} ({info['score']}점)"
                    ))
            placeholder = "명단에 추가할 유저를 선택하세요..."
        else:
            for p in self.match_data["participants"]:
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

        async with self.match_data["lock"]:
            if self.action_type == "add":
                if len(self.match_data["participants"]) >= 10:
                    await interaction.followup.send("이미 10명 모집이 완료되었습니다.", ephemeral=True)
                    return
                
                info = members[selected_id]
                if not any(p["id"] == selected_id for p in self.match_data["participants"]):
                    self.match_data["participants"].append({
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
                self.match_data["participants"] = [p for p in self.match_data["participants"] if p["id"] != selected_id]
                await interaction.followup.send("✅ 명단에서 정상적으로 제외되었습니다.", ephemeral=True)

        await update_match_status(self.match_data)


class ProxyView(discord.ui.View):
    def __init__(self, match_data, action_type="add"):
        super().__init__(timeout=60)
        self.add_item(ProxySelectMenu(match_data, action_type))


def get_target_output_channel(guild_id, fallback_channel):
    if guild_id in result_channels:
        channel_obj = bot.get_channel(result_channels[guild_id])
        if channel_obj:
            return channel_obj
    return fallback_channel


# --- [내전 모집 상태 실시간 임베드 업데이트 함수] ---
async def update_match_status(match_data):
    participants = match_data["participants"]
    fallback_channel = match_data["channel"]
    guild_id = match_data["guild_id"]
    output_channel = get_target_output_channel(guild_id, fallback_channel)
    
    is_full = len(participants) >= 10

    mentions = []
    for i, p in enumerate(participants):
        user_display = p["name"] if p.get("is_guest") else f"<@{p['id']}> (**{p['name']}**)"
        mentions.append(f"{i+1}. {user_display} ({p['tier']}, {p['score']}점)")

    user_list = "\n".join(mentions) if mentions else "현재 참가자가 없습니다."

    embed = discord.Embed(
        title=f"🎮 내전 참가 모집 {'[모집 완료!]' if is_full else f'({len(participants)}/10)'}",
        description=f"**[현재 참가자 명단]**\n{user_list}",
        color=discord.Color.gold() if is_full else discord.Color.blue()
    )

    try:
        status_msg = match_data.get("status_msg")
        
        if is_full:
            embed.set_footer(text="10명이 모두 모였습니다! 최적 팀 밸런싱을 시작합니다.")
            if status_msg:
                try:
                    await status_msg.edit(embed=embed)
                except Exception:
                    await output_channel.send(embed=embed)
            else:
                await output_channel.send(embed=embed)
                
            await run_balance_and_send(output_channel, participants)
        else:
            if match_data.get("debounce_task") and not match_data["debounce_task"].done():
                match_data["debounce_task"].cancel()

            async def delayed_update():
                await asyncio.sleep(0.8)
                nonlocal status_msg
                if status_msg:
                    try:
                        await status_msg.edit(embed=embed)
                    except Exception:
                        match_data["status_msg"] = await output_channel.send(embed=embed)
                else:
                    match_data["status_msg"] = await output_channel.send(embed=embed)

            match_data["debounce_task"] = asyncio.create_task(delayed_update())
    except Exception as e:
        print(f"상태 업데이트 중 에러: {e}")


async def run_balance_and_send(channel, participants):
    selected_players = []
    for p in participants:
        selected_players.append({
            "id": p["id"],
            "riot_id": p["name"],
            "tier": p["tier"],
            "score": p["score"]
        })

    team_a, team_b, score_a, score_b, diff = calculate_best_teams(selected_players)

    embed = discord.Embed(
        title="⚔️ 발로란트 내전 5:5 최적 팀 밸런스 결과",
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


# --- [이벤트: 지정 채널 채팅 감지 및 자동 이모지 부여] ---
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    guild_id = message.guild.id if message.guild else None
    if guild_id in target_channels and message.channel.id == target_channels[guild_id]:
        try:
            await message.add_reaction("✅")
            await message.add_reaction("❌")

            active_matches[message.id] = {
                "channel": message.channel,
                "guild_id": guild_id,
                "message": message,
                "participants": [],
                "status_msg": None,
                "lock": asyncio.Lock(),
                "debounce_task": None
            }
        except Exception as e:
            print(f"이모지 반응 추가 에러: {e}")

    await bot.process_commands(message)


# --- [이벤트: 이모지 반응 처리] ---
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    await handle_reaction_change(payload, is_add=True)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    await handle_reaction_change(payload, is_add=False)

async def handle_reaction_change(payload: discord.RawReactionActionEvent, is_add: bool):
    if payload.user_id == bot.user.id:
        return
    if payload.message_id not in active_matches:
        return

    emoji = str(payload.emoji)
    if emoji not in ["✅", "❌"]:
        return

    match_data = active_matches[payload.message_id]
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    member = guild.get_member(payload.user_id)
    if not member:
        return

    user_id = str(member.id)
    members = load_members()

    output_channel = get_target_output_channel(payload.guild_id, match_data["channel"])

    async with match_data["lock"]:
        if emoji == "✅" and is_add:
            if user_id not in members:
                try:
                    await output_channel.send(
                        f"⚠️ <@{user_id}> 님은 아직 등록되지 않았습니다! `/등록` 명령어로 본인 이름과 티어를 먼저 등록해 주세요.",
                        delete_after=5
                    )
                except Exception:
                    pass
                return

            if len(match_data["participants"]) >= 10:
                return

            if not any(p["id"] == user_id for p in match_data["participants"]):
                info = members[user_id]
                match_data["participants"].append({
                    "id": user_id,
                    "name": info["riot_id"],
                    "tier": info["tier"],
                    "score": info["score"],
                    "is_guest": False
                })
        elif emoji == "❌" or (emoji == "✅" and not is_add):
            match_data["participants"] = [p for p in match_data["participants"] if p["id"] != user_id]

    await update_match_status(match_data)


# --- [슬래시 커맨드 설정] ---
@bot.tree.command(name="모집채널설정", description="현재 채널을 내전 모집 전용 채널로 지정합니다.")
async def set_match_channel(interaction: discord.Interaction):
    target_channels[interaction.guild_id] = interaction.channel_id
    await interaction.response.send_message(f"✅ <#{interaction.channel_id}> 채널이 **내전 모집 채널**로 설정되었습니다!")


@bot.tree.command(name="결과채널설정", description="내전 참가 명단 및 밸런스 결과가 출력될 채널을 지정합니다.")
@app_commands.describe(채널="결과 메시지를 보낼 디스코드 채널 선택")
async def set_result_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    result_channels[interaction.guild_id] = 채널.id
    await interaction.response.send_message(f"📢 내전 명단 및 밸런스 결과가 앞으로 {채널.mention} 채널로 출력됩니다!")


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
    display_user = f"<@{target_id}>" if 대상_유zer else f"**용병 (디스코드 미가입)**"
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


@bot.event
async def on_ready():
    print(f"✅ {bot.user.name} 봇이 성공적으로 로그인했습니다!")
    if not keep_db_alive.is_running():
        keep_db_alive.start()


if __name__ == "__main__":
    init_db()
    keep_alive()
    bot.run(TOKEN)