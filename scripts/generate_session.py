"""
Run ONCE locally to generate a Telethon session string:
  python3 scripts/generate_session.py

Copy the printed session string → add to Railway Variables as TG_SESSION
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 39433761
API_HASH = "ed7fd93fefa245a4bda0888d5315a122"


async def main():
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        session_string = client.session.save()
        print("\n" + "="*60)
        print("✅ SESSION STRING (copy this to Railway → TG_SESSION):")
        print("="*60)
        print(session_string)
        print("="*60 + "\n")


asyncio.run(main())
