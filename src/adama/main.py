import asyncio
import logging

import discord

from adama.bot import AdamaBot
from adama.config import load_config
from adama.container import Container
from adama.db.database import get_connection
from adama.utils.logging import setup_logging

logger = logging.getLogger()


async def _main():
	config = load_config()
	token = config.discord.TOKEN

	intents = discord.Intents.default()
	intents.members = True
	intents.message_content = True

	db_path = ":memory:" if config.database.IN_MEMORY else config.database.DB_PATH
	container = Container(conn=get_connection(db_path))
	bot = AdamaBot(config, intents=intents, container=container)
	try:
		await bot.start(token)
	except asyncio.CancelledError, KeyboardInterrupt:
		logger.info("Interrupt detected, attempting safe close...")
	finally:
		if not bot.is_closed():
			await bot.close()

		logger.info("Bot has shut down.")


def main():
	setup_logging()
	asyncio.run(_main())


if __name__ == "__main__":
	main()
