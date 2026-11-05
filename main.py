import os
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from database import init_db, get_db_connection
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


class ValBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    # 5분마다 Aiven DB 자동 꺼짐 방지 핑 (PaaS 세션 유지)
    @tasks.loop(minutes=5)
    async def keep_db_alive(self):
        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1;")
            conn.close()
        except Exception as e:
            print(f"⚠️ DB Ping 실패: {e}")

    async def setup_hook(self):
        # Cogs 모듈 자동 로드 목록 (match_manage.py 파일명과 일치시킴)
        cogs_to_load = [
            "cogs.match_manage",
            "cogs.admin_settings",
            "cogs.valorant_map",
            "cogs.voice_mover"
        ]
        
        for cog in cogs_to_load:
            try:
                await self.load_extension(cog)
                print(f"✅ {cog} 로드 성공")
            except Exception as e:
                print(f"❌ {cog} 로드 실패: {e}")

        # 슬래시 커맨드 동기화
        await self.tree.sync()
        print("✅ 슬래시 커맨드가 동기화되었습니다!")

        # DB 핑 루프 시작
        if not self.keep_db_alive.is_running():
            self.keep_db_alive.start()


bot = ValBot()

@bot.event
async def on_ready():
    print(f"✅ {bot.user.name} 봇이 성공적으로 로그인했습니다!")


if __name__ == "__main__":
    init_db()
    keep_alive()
    bot.run(TOKEN)