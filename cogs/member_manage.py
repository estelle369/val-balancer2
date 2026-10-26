import discord
from discord import app_commands
from discord.ext import commands

from config import TIER_SCORES
from database import add_or_update_member, load_members, delete_member


class MemberManageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="등록", description="본인의 이름과 티어를 등록/수정합니다.")
    @app_commands.describe(이름="본인 이름 입력", tier="티어 선택")
    @app_commands.choices(tier=[app_commands.Choice(name=t, value=t) for t in TIER_SCORES.keys()])
    async def register(self, interaction: discord.Interaction, 이름: str, tier: app_commands.Choice[str]):
        user_id = str(interaction.user.id)
        selected_tier = tier.value
        
        try:
            # DB 저장 함수 호출
            score = add_or_update_member(user_id, 이름, selected_tier)

            embed = discord.Embed(title="✅ 등록 완료 (DB 영구 저장)", color=discord.Color.green())
            embed.add_field(name="디스코드 유저", value=f"<@{user_id}>", inline=False)
            embed.add_field(name="이름", value=이름, inline=True)
            embed.add_field(name="티어", value=f"{selected_tier} ({score}점)", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"❌ 등록 중 DB 에러 발생: {e}")
            await interaction.response.send_message("❌ 데이터베이스 저장 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", ephemeral=True)

    @app_commands.command(name="대리등록", description="다른 유저나 디스코드에 없는 용병의 이름을 등록합니다.")
    @app_commands.describe(이름="등록할 유저 이름 (예: 홍길동)", tier="티어 선택", 대상_유저="디스코드 유저 지목 (용병일 경우 비워둠)")
    @app_commands.choices(tier=[app_commands.Choice(name=t, value=t) for t in TIER_SCORES.keys()])
    async def register_proxy(self, interaction: discord.Interaction, 이름: str, tier: app_commands.Choice[str], 대상_유저: discord.User = None):
        target_id = str(대상_유저.id) if 대상_유저 else f"guest_{이름}"
        display_user = f"<@{target_id}>" if 대상_유저 else f"**용병 (디스코드 미가입)**"
        selected_tier = tier.value
        
        try:
            score = add_or_update_member(target_id, 이름, selected_tier)

            embed = discord.Embed(title="✅ 대리 등록 완료 (DB 영구 저장)", color=discord.Color.green())
            embed.add_field(name="대상 유저", value=display_user, inline=False)
            embed.add_field(name="이름", value=이름, inline=True)
            embed.add_field(name="티어", value=f"{selected_tier} ({score}점)", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"❌ 대리 등록 중 DB 에러 발생: {e}")
            await interaction.response.send_message("❌ 데이터베이스 저장 중 오류가 발생했습니다.", ephemeral=True)

    @app_commands.command(name="목록", description="등록된 모든 유저 목록을 조회합니다.")
    async def member_list(self, interaction: discord.Interaction):
        members = load_members()
        if not members:
            await interaction.response.send_message("❌ 아직 등록된 유저가 없습니다.", ephemeral=True)
            return

        embed = discord.Embed(title="📋 내전 등록 유저 목록 (DB 연동)", color=discord.Color.blue())
        text = ""
        for user_id, info in members.items():
            user_display = f"**용병**" if str(user_id).startswith("guest_") else f"<@{user_id}>"
            text += f"{user_display} | **{info['riot_id']}** | {info['tier']} ({info['score']}점)\n"

        embed.description = text
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="삭제", description="본인의 등록 정보를 삭제합니다.")
    async def unregister(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        if delete_member(user_id):
            await interaction.response.send_message("✅ 등록 정보가 데이터베이스에서 성공적으로 삭제되었습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 등록된 정보가 없습니다.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(MemberManageCog(bot))