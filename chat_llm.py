"""
Interactive CLI Chat with LLM API (necrolich.ru / qwable-9b)
Supports direct console chat with error handling and fallback parsing.
"""

import argparse
import httpx
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

API_KEY = "us-npSSJ4QzgBGcSxql9u3xXQyVcbIfUHcu0OOd65VI"
ENDPOINT = "https://necrolich.ru/llm/v1/chat/completions"
MODEL = "qwable-9b"

def send_prompt(prompt_text: str):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 1024
    }

    print(f"\n💬 Запрос отправлен к ИИ ({MODEL})... Ожидание ответа...", flush=True)

    try:
        with httpx.Client(timeout=120.0, verify=False) as client:
            resp = client.post(ENDPOINT, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    content = msg.get("content") or ""
                    if content:
                        print(f"\n🤖 Ответ LLM ({MODEL}):\n")
                        print(content.strip())
                        print("\n" + "=" * 50)
                        return
                    else:
                        print(f"\n🤖 Ответ LLM получен, но сырой объект содержимого пуст: {choices}")
                        return
            print(f"⚠️ Ошибка сервера HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"\n❌ Ошибка подключения: {e}")

def main():
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        prompt = " ".join(sys.argv[1:])
        send_prompt(prompt)
        return

    print("=====================================================")
    print(" 🤖 ИНТЕРАКТИВНЫЙ ЧАТ С ИИ (necrolich.ru / qwable-9b)")
    print(" Напишите ваш вопрос и нажмите Enter (для выхода нажмите Ctrl+C)")
    print("=====================================================\n")

    while True:
        try:
            user_input = input("Вы > ").strip()
            if not user_input:
                continue
            send_prompt(user_input)
        except (KeyboardInterrupt, EOFError):
            print("\nВыход из чата.")
            break

if __name__ == "__main__":
    main()
