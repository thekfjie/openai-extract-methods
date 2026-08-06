from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

try:
    from curl_cffi.requests import Session as CurlSession
except ImportError:
    CurlSession = None


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
PH_COUNTRY = "PH"
PH_CURRENCY = "PHP"
PH_LOCALE = "en-PH"
CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
CHECKOUT_UPDATE_URL = "https://chatgpt.com/backend-api/payments/checkout/update"
DEFAULT_TIMEOUT = int(os.getenv("PH_LINK_TIMEOUT", "45"))
DEFAULT_PROMO = os.getenv("PH_PROMO_ID", "plus-1-month-free").strip()
DEFAULT_PROXY_POOL = os.getenv("PH_CHECKOUT_PROXY_POOL", "")
DEFAULT_PROMOTION_PROXY_POOL = os.getenv("PH_PROMOTION_PROXY_POOL", "")

app = FastAPI(title="Philippines PHP Link")


class LinkRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str = Field(..., alias="accessToken")
    proxy: str = ""
    proxy_pool: list[str] = Field(default_factory=list, alias="proxyPool")
    promotion_proxy: str = Field("", alias="promotionProxy")
    promotion_proxy_pool: list[str] = Field(default_factory=list, alias="promotionProxyPool")
    use_promo: bool = Field(True, alias="usePromo")


def find_token(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("accessToken", "access_token", "token"):
            token = str(value.get(key) or "").strip()
            if token:
                return token
        for item in value.values():
            token = find_token(item)
            if token:
                return token
    if isinstance(value, list):
        for item in value:
            token = find_token(item)
            if token:
                return token
    return ""


def normalize_token(raw: str) -> str:
    value = str(raw or "").strip()
    if value.startswith(("{", "[")):
        try:
            return find_token(json.loads(value)) or value
        except json.JSONDecodeError:
            pass
    return value


def normalize_proxy(raw: str) -> str:
    value = str(raw or "").strip()
    if not value or value.startswith(("http://", "https://", "socks5://", "socks5h://")):
        return value
    match = re.match(r"^([^:/\s]+):(\d+):([^:]+):(.+)$", value)
    if match:
        host, port, username, password = match.groups()
        return f"http://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
    match = re.match(r"^([^:@\s]+):([^@\s]+)@([^:/\s]+):(\d+)$", value)
    if match:
        username, password, host, port = match.groups()
        return f"http://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}"
    if re.match(r"^[^:/\s]+:\d+$", value):
        return f"http://{value}"
    return value


def split_proxies(raw: str) -> list[str]:
    return [normalize_proxy(value) for value in re.split(r"[\r\n,;]+", raw) if normalize_proxy(value)]


def proxy_candidates(primary: str, requested: list[str], env_pool: str) -> list[str]:
    candidates: list[str] = []
    for value in [primary, *requested, *split_proxies(env_pool), ""]:
        proxy = normalize_proxy(value)
        if proxy not in candidates:
            candidates.append(proxy)
    return candidates


def new_session(token: str, proxy: str) -> Any:
    session: Any = CurlSession(impersonate="chrome136") if CurlSession is not None else requests.Session()
    if hasattr(session, "trust_env"):
        session.trust_env = False
    device_id = str(uuid.uuid4())
    session.headers.update(
        {
            "Accept": "*/*",
            "Accept-Language": "en-PH,en;q=0.9,en-US;q=0.8",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "oai-device-id": device_id,
            "oai-language": PH_LOCALE,
            "Cookie": f"oai-did={device_id}",
        }
    )
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in walk_strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in walk_strings(child)]
    return []


def checkout_ids(payload: Any) -> tuple[str, str]:
    stripe_id = ""
    openai_id = ""
    for text in walk_strings(payload):
        if not stripe_id:
            match = re.search(r"\bcs_(?:live|test)_[A-Za-z0-9]+", text)
            stripe_id = match.group(0) if match else ""
        if not openai_id:
            match = re.search(r"\boaics_[A-Za-z0-9]+", text)
            openai_id = match.group(0) if match else ""
    return stripe_id, openai_id


def processor_entity(payload: dict[str, Any]) -> str:
    for node in (payload, payload.get("checkout_session"), payload.get("session"), payload.get("checkout"), payload.get("data")):
        if isinstance(node, dict):
            value = str(node.get("processor_entity") or node.get("processorEntity") or "").strip()
            if value:
                return value
    return "openai_ie"


def checkout_link(payload: Any, checkout_id: str, entity: str) -> str:
    for text in walk_strings(payload):
        match = re.search(r"https://(?:chatgpt\.com/checkout|pay\.openai\.com|checkout\.stripe\.com)[^\s\"']+", text)
        if match:
            return match.group(0)
    return f"https://chatgpt.com/checkout/{entity}/{checkout_id}"


def response_json(response: Any, stage: str) -> dict[str, Any]:
    content_type = str(response.headers.get("content-type") or "").lower()
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=f"{stage} failed: {response.text[:500]}")
    if "application/json" not in content_type:
        raise HTTPException(status_code=502, detail=f"{stage} returned non-JSON content")
    try:
        data = response.json() or {}
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"{stage} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=f"{stage} returned an invalid response")
    return data


def create_checkout(session: Any, use_promo: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    body: dict[str, Any] = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": PH_COUNTRY, "currency": PH_CURRENCY},
        "checkout_ui_mode": "custom",
    }
    if use_promo and DEFAULT_PROMO:
        body["promo_campaign"] = {"promo_campaign_id": DEFAULT_PROMO, "is_coupon_from_query_param": False}
    response = session.post(CHECKOUT_URL, json=body, headers={"x-openai-target-path": "/backend-api/payments/checkout", "x-openai-target-route": "/backend-api/payments/checkout"}, timeout=DEFAULT_TIMEOUT)
    payload = response_json(response, "Philippines checkout")
    stripe_id, openai_id = checkout_ids(payload)
    checkout_id = openai_id or stripe_id or str(payload.get("checkout_session_id") or payload.get("id") or "").strip()
    if not checkout_id:
        raise HTTPException(status_code=502, detail="checkout response did not contain a checkout id")
    return payload, {"checkout_id": checkout_id, "stripe_id": stripe_id, "openai_id": openai_id, "processor_entity": processor_entity(payload)}


def update_promotion(session: Any, checkout: dict[str, Any]) -> None:
    if not DEFAULT_PROMO:
        return
    path = "/backend-api/payments/checkout/update"
    body = {
        "checkout_session_id": checkout["checkout_id"],
        "processor_entity": checkout["processor_entity"],
        "plan_name": "chatgptplusplan",
        "price_interval": "month",
        "seat_quantity": 1,
        "promo_campaign": {"promo_campaign_id": DEFAULT_PROMO, "is_coupon_from_query_param": False},
    }
    headers = {
        "Referer": f"https://chatgpt.com/checkout/{checkout['processor_entity']}/{checkout['checkout_id']}",
        "x-openai-target-path": path,
        "x-openai-target-route": path,
    }
    response = session.post(CHECKOUT_UPDATE_URL, json=body, headers=headers, timeout=DEFAULT_TIMEOUT)
    payload = response_json(response, "Philippines promotion update")
    if payload.get("success") is False:
        raise HTTPException(status_code=409, detail="Philippines promotion update was rejected")
    stripe_id, openai_id = checkout_ids(payload)
    if stripe_id:
        checkout["stripe_id"] = stripe_id
    if openai_id:
        checkout["openai_id"] = openai_id
        checkout["checkout_id"] = openai_id


@app.get("/")
@app.get("/ticdk/")
def homepage() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"ok": "true", "country": PH_COUNTRY, "currency": PH_CURRENCY, "line": "philippines"}


@app.post("/api/philippines/link")
def generate_philippines_link(req: LinkRequest) -> dict[str, Any]:
    token = normalize_token(req.access_token)
    if not token:
        raise HTTPException(status_code=400, detail="accessToken is required")
    checkout_proxies = proxy_candidates(req.proxy, req.proxy_pool, DEFAULT_PROXY_POOL)
    promotion_proxies = proxy_candidates(req.promotion_proxy, req.promotion_proxy_pool, DEFAULT_PROMOTION_PROXY_POOL)
    # The second pool is optional.  With no separate promotion proxy supplied,
    # reuse the corresponding PH Checkout proxy rather than making a direct
    # request.
    has_promotion_pool = bool(
        normalize_proxy(req.promotion_proxy)
        or any(normalize_proxy(value) for value in req.promotion_proxy_pool)
        or split_proxies(DEFAULT_PROMOTION_PROXY_POOL)
    )
    if not has_promotion_pool:
        promotion_proxies = checkout_proxies
    last_error: HTTPException | None = None
    for index, proxy in enumerate(checkout_proxies, start=1):
        session = new_session(token, proxy)
        try:
            payload, checkout = create_checkout(session, req.use_promo)
            if req.use_promo:
                promotion_proxy = promotion_proxies[(index - 1) % len(promotion_proxies)]
                if promotion_proxy:
                    session.proxies = {"http": promotion_proxy, "https": promotion_proxy}
                update_promotion(session, checkout)
            checkout_id = checkout["openai_id"] or checkout["stripe_id"] or checkout["checkout_id"]
            return {
                "ok": True,
                "line": "philippines",
                "country": PH_COUNTRY,
                "currency": PH_CURRENCY,
                "locale": PH_LOCALE,
                "checkout_id": checkout_id,
                "checkout_url": checkout_link(payload, checkout_id, checkout["processor_entity"]),
                "proxy_attempt": index,
            }
        except HTTPException as exc:
            last_error = exc
            if index == len(checkout_proxies):
                raise
    raise last_error or HTTPException(status_code=502, detail="no checkout proxy candidates")
