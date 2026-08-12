"""
Native Python Web Client for chat.deepseek.com
Uses WebAssembly WASM SHA3 Proof-of-Work solver to communicate directly
with DeepSeek Web UI Chat Client using User Session Token.
"""

import base64
import httpx
import json
import os
import struct
import sys
import logging
from typing import Dict, Any, Optional
from wasmtime import Store, Module, Instance

from app.config import DEEPSEEK_TOKEN

sys.stdout.reconfigure(encoding='utf-8')
logger = logging.getLogger("deepseek_web_client")

LOCAL_WASM = os.path.join(os.path.dirname(__file__), "wasm", "sha3_wasm_bg.7b9ca65ddd.wasm")

class DeepSeekWebClient:
    def __init__(self, token: Optional[str] = None, wasm_path: str = LOCAL_WASM):
        self.token = token or DEEPSEEK_TOKEN
        self.wasm_path = wasm_path
        self._init_wasm()
        self.session_id = None
        self.parent_message_id = None

    def _init_wasm(self):
        if not os.path.exists(self.wasm_path):
            raise FileNotFoundError(f"WASM file not found at {self.wasm_path}")

        self.store = Store()
        module = Module.from_file(self.store.engine, self.wasm_path)
        instance = Instance(self.store, module, [])
        exports = instance.exports(self.store)

        self.memory = exports["memory"]
        self.alloc = exports["__wbindgen_export_0"]
        self.add_to_stack = exports["__wbindgen_add_to_stack_pointer"]
        self.wasm_solve = exports["wasm_solve"]

    def _encode_string(self, text: str):
        data = text.encode("utf-8")
        length = len(data)
        ptr = self.alloc(self.store, length, 1)
        mem_ptr = self.memory.data_ptr(self.store)
        for i, b in enumerate(data):
            mem_ptr[ptr + i] = b
        return ptr, length

    def solve_pow(self, challenge_str: str, prefix: str, difficulty: float) -> Optional[int]:
        retptr = self.add_to_stack(self.store, -16)
        ptr_c, len_c = self._encode_string(challenge_str)
        ptr_p, len_p = self._encode_string(prefix)

        self.wasm_solve(self.store, retptr, ptr_c, len_c, ptr_p, len_p, float(difficulty))

        mem_ptr = self.memory.data_ptr(self.store)
        status = struct.unpack("<i", bytes(mem_ptr[retptr:retptr+4]))[0]
        val = struct.unpack("<d", bytes(mem_ptr[retptr+8:retptr+16]))[0]

        self.add_to_stack(self.store, 16)
        if status == 0:
            return None
        return int(val)

    def get_pow_response_b64(self, target_path: str = "/api/v0/chat/completion") -> str:
        url = "https://chat.deepseek.com/api/v0/chat/create_pow_challenge"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://chat.deepseek.com",
            "Referer": "https://chat.deepseek.com/"
        }
        payload = {"target_path": target_path}

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp_data = resp.json()

        challenge_info = resp_data["data"]["biz_data"]["challenge"]
        prefix = f"{challenge_info['salt']}_{challenge_info['expire_at']}_"

        answer = self.solve_pow(challenge_info["challenge"], prefix, challenge_info["difficulty"])

        pow_dict = {
            "algorithm": challenge_info["algorithm"],
            "answer": answer,
            "challenge": challenge_info["challenge"],
            "difficulty": challenge_info["difficulty"],
            "expire_at": challenge_info["expire_at"],
            "salt": challenge_info["salt"],
            "signature": challenge_info["signature"],
            "target_path": challenge_info["target_path"]
        }

        return base64.b64encode(json.dumps(pow_dict).encode("utf-8")).decode("utf-8")

    def create_session(self) -> str:
        url = "https://chat.deepseek.com/api/v0/chat_session/create"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://chat.deepseek.com",
            "Referer": "https://chat.deepseek.com/",
            "X-App-Version": "20241129.0"
        }
        payload = {"character_id": None}

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            data = resp.json()
            biz_data = data.get("data", {}).get("biz_data", {})
            session_id = biz_data.get("id") or biz_data.get("chat_session_id") or biz_data.get("session_id")
            return session_id

    def send_message(self, prompt: str, thinking_enabled: bool = False, search_enabled: bool = False) -> str:
        if not self.session_id:
            self.session_id = self.create_session()

        pow_b64 = self.get_pow_response_b64("/api/v0/chat/completion")

        url = "https://chat.deepseek.com/api/v0/chat/completion"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://chat.deepseek.com",
            "Referer": "https://chat.deepseek.com/",
            "X-Ds-Pow-Response": pow_b64,
            "X-App-Version": "20241129.0"
        }

        payload = {
            "prompt": prompt,
            "model": "deepseek-chat",
            "model_type": "default" if self.parent_message_id is None else None,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 4096,
            "ref_file_ids": [],
            "thinking_enabled": thinking_enabled,
            "search_enabled": search_enabled,
            "chat_session_id": self.session_id,
            "parent_message_id": self.parent_message_id
        }

        full_response_text = []

        with httpx.Client(timeout=60.0) as client:
            with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"DeepSeek Web API error HTTP {response.status_code}: {response.text}")

                for line in response.iter_lines():
                    if line and line.startswith("data: "):
                        raw_chunk = line[6:].strip()
                        if raw_chunk == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(raw_chunk)
                            if "v" in chunk_json and isinstance(chunk_json["v"], str):
                                full_response_text.append(chunk_json["v"])
                        except Exception:
                            pass

        final_text = "".join(full_response_text).strip()
        return final_text

def test_deepseek_web(prompt: str = "Привет! Проверка работы через WASM PoW.") -> Dict[str, Any]:
    try:
        client = DeepSeekWebClient()
        response_text = client.send_message(prompt)
        return {
            "status": "success",
            "content": response_text
        }
    except Exception as e:
        logger.error(f"Error testing DeepSeek Web WASM client: {e}")
        return {
            "status": "error",
            "error": str(e)
        }
