"""
LLM Risk Manager & Match Analyst Integration Module
Communicates with https://necrolich.ru/llm/v1/chat/completions (model qwable-9b)
for deep match validation, risk assessment, and dispute resolution.
"""

import httpx
import json
import logging
import sys
from typing import Dict, Any, Optional

sys.path.insert(0, r"c:\Codes\autobet")
sys.stdout.reconfigure(encoding='utf-8')

logger = logging.getLogger("llm_analyzer")

class LLMRiskAnalyst:
    def __init__(
        self,
        api_key: str = "us-npSSJ4QzgBGcSxql9u3xXQyVcbIfUHcu0OOd65VI",
        endpoint: str = "https://necrolich.ru/llm/v1/chat/completions",
        model: str = "qwable-9b"
    ):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def analyze_bet_safety(self, match_info: Dict[str, Any], bet_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submits match data and neural net predictions to LLM for risk analysis and validation.
        """
        system_prompt = (
            "Ты — строго профессиональный спортивный Риск-Менеджер и Эксперт по безопасности ставок. "
            "Твоя задача — проанализировать предложенную ставку, выявить возможные подводные камни "
            "и дать итоговый вердикт: [БЕЗОПАСНО], [СРЕДНИЙ РИСК] или [ОТКЛОНЕНО]. "
            "Отвечай кратко, емко и по существу на русском языке."
        )

        user_prompt = f"""
Проведи экспертный аудит безопасности ставки на матч:

📍 Лига: {match_info.get('sport_path')}
⚔️ Матч: {match_info.get('match_name')}
📊 Счет: {match_info.get('score')} ({match_info.get('timer')})
🎯 Ставка: {bet_info.get('bet_target')}
💰 Коэффициент: {bet_info.get('bookmaker_odds')}
🧠 Нейросеть (PyTorch+LGBM): {bet_info.get('model_probability')}
🔥 Матожидание (+EV): {bet_info.get('expected_value_ev')}

Дай краткий вердикт и 2 коротких аргумента безопасности.
"""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 512,
            "temperature": 0.3
        }

        try:
            # High timeout and verify=False to prevent SSL / network timeouts
            with httpx.Client(timeout=60.0, verify=False) as client:
                resp = client.post(self.endpoint, headers=self.headers, json=payload)
                if resp.status_code == 200:
                    res_data = resp.json()
                    choices = res_data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        content = msg.get("content") or ""
                        if content:
                            return {
                                "status": "success",
                                "analysis": content.strip()
                            }
                return {
                    "status": "error",
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"
                }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
