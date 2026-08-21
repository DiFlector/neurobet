"""
Native Python Web Client for chat.deepseek.com
Uses WebAssembly WASM SHA3 Proof-of-Work solver to communicate directly
with DeepSeek Web UI Chat Client using User Session Token.
"""

import base64
import httpx
import json
import os
import re
import struct
import sys
import logging
from typing import Dict, Any, Optional, List
from wasmtime import Store, Module, Instance

from app.config import DEEPSEEK_TOKEN

sys.stdout.reconfigure(encoding='utf-8')
logger = logging.getLogger("deepseek_web_client")

LOCAL_WASM = os.path.join(os.path.dirname(__file__), "wasm", "sha3_wasm_bg.7b9ca65ddd.wasm")


class DeepSeekStreamError(RuntimeError):
    """DeepSeek returned an in-stream error (rate limit, auth, etc.)."""

    def __init__(self, message: str, *, finish_reason: Optional[str] = None):
        super().__init__(message)
        self.finish_reason = (finish_reason or "").strip().lower()

    @property
    def is_rate_limited(self) -> bool:
        fr = self.finish_reason
        msg = str(self).lower()
        return fr in ("rate_limit_reached", "rate_limited", "too_many_requests") or (
            "too frequent" in msg or "rate limit" in msg
        )


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

    def send_message(
        self,
        prompt: str,
        thinking_enabled: bool = False,
        search_enabled: bool = False,
        *,
        new_session: bool = True,
    ) -> str:
        """
        Send one user prompt and return the assistant reply text.

        By default each call creates a **fresh chat session** (`new_session=True`).
        Reusing one session with parent_message_id=None stacks sibling variants of
        the same bubble in the DeepSeek UI (the «124 / 127» pager) and mixes
        prior match context into later answers.
        """
        if new_session or not self.session_id:
            self.session_id = self.create_session()
            self.parent_message_id = None
            if not self.session_id:
                raise RuntimeError("DeepSeek chat_session/create returned no session id")
            logger.info("DeepSeek new chat session=%s", self.session_id)

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

        # Always start a turn as a root message in this session (no parent chain).
        # Continuations would need a real parent_message_id from the prior SSE —
        # NeuroBet never multi-turns inside one chat on purpose.
        payload = {
            "prompt": prompt,
            "model": "deepseek-chat",
            "model_type": "default",
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 4096,
            "ref_file_ids": [],
            "thinking_enabled": thinking_enabled,
            "search_enabled": search_enabled,
            "chat_session_id": self.session_id,
            "parent_message_id": None,
        }

        full_response_text = []

        with httpx.Client(timeout=90.0 if search_enabled else 60.0) as client:
            with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    raise RuntimeError(f"DeepSeek Web API error HTTP {response.status_code}: {response.text}")

                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        raw_chunk = line[5:].lstrip()
                    else:
                        continue
                    if raw_chunk == "[DONE]":
                        break
                    try:
                        chunk_json = json.loads(raw_chunk)
                    except Exception:
                        continue
                    err = _extract_sse_error(chunk_json)
                    if err is not None:
                        raise DeepSeekStreamError(
                            str(err.get("content") or err.get("message") or "DeepSeek stream error"),
                            finish_reason=str(err.get("finish_reason") or err.get("code") or ""),
                        )
                    piece = _extract_sse_content(chunk_json)
                    if piece:
                        full_response_text.append(piece)
                    # Search (and sometimes plain) streams emit FINISHED on an
                    # intermediate turn before RESPONSE fragments. Never stop on
                    # empty FINISHED — wait for [DONE] / connection close.
                    if _is_stream_finished(chunk_json) and full_response_text:
                        break

        # Drop sticky session so a mistaken caller without new_session cannot
        # append another sibling into the chat we just used.
        self.session_id = None
        self.parent_message_id = None

        final_text = "".join(full_response_text).strip()
        return _sanitize_stream_text(final_text)


_STATUS_TOKENS = frozenset({
    "FINISHED", "FINISH", "DONE", "STOP", "FAILED", "ERROR",
    "SEARCHING", "THINKING", "PENDING",
})


def _extract_sse_error(chunk: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    DeepSeek may emit a top-level error object (not under ``v``), e.g.:
      {"type":"error","content":"Messages too frequent…","finish_reason":"rate_limit_reached"}
    or the same nested in ``v``.
    """
    if not isinstance(chunk, dict):
        return None
    if str(chunk.get("type") or "").lower() == "error":
        return chunk
    val = chunk.get("v")
    if isinstance(val, dict) and str(val.get("type") or "").lower() == "error":
        return val
    return None


def _is_stream_finished(chunk: Dict[str, Any]) -> bool:
    path = str(chunk.get("p") or "")
    val = chunk.get("v")
    if path.endswith("status") and isinstance(val, str) and val.upper() in _STATUS_TOKENS:
        return val.upper() in ("FINISHED", "FINISH", "DONE", "STOP")
    return False


def _fragment_texts(fragments: Any) -> List[str]:
    out: list[str] = []
    if not isinstance(fragments, list):
        return out
    for frag in fragments:
        if not isinstance(frag, dict):
            continue
        # Skip chain-of-thought / search UI fragments — keep answer content only.
        ftype = str(frag.get("type") or "").upper()
        if ftype in ("THINK", "THINKING", "SEARCH", "SEARCH_RESULT", "SEARCHING"):
            continue
        content = frag.get("content")
        if isinstance(content, str) and content:
            out.append(content)
            continue
        # Some search-final payloads nest the answer under "text".
        text = frag.get("text")
        if isinstance(text, str) and text:
            out.append(text)
    return out


def _extract_sse_content(chunk: Dict[str, Any]) -> Optional[str]:
    """
    DeepSeek web SSE uses several shapes:
      1) Initial: {"v": {"response": {"fragments": [{"type":"RESPONSE","content":"..."}]}}}
      2) Batch replace: {"p":"response/fragments","v":[...]}
      3) Append token: {"p":"response/fragments/-1/content","o":"APPEND","v":"word"}
      4) Bare delta: {"v":"word"}
      5) Status: {"p":"response/status","v":"FINISHED"}  — not content
    Older parser only took (4), so the first fragment was dropped and FINISHED leaked in.
    """
    path = str(chunk.get("p") or "")
    path_l = path.lower()
    val = chunk.get("v")
    op = str(chunk.get("o") or "").upper()

    # Never treat status / progress enums as answer text.
    if "status" in path_l or path_l.endswith("/state"):
        return None
    if isinstance(val, str) and val.strip().upper() in _STATUS_TOKENS and (
        op in ("", "SET", "REPLACE") or "status" in path_l
    ):
        # Bare {"v":"FINISHED"} without a content path — skip.
        if not path or "status" in path_l or "fragment" not in path_l:
            return None

    # (1) Nested initial fragments
    if isinstance(val, dict):
        response = val.get("response") if isinstance(val.get("response"), dict) else val
        texts = _fragment_texts(response.get("fragments") if isinstance(response, dict) else None)
        if texts:
            return "".join(texts)
        return None

    # (2) Array of fragments at response/fragments
    if isinstance(val, list) and ("fragment" in path_l or not path):
        texts = _fragment_texts(val)
        if texts:
            return "".join(texts)
        return None

    # (3)/(4) String deltas — only when clearly content, not a status token.
    if isinstance(val, str):
        if val.strip().upper() in _STATUS_TOKENS and "content" not in path_l:
            return None
        # Prefer content paths; allow bare {"v":"..."} deltas (no path).
        if path and "content" not in path_l and "fragment" not in path_l:
            return None
        return val

    return None


def _sanitize_stream_text(text: str) -> str:
    """Strip leaked stream sentinels that still slip through."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    # Trailing/leading status tokens from bad chunk joins (often glued: «...текст.FINISHED»).
    cleaned = re.sub(
        r"[\s.]*\b(?:FINISHED|FINISH|DONE|STOP|FAILED|ERROR)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(?:FINISHED|FINISH|DONE|STOP)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # DeepSeek sometimes echoes citation markers like [citation:6] — keep notes readable.
    cleaned = re.sub(r"\[citation:\d+\]", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


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
