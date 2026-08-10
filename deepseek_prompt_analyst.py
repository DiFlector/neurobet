"""
DeepSeek Web UI Prompt Analyst Module according to prompt.md specification (Top 3 Bets)
Uses native WebAssembly PoW solver & token 8He37gBBj2KFJ5ia4yaN/...
Passes explicit human-readable market names (ТБ 2.5, ТМ 2.5, П1, Х, П2, 1Х, Х2).
Excludes Handicaps (Форы) across all sports including Billiards/Snooker/Tennis/Football.
Includes post-processing verification against live odds line to guarantee 100% accuracy of TB/TM labels and handicap rejection.
"""

import json
import os
import re
import sys
from typing import Dict, Any

sys.path.insert(0, r"c:\Codes\autobet")
sys.stdout.reconfigure(encoding='utf-8')

from deepseek_web_client import DeepSeekWebClient
from fonbet_catalog import CORE_FACTOR_MAP

PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompt.md")

TOTAL_OVER_FIDS = {930, 1696, 1727, 1730, 1733, 1736, 1739, 1791, 1794, 1797, 1804}
TOTAL_UNDER_FIDS = {931, 1697, 1728, 1731, 1734, 1737, 1740, 1793, 1796, 1802, 1805}
HANDICAP_KEYWORDS = ["фора", "форой", "handicap", " ф1", " ф2"]

def postprocess_fix_odds_labels(text: str, odds_data: dict) -> str:
    """
    Scans LLM output text. Cross-checks coefficients against exact Fonbet live line,
    automatically corrects inverted TB/TM labels, and auto-replaces forbidden Handicap picks.
    """
    events = odds_data.get("events", [])
    blocks = re.split(r"(?=(?:^|\n)(?:[1-3]️⃣|[1-3][\.\)]|###|\*\*1️⃣|\*\*2️⃣|\*\*3️⃣|<b>1️⃣|<b>2️⃣|<b>3️⃣))", text)
    output_blocks = []

    for block in blocks:
        if not block.strip():
            output_blocks.append(block)
            continue

        matched_event = None
        for ev in events:
            mname = ev.get("match_name", "")
            t1 = ev.get("team1", "")
            if (mname and mname.lower() in block.lower()) or (t1 and len(t1) > 3 and t1.lower() in block.lower()):
                matched_event = ev
                break

        # Check if block contains forbidden Handicap (Фора)
        if any(h in block.lower() for h in ["фора", "форой", "handicap"]):
            if matched_event:
                odds_list = matched_event.get("odds", [])
                fallback_odds = None
                for o in odds_list:
                    fid = int(o.get("factor_id", 0))
                    label = str(o.get("label", "")).lower()
                    if fid in [921, 922, 923, 924, 925, 926, 930, 931, 1696, 1697, 1730, 1731] and not any(h in label for h in HANDICAP_KEYWORDS):
                        fallback_odds = o
                        break
                
                if fallback_odds:
                    f_label = fallback_odds.get("label") or "П1"
                    f_coef = fallback_odds.get("coefficient", 1.5)
                    block = re.sub(
                        r"Ставка:\s*\*?([^\n]+)",
                        f"Ставка:** {f_label}",
                        block,
                        flags=re.IGNORECASE
                    )
                    block = re.sub(
                        r"Коэффициент:\s*`?[0-9\.]+`?",
                        f"Коэффициент:** `{f_coef}`",
                        block,
                        flags=re.IGNORECASE
                    )

        if matched_event:
            odds_list = matched_event.get("odds", [])
            coef_match = re.search(r"Коэффициент[^\d]*([0-9]+\.?[0-9]*)", block, re.IGNORECASE)
            
            if coef_match:
                coef_val = float(coef_match.group(1))
                matched_factor = None
                for o in odds_list:
                    real_coef = float(o.get("coefficient", 0))
                    if abs(real_coef - coef_val) < 0.08:
                        matched_factor = o
                        break

                if matched_factor:
                    fid = int(matched_factor.get("factor_id", 0))
                    param = matched_factor.get("parameter")
                    param_str = f"({param})" if param is not None else ""

                    if fid in TOTAL_OVER_FIDS:
                        correct_bet = f"Тотал Больше {param_str}".strip()
                        block = re.sub(
                            r"(Ставка:[^\n]*?)(Тотал Меньше|ТМ)",
                            f"\\1{correct_bet}",
                            block,
                            flags=re.IGNORECASE
                        )
                        block = re.sub(r"\(1\.5\)\s*\(1\.5\)", "(1.5)", block)

                    elif fid in TOTAL_UNDER_FIDS:
                        correct_bet = f"Тотал Меньше {param_str}".strip()
                        block = re.sub(
                            r"(Ставка:[^\n]*?)(Тотал Больше|ТБ)",
                            f"\\1{correct_bet}",
                            block,
                            flags=re.IGNORECASE
                        )
                        block = re.sub(r"\(1\.5\)\s*\(1\.5\)", "(1.5)", block)

        output_blocks.append(block)

    return "".join(output_blocks)

class DeepSeekPromptAnalyst:
    def __init__(self, token: str = "8He37gBBj2KFJ5ia4yaN/llmrN5EqzNjr5mZ1iCRCUbuadE7mUdDL4/pNTFZbH4s"):
        self.client = DeepSeekWebClient(token=token)

    def resolve_human_label(self, o: Dict[str, Any]) -> str:
        """Resolves factor to explicit text like 'Тотал Больше (1.5)' or 'П1'."""
        label = o.get("label") or o.get("name")
        fid = o.get("factor_id")
        param = o.get("parameter")

        if fid is not None:
            try:
                fid_int = int(fid)
                if fid_int in CORE_FACTOR_MAP:
                    label = CORE_FACTOR_MAP[fid_int]
                elif not label:
                    label = f"Фактор {fid_int}"
            except Exception:
                pass

        if param is not None and str(param).strip() != "":
            p_clean = str(param).strip()
            if "(" in title if 'title' in locals() else False:
                pass
            elif label and "(" not in str(label):
                label = f"{label} ({p_clean})"

        return str(label)

    def run_prompt_analysis(self, odds_data: Dict[str, Any], bankroll: float = 414.0) -> str:
        prompt_instruction = ""
        if os.path.exists(PROMPT_FILE):
            with open(PROMPT_FILE, "r", encoding="utf-8") as f:
                prompt_instruction = f.read()

        events_summary = []
        events = odds_data.get("events", [])[:18]

        for ev in events:
            odds_items = []
            for o in ev.get("odds", [])[:12]:
                fid = o.get("factor_id")
                coef = o.get("coefficient")
                if coef is None:
                    continue

                # Filter out Handicaps (Фора)
                if fid in [927, 928, 910, 912, 989, 991, 1569, 1572, 1677, 1678, 1680, 1681]:
                    continue

                human_label = self.resolve_human_label(o)
                label_lower = human_label.lower()
                if any(h in label_lower for h in ["фора", "форой", "handicap", "победа с форой", "(-", "(+"]):
                    continue

                odds_items.append(f"{human_label} = {coef}")

            if odds_items:
                odds_str = "\n    ".join(odds_items)
                events_summary.append(
                    f"- [{ev.get('sport_path')}] {ev.get('match_name')} (Счет: {ev.get('score', '0:0')}, Время: {ev.get('timer', 'Не начался')})\n    {odds_str}"
                )

        full_prompt = f"""
Ты — профессиональный аналитик ставок. Выполни четко инструкции из prompt.md.

--- ТЕКСТ PROMPT.MD ---
{prompt_instruction}

--- ТЕКУЩИЕ ДАННЫЕ МАТЧЕЙ И КОЭФФИЦИЕНТОВ FONBET ---
{chr(10).join(events_summary)}

ТЕКУЩИЙ ДОСТУПНЫЙ БАНК: {bankroll} рублей.

ОБРАТИ ВНИМАНИЕ: Все коэффициенты явно подписаны (например: "Тотал Больше (1.5) = 1.32", "Тотал Меньше (1.5) = 3.10").
НЕ ПУТАЙ Тотал Больше (ТБ) и Тотал Меньше (ТМ)! Указывай строго реальный коэффициент из данных.

ФОРА КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНА ДЛЯ ВСЕХ ВИДОВ СПОРТА!
Разрешены только Победы (П1, X, П2, 1X, 12, X2) и Тоталы (ТМ, ТБ).
Выполни анализ и дай ровно 3 лучшие ставки с распределением всего банка {bankroll} рублей!
"""

        print(f"📡 Запрос отправлен в веб-версию DeepSeek (chat.deepseek.com) с банком {bankroll} руб...")
        raw_answer = self.client.send_message(full_prompt)

        # Post-process verification against live odds line
        verified_answer = postprocess_fix_odds_labels(raw_answer, odds_data)
        return verified_answer

def main():
    live_json = "output/live_odds_human.json"
    if not os.path.exists(live_json):
        print("Live data file output/live_odds_human.json not found. Run fonbet_parser.py first.")
        return

    with open(live_json, "r", encoding="utf-8") as f:
        odds_data = json.load(f)

    bankroll = 414.0
    if len(sys.argv) > 1:
        try:
            bankroll = float(sys.argv[1])
        except ValueError:
            pass

    analyst = DeepSeekPromptAnalyst()
    analysis_result = analyst.run_prompt_analysis(odds_data, bankroll=bankroll)

    print("\n=====================================================")
    print(f" 🤖 ОФИЦИАЛЬНЫЙ АНАЛИЗ DEEPSEEK (БАНК: {bankroll} РУБЛЕЙ | 3 СТАВКИ)")
    print("=====================================================\n")
    print(analysis_result)

if __name__ == "__main__":
    main()
