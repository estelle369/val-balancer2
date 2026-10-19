#디스코드 API와 독립적으로 순수하게 내전 방의 상태와 참가자 명단을 관리
import asyncio

class LobbyManager:
    def __init__(self, max_slots: int = 10):
        self.max_slots = max_slots
        self.active_lobbies = {}  # guild_id: dict
        self.locks = {}           # guild_id: asyncio.Lock

    def get_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self.locks:
            self.locks[guild_id] = asyncio.Lock()
        return self.locks[guild_id]

    def get_lobby(self, guild_id: int):
        return self.active_lobbies.get(guild_id)

    def create_lobby(self, guild_id: int, message_id: int, channel_id: int, host_name: str):
        self.active_lobbies[guild_id] = {
            "message_id": message_id,
            "channel_id": channel_id,
            "participants": [],
            "max_slots": self.max_slots,
            "is_closed": False,
            "host_name": host_name
        }
        return self.active_lobbies[guild_id]

    def close_lobby(self, guild_id: int):
        if guild_id in self.active_lobbies:
            self.active_lobbies[guild_id]["is_closed"] = True
            return self.active_lobbies[guild_id]
        return None

    def add_participant(self, guild_id: int, member) -> bool:
        lobby = self.get_lobby(guild_id)
        if not lobby or lobby["is_closed"]:
            return False
        
        participants = lobby["participants"]
        if member not in participants and len(participants) < lobby["max_slots"]:
            participants.append(member)
            return True
        return False

    def remove_participant(self, guild_id: int, member) -> bool:
        lobby = self.get_lobby(guild_id)
        if not lobby:
            return False
        
        participants = lobby["participants"]
        if member in participants:
            participants.remove(member)
            return True
        return False