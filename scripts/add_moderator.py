"""
Run once to add the first moderator:
  DATABASE_URL=... python scripts/add_moderator.py <tg_user_id> <username>
"""
import asyncio
import sys
import os
import asyncpg


async def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/add_moderator.py <tg_user_id> <username>")
        sys.exit(1)

    tg_user_id = int(sys.argv[1])
    username = sys.argv[2]

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    await pool.execute(
        """
        INSERT INTO rbac_users (tg_user_id, username, role)
        VALUES ($1, $2, 'admin')
        ON CONFLICT (tg_user_id) DO UPDATE SET role='admin', active=TRUE
        """,
        tg_user_id, username,
    )
    await pool.close()
    print(f"✅ Moderator added: @{username} (id={tg_user_id})")


asyncio.run(main())
