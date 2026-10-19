#디스코드 슬래시 명령어와 on_raw_reaction_add/remove 이벤트를 처리하며, 디바운싱(Debounce)을 통해 레이트 리밋을 방어합니다.
import asyncio
import discord
from discord.ext import commands
from utils.lobby_manager import LobbyManager

class MatchCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lobby_manager = LobbyManager(max_slots=10)
        self.debounce_tasks = {}

    @discord.app.commands.command(name="내전모집", description="이모지 반응 기반 내전 참가 모집을 시작합니다.")
    async def start_match(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        
        # 1. 이전 모집글 충돌 방지: 기존 방이 있다면 마감 처리
        old_lobby = self.lobby_manager.get_lobby(guild_id)
        if old_lobby and not old_lobby["is_closed"]:
            await self._close_old_lobby_message(guild_id, old_lobby)

        await interaction.response.defer(thinking=True)

        # 2. 새로운 모집 메시지 생성 및 이모지 추가
        embed = self._build_embed(0, self.lobby_manager.max_slots, [], interaction.user.display_name)
        msg = await interaction.followup.send(embed=embed)
        
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")

        # 3. 상태 관리 등록
        self.lobby_manager.create_lobby(
            guild_id=guild_id,
            message_id=msg.id,
            channel_id=msg.channel.id,
            host_name=interaction.user.display_name
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction_event(payload, is_add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction_event(payload, is_add=False)

    async def _handle_reaction_event(self, payload: discord.RawReactionActionEvent, is_add: bool):
        if payload.user_id == self.bot.user.id:
            return

        guild_id = payload.guild_id
        lobby = self.lobby_manager.get_lobby(guild_id)
        if not lobby or lobby["is_closed"] or payload.message_id != lobby["message_id"]:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            msg = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(payload.user_id)
        if not member:
            return

        emoji = str(payload.emoji)
        lock = self.lobby_manager.get_lock(guild_id)

        async with lock: # 동시성 제어
            # 잘못된 이모지 필터링
            if emoji not in ["✅", "❌"]:
                await self._safe_remove_reaction(msg, payload.emoji, member)
                return

            updated = False
            if emoji == "✅" and is_add:
                updated = self.lobby_manager.add_participant(guild_id, member)
                if not updated:  # 정원이 찼거나 중복인 경우 이모지 제거
                    await self._safe_remove_reaction(msg, payload.emoji, member)
            elif emoji == "❌" or (emoji == "✅" and not is_add):
                updated = self.lobby_manager.remove_participant(guild_id, member)

            # 유저가 누른 이모지는 깔끔하게 즉시 제거
            await self._safe_remove_reaction(msg, payload.emoji, member)

            if updated:
                self._schedule_debounce_update(guild_id, msg, lobby)

    def _schedule_debounce_update(self, guild_id: int, msg: discord.Message, lobby: dict):
        """연타로 인한 레이트 리밋 방지를 위한 디바운스 처리"""
        if guild_id in self.debounce_tasks and not self.debounce_tasks[guild_id].done():
            self.debounce_tasks[guild_id].cancel()

        self.debounce_tasks[guild_id] = asyncio.create_task(
            self._delayed_embed_update(msg, lobby)
        )

    async def _delayed_embed_update(self, msg: discord.Message, lobby: dict):
        await asyncio.sleep(1.2)  # 1.2초 동안 추가 입력 대기
        
        participants = lobby["participants"]
        max_slots = lobby["max_slots"]
        embed = self._build_embed(len(participants), max_slots, participants, lobby["host_name"])

        try:
            await msg.edit(embed=embed)
        except discord.HTTPException as e:
            print(f"임베드 수정 에러: {e}")

    def _build_embed(self, current_count: int, max_slots: int, participants: list, host_name: str) -> discord.Embed:
        is_full = current_count >= max_slots
        list_text = "\n".join([f"{i+1}. {m.mention}" for i, m in enumerate(participants)]) if participants else "아직 참가한 사람이 없습니다."

        embed = discord.Embed(
            title=f"🎮 디스코드 내전 참가 모집 {'[모집 마감!]' if is_full else ''}",
            description="아래 **✅(참가)** 이모지를 눌러 참가하세요! (취소: **❌**)",
            color=discord.Color.orange() if is_full else discord.Color.blue()
        )
        embed.add_field(name=f"참가자 명단 ({current_count} / {max_slots})", value=list_text, inline=False)
        embed.set_footer(text=f"개설자: {host_name}")
        return embed

    async def _close_old_lobby_message(self, guild_id: int, old_lobby: dict):
        self.lobby_manager.close_lobby(guild_id)
        try:
            channel = self.bot.get_channel(old_lobby["channel_id"])
            if channel:
                msg = await channel.fetch_message(old_lobby["message_id"])
                embed = msg.embeds[0]
                embed.color = discord.Color.red()
                embed.title = "🎮 [내전 마감됨 / 새로운 모집으로 대체]"
                await msg.edit(embed=embed)
                await msg.clear_reactions()
        except Exception:
            pass

    async def _safe_remove_reaction(self, msg: discord.Message, emoji, member):
        try:
            await msg.remove_reaction(emoji, member)
        except Exception:
            pass

async def setup(bot):
    await bot.add_cog(MatchCog(bot))