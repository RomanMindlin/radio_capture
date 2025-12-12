import logging

import httpx
from sqlmodel import Session, select

from app.core.db import engine
from app.models.models import Notification

logger = logging.getLogger(__name__)

class TelegramService:
    async def send_message(self, message: str):
        # Fetch config
        with Session(engine) as session:
            notifs = session.exec(select(Notification).where(Notification.enabled == True)).all()
            for n in notifs:
                try:
                    url = f"https://api.telegram.org/bot{n.bot_token}/sendMessage"
                    async with httpx.AsyncClient() as client:
                        await client.post(url, json={"chat_id": n.chat_id, "text": message})
                except Exception as e:
                    logger.error(f"Failed to send telegram message: {e}")

telegram_bot = TelegramService()
