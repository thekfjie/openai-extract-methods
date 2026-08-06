"""Sentinel header adapter restored from the existing payment integration."""
from __future__ import annotations

import base64
import json
import random
import time
import uuid
from typing import Any

from curl_cffi.requests import Session

REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
REFERER = "https://sentinel.openai.com/backend-api/sentinel/frame.html"
SDK_URL = "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"


def _b64(value: Any) -> str:
    return base64.b64encode(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()).decode()


def _config(session_id: str, user_agent: str = USER_AGENT) -> list[Any]:
    now = time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime())
    elapsed = random.uniform(1000, 50000)
    return ["1920x1080", now, 4294705152, random.random(), user_agent, SDK_URL, None, None, "en-US", "en-US,en;q=0.9", random.random(), "vendor−undefined", "location", "Object", elapsed, session_id, "", 8, time.time() * 1000 - elapsed]


def _requirements(session_id: str) -> str:
    config = _config(session_id); config[3] = 1; config[9] = random.randint(5, 50)
    return "gAAAAAC" + _b64(config)


def _fnv(text: str) -> str:
    value = 2166136261
    for char in text:
        value ^= ord(char); value = (value * 16777619) & 0xFFFFFFFF
    value ^= value >> 16; value = (value * 2246822507) & 0xFFFFFFFF
    value ^= value >> 13; value = (value * 3266489909) & 0xFFFFFFFF; value ^= value >> 16
    return f"{value & 0xFFFFFFFF:08x}"


def _pow(seed: str, difficulty: str, session_id: str) -> str:
    config = _config(session_id); started = time.time()
    for nonce in range(200000):
        config[3] = nonce; config[9] = round((time.time() - started) * 1000); encoded = _b64(config)
        if _fnv(seed + encoded)[:len(difficulty)] <= difficulty:
            return "gAAAAAB" + encoded + "~S"
    return "gAAAAAB" + _b64("e")


def build_headers(proxy: str, flow: str, device_id: str, did: str = "") -> dict[str, str]:
    session_id = str(uuid.uuid4()); requirements = _requirements(session_id); device = str(device_id or did or uuid.uuid4())
    http = Session(impersonate="chrome136")
    if proxy:
        http.proxies = {"http": proxy, "https": proxy}
    response = http.post(REQ_URL, data=json.dumps({"p": requirements, "id": device, "flow": flow}, separators=(",", ":")), headers={"Content-Type": "text/plain;charset=UTF-8", "Accept": "*/*", "Origin": "https://sentinel.openai.com", "Referer": REFERER, "User-Agent": USER_AGENT}, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"sentinel/req HTTP {response.status_code}")
    challenge = response.json() or {}; c_token = str(challenge.get("token") or "")
    if not c_token:
        raise RuntimeError("sentinel challenge missing token")
    proof = challenge.get("proofofwork") if isinstance(challenge.get("proofofwork"), dict) else {}
    p_token = _pow(str(proof.get("seed") or ""), str(proof.get("difficulty") or "0"), session_id) if proof.get("required") and proof.get("seed") else requirements
    main = {"p": p_token, "t": "", "c": c_token, "id": device, "flow": flow}
    return {"openai-sentinel-token": json.dumps(main, separators=(",", ":")), "oai-device-id": device}
