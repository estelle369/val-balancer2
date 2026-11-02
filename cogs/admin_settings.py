#관리자 설정 커맨드 
import discord
from discord import app_commands
from discord.ext import commands
from database import save_guild_setting

class AdminSettings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    setting_group = app_commands.Group(
        name="설정", 
        description="⚙️ [관리자 전용] 내전 채널 설정 커맨드 모음",
        default_permissions=discord.Permissions(manage_channels=True)
    )

    @setting_group.command(name="모집채널", description="⚙️ 현재 채널을 내전 모집 전용 채널로 지정합니다.")
    async def set_match_channel(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        channel_id = interaction.channel_id
        
        save_guild_setting(guild_id, "target_channel_id", channel_id)
        
        match_cog = self.bot.get_cog("MatchManager")
        if match_cog:
            match_cog.target_channels[guild_id] = channel_id
        
        await interaction.response.send_message(f"✅ <#{channel_id}> 채널이 **내전 모집 채널**로 영구 설정되었습니다!")

    @setting_group.command(name="결과채널", description="⚙️ 내전 참가 명단 및 밸런스 결과가 출력될 채널을 지정합니다.")
    @app_commands.describe(채널="결과 메시지를 보낼 디스코드 채널 선택")
    async def set_result_channel(self, interaction: discord.Interaction, 채널: discord.TextChannel):
        guild_id = interaction.guild_id
        channel_id = 채널.id
        
        save_guild_setting(guild_id, "result_channel_id", channel_id)
        
        match_cog = self.bot.get_cog("MatchManager")
        if match_cog:
            match_cog.result_channels[guild_id] = channel_id
        
        await interaction.response.send_message(f"📢 내전 결과가 앞으로 {채널.mention} 채널로 영구 출력됩니다!")

async def setup(bot):
    await bot.add_cog(AdminSettings(bot))