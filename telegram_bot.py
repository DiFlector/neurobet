"""
Telegram Bot for AutoBetting System (PyTorch + LightGBM + DeepSeek AI)
Bot Username: @dftoolsbot
Authorized User ID: 537737180
Features:
- Dynamic Bankroll prompt when 'Прогноз' is pressed
- Strict market filter: Wins/Draws (1X2, 1X/12/X2) & Totals (ТМ/ТБ) ONLY (No Handicaps)
- Clean card formatting (No preambles, no <u> underlines, no dashed separators)
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import httpx
from typing import List, Dict, Any, Optional

sys.path.insert(0, r"c:\Codes\autobet")
sys.stdout.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("telegram_bot")

from fonbet_parser import FonbetParser
from deepseek_prompt_analyst import DeepSeekPromptAnalyst
from deepseek_web_client import DeepSeekWebClient

BOT_TOKEN = "8614860014:AAFRk86vJQljDsioTB_oAnm0lFKw_Yv6Wvo"
ALLOWED_USER_IDS = [537737180]

BASE_TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

def clean_and_format_telegram_message(text: str, bankroll: float) -> str:
    """Removes preambles, strategy sections, underlines, dashed separators, and formats clean HTML."""
    if not text:
        return ""

    # Strip strategy & footer sections
    text = re.split(r"(?:📊|###|\*\*)*\s*(?:СТРАТЕГИЯ|СЦЕНАРИИ|ОЖИДАЕМЫЙ ПРОФИТ|ИТОГОВАЯ СТРАТЕГИЯ)", text, flags=re.IGNORECASE)[0]
    text = text.replace("FINISHED", "").strip()

    # Strip any preamble before the first pick emoji (1️⃣, 2️⃣, 3️⃣)
    match_start = re.search(r"(?=(?:^|\n)(?:[1-3]️⃣|\*\*1️⃣|\*\*2️⃣|\*\*3️⃣|1️⃣|2️⃣|3️⃣))", text)
    if match_start and match_start.start() > 0:
        text = text[match_start.start():]

    # Remove <u> underlines entirely
    text = text.replace("<u>", "").replace("</u>", "")

    # Remove markdown headers ### -> bold
    text = re.sub(r'^#{1,6}\s*(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # Convert blockquotes > text to <b>text</b>
    text = re.sub(r'^\s*>\s*(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # Convert bold **text** -> <b>text</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    # Convert italic *text* or _text_ -> <i>text</i>
    text = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!_)_([^_]+)_(?!_)', r'<i>\1</i>', text)

    # Convert code `text` -> <code>text</code>
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

    # Remove messy horizontal separators (---, ───)
    text = re.sub(r'^\s*[-─_]{3,}\s*$', '', text, flags=re.MULTILINE)

    # Clean up empty lines
    lines = [line.strip() for line in text.split('\n')]
    cleaned_lines = []
    prev_empty = False

    for line in lines:
        if not line:
            if not prev_empty:
                cleaned_lines.append("")
                prev_empty = True
        else:
            cleaned_lines.append(line)
            prev_empty = False

    content = "\n".join(cleaned_lines).strip()
    header = f"🏆 <b>ТОП-3 СБАЛАНСИРОВАННЫХ ПРОГНОЗА</b> (Банк: <code>{bankroll:g} ₽</code>)\n"
    return f"{header}\n{content}"

class AutoBetTelegramBot:
    def __init__(self, allowed_ids: List[int] = ALLOWED_USER_IDS):
        self.allowed_ids = allowed_ids
        self.offset = 0
        self.parser = FonbetParser(out_dir="output")
        self.analyst = DeepSeekPromptAnalyst()
        self.user_state = {}

    async def send_message(self, chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None):
        """Sends an HTML formatted Telegram message with automatic fallback."""
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                resp = await client.post(f"{BASE_TELEGRAM_URL}/sendMessage", json=payload)
                if resp.status_code != 200:
                    logger.warning(f"HTML parse failed ({resp.text}). Falling back to clean text.")
                    clean_text = re.sub(r'<[^>]+>', '', text)
                    payload["text"] = clean_text
                    payload["parse_mode"] = ""
                    await client.post(f"{BASE_TELEGRAM_URL}/sendMessage", json=payload)
            except Exception as e:
                logger.error(f"Failed to send message to {chat_id}: {e}")

    def get_main_keyboard(self) -> Dict[str, Any]:
        """Generates reply keyboard markup with 'Прогноз' button."""
        return {
            "keyboard": [
                [{"text": "🎲 Прогноз"}],
                [{"text": "🤖 Спросить ИИ (Чат)"}, {"text": "📊 Статистика моделей"}]
            ],
            "resize_keyboard": True,
            "persistent": True
        }

    async def handle_prediction_request(self, chat_id: int, bankroll: float):
        """Executes Live parsing and DeepSeek analysis for entered bankroll."""
        await self.send_message(
            chat_id,
            f"⏳ <b>Запуск анализа для банка {bankroll:g} ₽...</b>\n\n"
            "• Получение котировок Live (без фор, только Победы и Тоталы)...\n"
            "• Оценка вероятностей PyTorch + LightGBM...\n"
            "• Формирование портфеля через DeepSeek AI (PoW WASM solver)..."
        )

        try:
            # Step 1: Parse fresh Live odds
            self.parser.run(place="live", sport_filter="all", export_format="human")
            live_json = "output/live_odds_human.json"

            if not os.path.exists(live_json):
                await self.send_message(chat_id, "❌ Ошибка парсинга линии Fonbet Live.")
                return

            with open(live_json, "r", encoding="utf-8") as f:
                odds_data = json.load(f)

            # Step 2: Run DeepSeek prompt analysis with dynamic bankroll
            loop = asyncio.get_event_loop()
            raw_analysis_text = await loop.run_in_executor(
                None, lambda: self.analyst.run_prompt_analysis(odds_data, bankroll=bankroll)
            )

            # Clean and format card layout
            msg_text = clean_and_format_telegram_message(raw_analysis_text, bankroll=bankroll)

            if len(msg_text) > 4000:
                chunks = [msg_text[i:i+3900] for i in range(0, len(msg_text), 3900)]
                for chunk in chunks:
                    await self.send_message(chat_id, chunk, reply_markup=self.get_main_keyboard())
            else:
                await self.send_message(chat_id, msg_text, reply_markup=self.get_main_keyboard())

        except Exception as e:
            logger.error(f"Error executing prediction pipeline: {e}")
            await self.send_message(chat_id, f"❌ Произошла ошибка при анализе: {e}", reply_markup=self.get_main_keyboard())

    async def handle_ai_chat_request(self, chat_id: int, user_text: str):
        """Processes user chat prompts directly with DeepSeek AI."""
        await self.send_message(chat_id, "🤖 <i>Ожидаем ответ от DeepSeek AI...</i>")
        try:
            loop = asyncio.get_event_loop()
            client = DeepSeekWebClient()
            raw_answer = await loop.run_in_executor(None, lambda: client.send_message(user_text))
            cleaned_answer = clean_and_format_telegram_message(raw_answer, bankroll=0.0)
            
            resp_msg = f"🤖 <b>Ответ ИИ DeepSeek:</b>\n\n{cleaned_answer}"
            await self.send_message(chat_id, resp_msg, reply_markup=self.get_main_keyboard())
        except Exception as e:
            await self.send_message(chat_id, f"❌ Ошибка ИИ: {e}", reply_markup=self.get_main_keyboard())

    async def process_update(self, update: Dict[str, Any]):
        """Processes incoming Telegram webhook / polling update."""
        message = update.get("message")
        if not message:
            return

        chat_id = message.get("chat", {}).get("id")
        user_id = message.get("from", {}).get("id")
        text = message.get("text", "").strip()

        # Authorization check
        if user_id not in self.allowed_ids:
            logger.warning(f"Unauthorized access attempt by user_id: {user_id}")
            await self.send_message(
                chat_id,
                f"⛔ <b>Доступ запрещен.</b>\nВаш Telegram ID: <code>{user_id}</code> не найден в списке разрешенных."
            )
            return

        # Commands and buttons
        if text in ["/start", "/help"]:
            self.user_state[chat_id] = None
            welcome_text = (
                "👋 <b>Добро пожаловать в ИИ-Систему Ставок AutoBet!</b>\n\n"
                "Система объединяет:\n"
                "• <b>Fonbet Live Parser</b> (только Победы/Ничьи и Тоталы, без фор)\n"
                "• <b>PyTorch OddsNet + LightGBM</b> (обучены на 1.68 млн матчей)\n"
                "• <b>DeepSeek AI</b> (динамическое распределение любого банка)\n\n"
                "Нажмите кнопку <b>🎲 Прогноз</b> ниже для получения лучших ставок!"
            )
            await self.send_message(chat_id, welcome_text, reply_markup=self.get_main_keyboard())

        elif text in ["🎲 Прогноз", "/predict"]:
            self.user_state[chat_id] = "WAITING_FOR_BANKROLL"
            await self.send_message(
                chat_id,
                "💰 <b>Введите ваш текущий доступный банк (в рублях):</b>\n"
                "<i>Например: 500, 1000 или 711</i>"
            )

        elif text in ["📊 Статистика моделей", "/stats"]:
            self.user_state[chat_id] = None
            stats_text = (
                "📊 <b>СТАТИСТИКА МОДЕЛЕЙ И ОБУЧЕНИЯ</b>\n"
                "───────────────────────────\n"
                "• <b>Исторический датасет</b>: 2 024 764 матчей (2 года)\n"
                "• <b>Очищенных чистых матчей</b>: 1 684 524 событий\n"
                "• <b>Точность LightGBM</b>: 100.00% (на обучении) | 49.3% (Prematch 1X2)\n"
                "• <b>Точность PyTorch OddsNet</b>: 99.97% (на обучении) | 49.3% (Prematch 1X2)\n"
                "• <b>Фильтр рынков</b>: Победы (П1, X, П2, 1X, 12, X2) и Тоталы (ТМ/ТБ). Форы исключены.\n"
                "• <b>Веб-интеграция ИИ</b>: DeepSeek-Chat (chat.deepseek.com)\n"
                "• <b>PoW Solver</b>: WebAssembly SHA3 (sha3_wasm_bg.wasm)\n"
                "───────────────────────────\n"
                "Статус системы: 🟢 <b>ГОТОВА К РАБОТЕ</b>"
            )
            await self.send_message(chat_id, stats_text, reply_markup=self.get_main_keyboard())

        elif text in ["🤖 Спросить ИИ (Чат)", "/chat"]:
            self.user_state[chat_id] = "WAITING_FOR_CHAT"
            await self.send_message(
                chat_id,
                "💬 Напишите ваш вопрос к DeepSeek AI (например: <i>'Как оценить форму команд во 2-м тайме?'</i>):"
            )

        else:
            current_state = self.user_state.get(chat_id)

            if current_state == "WAITING_FOR_BANKROLL":
                try:
                    clean_str = re.sub(r"[^\d.]", "", text.replace(",", "."))
                    bankroll = float(clean_str)
                    if bankroll <= 0:
                        raise ValueError()
                    self.user_state[chat_id] = None
                    await self.handle_prediction_request(chat_id, bankroll=bankroll)
                except Exception:
                    await self.send_message(
                        chat_id,
                        "⚠️ <b>Пожалуйста, введите корректное положительное число для банка (в рублях):</b>\n"
                        "<i>Например: 500, 1000 или 711</i>"
                    )

            elif current_state == "WAITING_FOR_CHAT" or not text.startswith("/"):
                self.user_state[chat_id] = None
                await self.handle_ai_chat_request(chat_id, text)

    async def start_polling(self):
        """Starts async polling loop for Telegram updates."""
        logger.info(f"🚀 Telegram Bot started! Allowed User IDs: {self.allowed_ids}")
        logger.info("Bot is listening for updates...")

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                try:
                    url = f"{BASE_TELEGRAM_URL}/getUpdates?offset={self.offset}&timeout=20"
                    resp = await client.get(url)

                    if resp.status_code == 200:
                        data = resp.json()
                        updates = data.get("result", [])

                        for update in updates:
                            self.offset = update["update_id"] + 1
                            await self.process_update(update)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Polling error: {e}")
                    await asyncio.sleep(2)

def main():
    bot = AutoBetTelegramBot(allowed_ids=ALLOWED_USER_IDS)
    try:
        asyncio.run(bot.start_polling())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")

if __name__ == "__main__":
    main()
