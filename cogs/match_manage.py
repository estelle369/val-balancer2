import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from database import load_members, load_all_guild_settings
from utils.balancer import calculate_best_teams


class MatchManageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # DB에서 저장된 채널 설정 불러오기
        target_ch, result_ch, _, _ = load_all_guild_settings()
        self.target_channels = target_ch  # { guild_id: channel_id }
        self.result_channels = result_ch  # { guild_id: result_channel_id }
        self.active_matches = {}   # 진행 중인 내전 모집 데이터
        self.last_balanced_match = {}

    def get_target_output_channel(self, guild_id, fallback_channel):
        if guild_id in self.result_channels:
            channel_obj = self.bot.get_channel(self.result_channels[guild_id])
            if channel_obj:
                return channel_obj
        return fallback_channel

    async def update_match_status(self, match_data):
        participants = match_data["participants"]
        fallback_channel = match_data["channel"]
        guild_id = match_data["guild_id"]
        output_channel = self.get_target_output_channel(guild_id, fallback_channel)
        
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
                    
                await self.run_balance_and_send(output_channel, participants)
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

    async def run_balance_and_send(self, channel, participants):
        selected_players = []
        for p in participants:
            selected_players.append({
                "id": p["id"],
                "riot_id": p["name"],
                "tier": p["tier"],
                "score": p["score"]
            })

        team_a, team_b, score_a, score_b, diff = calculate_best_teams(selected_players)

        guild_id = channel.guild.id if channel.guild else None
        if guild_id:
            self.last_balanced_match[guild_id] = {
                "team_a": team_a,
                "team_b": team_b
            }

        embed = discord.Embed(
            title="⚔️ 발로란트 내전 5:5 최적 팀 밸런스 결과",
            description=f"**두 팀 점수 차이:** {diff}점\n💡 *B팀만 아래 음성 채널로 이동하려면 `/팀이동`을 입력하세요.*",
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
            name=f"🔵 A 팀 [선수비 (Defender)] (총점: {score_a}점 / 평균: {score_a//5}점)",
            value=format_team(team_a),
            inline=False,
        )
        embed.add_field(
            name=f"🔴 B 팀 [선공격 (Attacker)] (총점: {score_b}점 / 평균: {score_b//5}점)",
            value=format_team(team_b),
            inline=False,
        )

        await channel.send(embed=embed)

    # --- [이벤트: 이모지 및 메시지 감지] ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        guild_id = message.guild.id if message.guild else None
        if guild_id in self.target_channels and message.channel.id == self.target_channels[guild_id]:
            try:
                await message.add_reaction("✅")
                await message.add_reaction("❌")

                self.active_matches[message.id] = {
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

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self.handle_reaction_change(payload, is_add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self.handle_reaction_change(payload, is_add=False)

    async def handle_reaction_change(self, payload: discord.RawReactionActionEvent, is_add: bool):
        if payload.user_id == self.bot.user.id:
            return
        if payload.message_id not in self.active_matches:
            return

        emoji = str(payload.emoji)
        if emoji not in ["✅", "❌"]:
            return

        match_data = self.active_matches[payload.message_id]
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            return

        user_id = str(member.id)
        members = load_members()
        output_channel = self.get_target_output_channel(payload.guild_id, match_data["channel"])

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

        await self.update_match_status(match_data)

    # --- [슬래시 커맨드: /교체] ---
    @app_commands.command(
        name="교체", 
        description="현재 진행 중인 내전의 유저/용병을 신규 유저로 교체하고 밸런스를 재계산합니다."
    )
    @app_commands.describe(
        기존유저="명단에서 제외할 유저 (@유저 선택 또는 용병 이름/ID)",
        신규유저="대신 들어올 유저 (@유저 선택)",
        용병이름="디스코드에 없는 용병일 경우 이름 직접 입력 (신규유저 선택 시 생략)"
    )
    async def replace_participant(
        self, 
        interaction: discord.Interaction, 
        기존유저: str, 
        신규유저: discord.User = None, 
        용병이름: str = None
    ):
        guild_id = interaction.guild_id
        
        # 1. 신규 유저 정보 검증
        if not 신규유저 and not 용병이름:
            await interaction.response.send_message(
                "❌ `신규유저`(@유저 지목) 또는 `용병이름`(텍스트 입력) 중 하나는 반드시 입력해야 합니다.", 
                ephemeral=True
            )
            return

        members = load_members()
        new_player_data = None

        if 신규유저:
            new_id = str(신규유저.id)
            if new_id not in members:
                await interaction.response.send_message(
                    f"❌ <@{new_id}> 님은 DB에 등록되어 있지 않습니다. 먼저 `/등록`을 진행해 주세요.", 
                    ephemeral=True
                )
                return
            info = members[new_id]
            new_player_data = {
                "id": new_id,
                "name": info["riot_id"],
                "tier": info["tier"],
                "score": info["score"],
                "is_guest": False
            }
        else:
            guest_id = f"guest_{용병이름}"
            if guest_id not in members:
                await interaction.response.send_message(
                    f"❌ 용병 '{용병이름}' 정보가 DB에 없습니다. 먼저 `/대리등록`으로 등록해 주세요.", 
                    ephemeral=True
                )
                return
            info = members[guest_id]
            new_player_data = {
                "id": guest_id,
                "name": info["riot_id"],
                "tier": info["tier"],
                "score": info["score"],
                "is_guest": True
            }

        # 2. 해당 서버의 활성화된 내전 세션 찾기 (가장 최근 세션 탐색)
        target_match = None
        for message_id, match in reversed(list(self.active_matches.items())):
            if match.get("guild_id") == guild_id:
                target_match = match
                break

        if not target_match:
            await interaction.response.send_message("❌ 현재 진행 중인 내전 모집 세션이 없습니다.", ephemeral=True)
            return

        # 3. 기존 유저 대상 탐색 및 교체
        target_id_clean = 기존유저.replace("<@", "").replace(">", "").replace("!", "").strip()
        
        participants = target_match["participants"]
        found_idx = -1

        for idx, p in enumerate(participants):
            if str(p["id"]) == target_id_clean or p["name"] == 기존유저:
                found_idx = idx
                break

        if found_idx == -1:
            await interaction.response.send_message(
                f"❌ 현재 참가 명단에서 '{기존유저}' 유저를 찾을 수 없습니다.", 
                ephemeral=True
            )
            return

        old_player = participants[found_idx]
        participants[found_idx] = new_player_data

        await interaction.response.defer()

        # 4. 내전 모집 명단 임베드 업데이트
        await self.update_match_status(target_match)

        # 5. 10명 명단이 완성된 상태였다면 밸런스 결과 재출력 (self.run_balance_and_send 사용)
        if len(participants) == 10:
            output_channel = interaction.channel
            await self.run_balance_and_send(output_channel, participants)

        old_disp = old_player['name'] if old_player.get('is_guest') else f"<@{old_player['id']}>"
        new_disp = new_player_data['name'] if new_player_data.get('is_guest') else f"<@{new_player_data['id']}>"

        embed = discord.Embed(
            title="🔄 내전 멤버 교체 완료",
            description=f"**퇴장:** {old_disp}\n**입장:** {new_disp}\n\n✅ 명단 및 팀 밸런스 결과가 재계산되었습니다.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(MatchManageCog(bot))