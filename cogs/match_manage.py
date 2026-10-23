#/교체 기능 
import discord
from discord import app_commands
from discord.ext import commands

from database import load_members
from utils.balancer import calculate_best_teams


class MatchManageCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
        from main import active_matches, last_balanced_match, run_balance_and_send, update_match_status

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
        for message_id, match in reversed(list(active_matches.items())):
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
        await update_match_status(target_match)

        # 5. 10명 명단이 완성된 상태였다면 밸런스 결과 재출력 및 /팀이동 데이터 자동 갱신
        if len(participants) == 10:
            output_channel = interaction.channel
            await run_balance_and_send(output_channel, participants)

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