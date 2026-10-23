import discord
from discord import app_commands
from discord.ext import commands

# 전역 설정 저장 (서버별 A팀/B팀 음성 채널 ID)
a_team_voice_channels = {}  # { guild_id: a_team_voice_channel_id }
b_team_voice_channels = {}  # { guild_id: b_team_voice_channel_id }


class VoiceMoverCog(commands.Cog):
    # 1. 관리자 전용 그룹 생성 (클래스 변수로 선언)
    setting_voice_group = app_commands.Group(
        name="설정음성",
        description="⚙️ [관리자 전용] 음성 채널 설정 커맨드 모음",
        default_permissions=discord.Permissions(manage_channels=True)
    )

    def __init__(self, bot):
        self.bot = bot

    @setting_voice_group.command(name="a팀채널", description="⚙️ 대기실 및 A팀이 사용할 음성 채널을 지정합니다.")
    @app_commands.describe(채널="A팀(대기실) 음성 채널 선택")
    async def set_a_team_channel(self, interaction: discord.Interaction, 채널: discord.VoiceChannel):
        a_team_voice_channels[interaction.guild_id] = 채널.id
        await interaction.response.send_message(f"📢 A팀(대기실) 음성 채널이 {채널.mention} (으)로 설정되었습니다!")

    @setting_voice_group.command(name="b팀채널", description="⚙️ B팀 유저들이 이동할 음성 채널을 지정합니다.")
    @app_commands.describe(채널="B팀이 이동할 음성 채널 선택")
    async def set_b_team_channel(self, interaction: discord.Interaction, 채널: discord.VoiceChannel):
        b_team_voice_channels[interaction.guild_id] = 채널.id
        await interaction.response.send_message(f"📢 B팀 이동 음성 채널이 {채널.mention} (으)로 설정되었습니다!")

    # 2. 유저용 독립 커맨드
    @app_commands.command(name="팀이동", description="🚚 최근 밸런스 결과의 B팀 멤버 5명만 지정된 B팀 음성 채널로 이동시킵니다.")
    async def move_teams(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        
        if guild_id not in b_team_voice_channels:
            await interaction.response.send_message("❌ 먼저 `/설정음성 b팀채널` 명령어로 B팀 음성 채널을 설정해 주세요!", ephemeral=True)
            return

        target_channel_id = b_team_voice_channels[guild_id]
        target_channel = interaction.guild.get_channel(target_channel_id)

        if not target_channel or not isinstance(target_channel, discord.VoiceChannel):
            await interaction.response.send_message("❌ 설정된 B팀 음성 채널을 찾을 수 없습니다.", ephemeral=True)
            return

        from main import last_balanced_match
        
        if guild_id not in last_balanced_match or not last_balanced_match[guild_id].get("team_b"):
            await interaction.response.send_message("❌ 이동시킬 B팀 명단 정보가 없습니다. 먼저 10명 모집을 완료해 주세요.", ephemeral=True)
            return

        team_b = last_balanced_match[guild_id]["team_b"]
        await interaction.response.defer()

        moved_count = 0
        not_in_voice = []

        for player in team_b:
            player_id = player["id"]
            if str(player_id).startswith("guest_"):
                continue

            member = interaction.guild.get_member(int(player_id))
            if member and member.voice and member.voice.channel:
                try:
                    await member.move_to(target_channel)
                    moved_count += 1
                except Exception as e:
                    print(f"{member.display_name} 이동 실패: {e}")
            else:
                riot_name = player.get("riot_id", "알 수 없음")
                not_in_voice.append(riot_name)

        msg = f"🚚 **B팀 유저 {moved_count}명**을 {target_channel.mention} 채널로 이동시켰습니다!"
        if not_in_voice:
            msg += f"\n⚠️ 음성 채널에 접속해 있지 않아 이동하지 못한 유저: {', '.join(not_in_voice)}"

        await interaction.followup.send(msg)

    @app_commands.command(name="대기실복귀", description="🏠 B팀 음성 채널 인원들을 지정된 A팀(대기실) 음성 채널로 복귀시킵니다.")
    async def return_to_lobby(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id

        if guild_id not in a_team_voice_channels:
            await interaction.response.send_message("❌ 먼저 `/설정음성 a팀채널` 명령어로 대기실(A팀) 음성 채널을 지정해 주세요!", ephemeral=True)
            return
            
        if guild_id not in b_team_voice_channels:
            await interaction.response.send_message("❌ 먼저 `/설정음성 b팀채널` 명령어로 B팀 음성 채널을 설정해 주세요!", ephemeral=True)
            return

        destination_channel = interaction.guild.get_channel(a_team_voice_channels[guild_id])
        b_channel = interaction.guild.get_channel(b_team_voice_channels[guild_id])

        if not destination_channel or not isinstance(destination_channel, discord.VoiceChannel):
            await interaction.response.send_message("❌ 설정된 A팀(대기실) 음성 채널을 찾을 수 없습니다.", ephemeral=True)
            return

        await interaction.response.defer()

        returned_count = 0
        if b_channel:
            for member in b_channel.members:
                try:
                    await member.move_to(destination_channel)
                    returned_count += 1
                except Exception as e:
                    print(f"{member.display_name} 복귀 실패: {e}")

        await interaction.followup.send(f"🏠 B팀 채널의 **{returned_count}명**을 {destination_channel.mention} (으)로 복귀시켰습니다!")


async def setup(bot):
    await bot.add_cog(VoiceMoverCog(bot))