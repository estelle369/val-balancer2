import random
import discord
from discord import app_commands
from discord.ext import commands

# 발로란트 맵 풀 상수 정의
VALORANT_MAPS = [
    "어센트 (Ascent)",
    "바인드 (Bind)",
    "헤이븐 (Haven)",
    "아이스박스 (Icebox)",
    "로터스 (Lotus)",
    "선셋 (Sunset)",
    "어비스 (Abyss)",
    "펄 (Pearl)",
    "프랙처 (Fracture)",
    "스플릿 (Split)",
    "서밋(Summit)",
    "브리즈(Breeze)",
    "코로드(Corrode)"
]

# 맵 다시 뽑기 버튼이 포함된 인터랙티브 View 클래스
class ValorantMapSelectView(discord.ui.View):
    def __init__(self, timeout=180):
        super().__init__(timeout=timeout)
        self.selected_map = random.choice(VALORANT_MAPS)

    @discord.ui.button(label="🔄 다시 뽑기", style=discord.ButtonStyle.primary)
    async def reroll_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        new_map = random.choice(VALORANT_MAPS)
        while len(VALORANT_MAPS) > 1 and new_map == self.selected_map:
            new_map = random.choice(VALORANT_MAPS)
        
        self.selected_map = new_map
        
        embed = discord.Embed(
            title="🗺️ 발로란트 내전 맵 추첨 결과",
            description=f"이번 판에 진행할 맵은 **{self.selected_map}** 입니다!",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"요청자: {interaction.user.display_name}")
        
        await interaction.response.edit_message(embed=embed, view=self)


class ValorantMapCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # 슬래시 명령어 등록
    @app_commands.command(name="랜덤맵", description="발로란트 내전에서 무작위 맵을 추첨합니다.")
    async def random_map(self, interaction: discord.Interaction):
        view = ValorantMapSelectView()
        
        embed = discord.Embed(
            title="🗺️ 발로란트 내전 맵 추첨 결과",
            description=f"이번 판에 진행할 맵은 **{view.selected_map}** 입니다!",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"요청자: {interaction.user.display_name}")
        
        await interaction.response.send_message(embed=embed, view=view)


# 봇이 이 파일을 Cog로 불러올 때 호출하는 필수 함수
async def setup(bot):
    await bot.add_cog(ValorantMapCog(bot))