"""HeroSMS and TeleAuto clients: phone-number purchase and SMS code retrieval."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from integrations.text_utils import collect_string_values, normalize_text


STATUS_LABELS = {
    "STATUS_WAIT_CODE": "等待验证码",
    "STATUS_WAIT_RETRY": "等待重发",
    "STATUS_WAIT_RESEND": "等待再次发送",
    "STATUS_WAIT_ACTIVATION": "等待激活",
    "STATUS_WAIT_GET": "号码已下发",
    "STATUS_OK": "收到验证码",
    "STATUS_CANCEL": "已取消",
    "FULL_SMS": "短信已满",
}

NORMALIZED_STATES = {
    "STATUS_WAIT_CODE": "waiting_for_code",
    "STATUS_WAIT_RETRY": "waiting_for_retry",
    "STATUS_WAIT_RESEND": "waiting_for_resend",
    "STATUS_WAIT_ACTIVATION": "waiting_for_activation",
    "STATUS_WAIT_GET": "number_issued",
    "STATUS_OK": "code_received",
    "STATUS_CANCEL": "canceled",
    "FULL_SMS": "finished",
}

class HeroSmsError(Exception):
    pass


class TeleAutoError(HeroSmsError):
    pass


class PurchaseError(HeroSmsError):
    def __init__(self, message: str, attempts: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts or []

class HeroSmsClient:
    def __init__(self, api_key: str, api_url: str, timeout_ms: int) -> None:
        self.api_key = api_key
        self.api_url = api_url
        self.timeout_seconds = timeout_ms / 1000
        self.cache_ttl_seconds = 600
        self._cache: dict[str, dict[str, Any]] = {}

    def _get_cached(self, key: str) -> Any | None:
        cached = self._cache.get(key)
        if not cached:
            return None
        expires_at = cached.get("expiresAt", 0)
        if datetime.now().timestamp() >= expires_at:
            self._cache.pop(key, None)
            return None
        return cached.get("value")

    def _set_cached(self, key: str, value: Any) -> Any:
        self._cache[key] = {
            "value": value,
            "expiresAt": datetime.now().timestamp() + self.cache_ttl_seconds,
        }
        return value

    def request(self, action: str, **params: Any) -> Any:
        if not self.api_key:
            raise HeroSmsError("未配置 HERO_SMS_API_KEY")

        query = {"api_key": self.api_key, "action": action}
        for key, value in params.items():
            if value in (None, ""):
                continue
            query[key] = str(value)

        request_url = f"{self.api_url}?{urlencode(query)}"
        request = Request(
            request_url,
            headers={"Accept": "application/json,text/plain;q=0.9,*/*;q=0.8", "User-Agent": "python-herosms-client/1.0"},
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8", errors="replace").strip()
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace").strip()
            raise HeroSmsError(f"上游请求失败: HTTP {error.code} {body}".strip())
        except URLError as error:
            raise HeroSmsError(f"上游连接失败: {error.reason}")

        if not text:
            return ""
        if text.startswith("{") or text.startswith("["):
            payload = json.loads(text)
        else:
            payload = text

        if isinstance(payload, str) and payload.startswith(("BAD_", "ERROR_", "NO_", "WRONG_", "SQL_")):
            raise HeroSmsError(payload)
        return payload

    def get_balance(self) -> Any:
        return self.request("getBalance")

    def get_balance_cached(self, force: bool = False) -> Any:
        if not force:
            cached = self._get_cached("balance")
            if cached is not None:
                return cached
        balance = self.get_balance()
        return self._set_cached("balance", balance)

    def get_services(self) -> list[dict[str, str]]:
        cached = self._get_cached("services")
        if cached is not None:
            return cached
        services = self._normalize_services(self.request("getServicesList"))
        return self._set_cached("services", services)

    def get_countries(self, force: bool = False) -> list[dict[str, Any]]:
        cached = None if force else self._get_cached("countries")
        if cached is not None:
            return cached
        countries = self._normalize_countries(self.request("getCountries"))
        return self._set_cached("countries", countries)

    def resolve_service(self, name: str, aliases: list[str]) -> tuple[dict[str, str], list[dict[str, str]]]:
        services = self.get_services()
        match = self._pick_by_name(services, name, aliases, ("name", "code"))
        if not match:
            raise HeroSmsError(f"找不到服务: {name}")
        return match, services

    def resolve_country(self, name: str, aliases: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        countries = self.get_countries()
        match = self._pick_by_name(countries, name, aliases, ("name", "localName", "code"))
        if not match:
            raise HeroSmsError(f"找不到国家/地区: {name}")
        return match, countries

    def get_pricing(self, service_code: str, country_code: str) -> dict[str, Any]:
        cache_key = f"pricing:{service_code}:{country_code}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        payload = self.request("getPrices", service=service_code, country=country_code)
        parsed = self._extract_price_info(payload, country_code, service_code)
        if parsed:
            return self._set_cached(cache_key, parsed)
        fallback = self.request("getPricesVerification", service=service_code, country=country_code)
        result = self._extract_price_info(fallback, country_code, service_code) or {"price": None, "count": None, "raw": fallback}
        return self._set_cached(cache_key, result)

    def get_operators(self, service_code: str, country_code: str, force: bool = False) -> list[str]:
        cache_key = f"operators:{service_code}:{country_code}"
        cached = None if force else self._get_cached(cache_key)
        if cached is not None:
            return cached
        try:
            payload = self.request("getOperators", country=country_code)
        except HeroSmsError:
            try:
                payload = self.request("getPricesVerification", service=service_code, country=country_code)
            except HeroSmsError:
                return self._set_cached(cache_key, ["any"])
        if isinstance(payload, dict):
            source = (
                payload.get("operators")
                or payload.get("countryOperators")
                or payload.get("data")
                or payload.get("items")
                or payload
            )
        else:
            source = payload
        if isinstance(source, dict):
            source = source.get(country_code) or source.get(str(int(country_code)) if str(country_code).isdigit() else country_code) or source
        if not source:
            return self._set_cached(cache_key, ["any"])
        values = []
        if isinstance(source, dict):
            iterator = source.values()
        else:
            iterator = source
        for item in iterator:
            if isinstance(item, dict):
                value = item.get("name") or item.get("code") or item.get("value")
            else:
                value = item
            if value:
                values.append(str(value))
        result = sorted(set(values))
        return self._set_cached(cache_key, result or ["any"])

    def buy_activation(self, *, service_code: str, country_code: str, operator: str, max_price: str | None) -> dict[str, Any]:
        payload = self.request(
            "getNumberV2",
            service=service_code,
            country=country_code,
            operator=operator or "any",
            maxPrice=max_price or "",
        )
        return self._parse_purchase_payload(payload, service_code, country_code, operator)

    def buy_activation_fixed_price(
        self,
        *,
        service_code: str,
        country_code: str,
        operator: str,
        exact_price: str,
    ) -> dict[str, Any]:
        payload = self.request(
            "getNumber",
            service=service_code,
            country=country_code,
            operator=operator or "any",
            maxPrice=exact_price,
            fixedPrice="true",
        )
        return self._parse_purchase_payload(payload, service_code, country_code, operator)

    def get_status(self, activation_id: str) -> dict[str, Any]:
        payload = self.request("getStatus", id=activation_id)
        return self._parse_status_payload(payload)

    def set_status(self, activation_id: str, status: int) -> dict[str, Any]:
        payload = self.request("setStatus", id=activation_id, status=status)
        return {"raw": payload, "result": str(payload)}

    def get_active_activations(self) -> list[dict[str, Any]]:
        payload = self.request("getActiveActivations")
        if isinstance(payload, dict):
            active = payload.get("activeActivations")
            if isinstance(active, dict) and isinstance(active.get("rows"), list):
                return active["rows"]
            if isinstance(payload.get("data"), list):
                return payload["data"]
        return []

    @staticmethod
    def _normalize_services(payload: Any) -> list[dict[str, str]]:
        if isinstance(payload, dict) and isinstance(payload.get("services"), list):
            items = payload.get("services", [])
        elif isinstance(payload, dict):
            items = [{"code": key, **value} if isinstance(value, dict) else {"code": key, "name": value} for key, value in payload.items()]
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        result = []
        for item in items:
            code = str(item.get("code") or item.get("id") or item.get("value") or item.get("shortName") or "")
            name = str(item.get("name") or item.get("title") or item.get("text") or item.get("service") or "").strip()
            if code and name:
                result.append({"code": code, "name": name})
        return sorted(result, key=lambda item: item["name"])

    @staticmethod
    def _normalize_countries(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict) and isinstance(payload.get("countries"), list):
            items = payload.get("countries", [])
        elif isinstance(payload, dict):
            items = [{"code": key, **value} if isinstance(value, dict) else {"code": key, "name": value} for key, value in payload.items()]
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        result = []
        for item in items:
            code = str(item.get("code") or item.get("id") or item.get("value") or "")
            name = str(item.get("eng") or item.get("name") or item.get("text") or "").strip()
            local_name = str(
                item.get("chn")
                or item.get("cn")
                or item.get("chinese")
                or item.get("name_cn")
                or item.get("rus")
                or item.get("localName")
                or item.get("name")
                or ""
            ).strip()
            search_terms = []
            seen_terms: set[str] = set()
            for value in [code, *collect_string_values(item)]:
                normalized = normalize_text(value)
                if normalized and normalized not in seen_terms:
                    seen_terms.add(normalized)
                    search_terms.append(str(value).strip())
            if code and (name or local_name):
                result.append(
                    {
                        "code": code,
                        "name": name,
                        "localName": local_name,
                        "searchTerms": search_terms,
                        "retry": bool(item.get("retry")),
                        "rent": bool(item.get("rent")),
                        "multiService": bool(item.get("multiService")),
                    }
                )
        return sorted(result, key=lambda item: item["name"] or item["localName"])

    @staticmethod
    def _pick_by_name(items: list[dict[str, Any]], preferred: str, aliases: list[str], fields: tuple[str, ...]) -> dict[str, Any] | None:
        targets = [normalize_text(value) for value in [preferred, *aliases] if value]
        for target in targets:
            for item in items:
                search_terms = [item.get(field) for field in fields]
                if isinstance(item.get("searchTerms"), list):
                    search_terms.extend(item.get("searchTerms"))
                if any(normalize_text(term) == target for term in search_terms):
                    return item
        for target in targets:
            for item in items:
                search_terms = [item.get(field) for field in fields]
                if isinstance(item.get("searchTerms"), list):
                    search_terms.extend(item.get("searchTerms"))
                if any(
                    target in normalize_text(term) or normalize_text(term) in target
                    for term in search_terms
                    if term
                ):
                    return item
        return items[0] if items else None

    @staticmethod
    def _extract_price_info(payload: Any, country_code: str, service_code: str) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        country_entry = payload.get(country_code) or payload.get(str(int(country_code))) if str(country_code).isdigit() else payload.get(country_code)
        country_entry = country_entry or payload.get("country") or payload.get("countries", {}).get(country_code) or payload
        service_entry = None
        if isinstance(country_entry, dict):
            service_entry = country_entry.get(service_code) or country_entry.get("services", {}).get(service_code) or country_entry.get("services", {}).get("full")
        service_entry = service_entry or payload.get("services", {}).get(service_code) if isinstance(payload.get("services"), dict) else service_entry
        service_entry = service_entry or payload
        if not isinstance(service_entry, dict):
            return None
        price = service_entry.get("cost") or service_entry.get("price") or service_entry.get("activationCost")
        count = service_entry.get("count") or service_entry.get("quant") or service_entry.get("qty") or service_entry.get("available")
        if price is None and count is None:
            return None
        return {
            "price": float(price) if price is not None else None,
            "count": int(count) if count is not None else None,
            "raw": payload,
        }

    @staticmethod
    def _parse_purchase_payload(payload: Any, service_code: str, country_code: str, operator: str) -> dict[str, Any]:
        if isinstance(payload, dict):
            return {
                "id": str(payload.get("activationId") or payload.get("id") or payload.get("activationID") or ""),
                "phoneNumber": str(payload.get("phoneNumber") or payload.get("phone") or payload.get("number") or ""),
                "activationCost": payload.get("activationCost") or payload.get("cost"),
                "countryCode": str(payload.get("countryCode") or country_code),
                "serviceCode": str(payload.get("activationService") or service_code),
                "operator": str(payload.get("activationOperator") or payload.get("operator") or operator or "any"),
                "canGetAnotherSms": bool(payload.get("canGetAnotherSms")),
                "raw": payload,
            }
        text = str(payload).strip()
        if text.startswith("ACCESS_NUMBER"):
            _, activation_id, phone_number = text.split(":", 2)
            return {
                "id": activation_id,
                "phoneNumber": phone_number,
                "activationCost": None,
                "countryCode": country_code,
                "serviceCode": service_code,
                "operator": operator or "any",
                "canGetAnotherSms": False,
                "raw": text,
            }
        raise HeroSmsError(f"无法解析购号响应: {text}")

    @staticmethod
    def _parse_status_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            upstream_status = payload.get("status") or payload.get("code") or payload.get("state") or "UNKNOWN"
            sms_code = payload.get("smsCode") or payload.get("codeValue") or payload.get("sms")
            return {
                "raw": payload,
                "upstreamStatus": upstream_status,
                "localStatus": NORMALIZED_STATES.get(upstream_status, "unknown"),
                "label": STATUS_LABELS.get(upstream_status, upstream_status),
                "code": str(sms_code) if sms_code else None,
            }
        text = str(payload).strip()
        parts = text.split(":", 1)
        upstream_status = parts[0] if parts else "UNKNOWN"
        sms_code = parts[1] if len(parts) > 1 else None
        return {
            "raw": text,
            "upstreamStatus": upstream_status,
            "localStatus": NORMALIZED_STATES.get(upstream_status, "unknown"),
            "label": STATUS_LABELS.get(upstream_status, upstream_status),
            "code": sms_code,
        }

class TeleAutoClient:
    def __init__(self, enabled: bool, base_url: str, username: str, password: str, timeout_ms: int) -> None:
        self.enabled = enabled
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout_seconds = timeout_ms / 1000

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.username and self.password)

    def _auth_headers(self) -> dict[str, str]:
        if not self.username or not self.password:
            raise TeleAutoError("未配置 TELE_AUTO_USERNAME / TELE_AUTO_PASSWORD")
        token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {token}"}

    def _build_url(self, path: str, query: dict[str, Any] | None = None) -> str:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            if not self.base_url:
                raise TeleAutoError("未配置 TELE_AUTO_API_URL")
            url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        query_string = urlencode({key: value for key, value in (query or {}).items() if value not in (None, "")})
        if query_string:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query_string}"
        return url

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> Any:
        if not self.enabled:
            raise TeleAutoError("Tele Auto 已禁用")
        headers = {
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            "User-Agent": "help-oai-tele-auto-client/1.0",
        }
        if auth:
            headers.update(self._auth_headers())
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self._build_url(path, query), data=data, method=method, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8", errors="replace").strip()
        except HTTPError as error:
            body_text = error.read().decode("utf-8", errors="replace").strip()
            raise TeleAutoError(f"Tele Auto 请求失败: HTTP {error.code} {body_text}".strip())
        except URLError as error:
            raise TeleAutoError(f"Tele Auto 连接失败: {error.reason}")
        if not text:
            return {}
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
        return text

    @staticmethod
    def extract_public_key(public_url: Any) -> str:
        text = str(public_url or "").strip()
        if not text:
            return ""
        parsed = urlparse(text)
        key = (parse_qs(parsed.query).get("key") or [""])[0]
        if key:
            return key
        match = re.search(r"(?:\?|&)key=([^&\s]+)", text)
        return unquote(match.group(1)) if match else ""

    def _localized_public_api_url(self, public_url: str) -> str:
        parsed = urlparse(public_url)
        if parsed.path.startswith("/api/") and self.base_url:
            return f"{self.base_url}{parsed.path}{'?' + parsed.query if parsed.query else ''}"
        return public_url

    def issue_account(self) -> dict[str, Any]:
        if not self.configured:
            raise TeleAutoError("Tele Auto 未配置完整")
        payload = self._request("POST", "/api/auto/account", auth=True)
        if not isinstance(payload, dict):
            raise TeleAutoError("Tele Auto 出号返回格式异常")
        if str(payload.get("code", "0")) not in {"0", ""}:
            raise TeleAutoError(str(payload.get("msg") or payload.get("error") or "Tele Auto 出号失败"))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        phone = str(data.get("phone") or data.get("phoneNumber") or "").strip()
        public_url = str(data.get("url") or data.get("publicUrl") or data.get("smsUrl") or "").strip()
        if not phone or not public_url:
            raise TeleAutoError("Tele Auto 出号缺少 phone/url")
        public_key = self.extract_public_key(public_url)
        id_source = public_key or f"{phone}|{public_url}"
        activation_id = f"tele:{hashlib.sha256(id_source.encode('utf-8')).hexdigest()[:24]}"
        return {
            "id": activation_id,
            "telePublicKey": public_key,
            "phoneNumber": phone,
            "publicUrl": public_url,
            "smsUrl": self._localized_public_api_url(public_url),
            "expiresAt": data.get("expires_at") or data.get("expiresAt") or "",
            "rawExpiresAt": data.get("raw_expires_at") or data.get("rawExpiresAt") or "",
            "teleSuccessCount": int(data.get("success_count") or data.get("successCount") or 0),
            "teleLastUsedAt": data.get("last_used_at") or data.get("lastUsedAt") or "",
            "teleMaxSuccessCount": int(data.get("max_success_count") or data.get("maxSuccessCount") or 3),
            "teleReuseAfterSeconds": int(data.get("reuse_after_seconds") or data.get("reuseAfterSeconds") or 0),
            "line": data.get("line") or "",
            "raw": {
                "code": payload.get("code"),
                "msg": payload.get("msg"),
                "data": data,
            },
        }

    def account_details(self, record_or_value: dict[str, Any] | str) -> dict[str, Any]:
        """Refresh Tele-side usage/expiry metadata for a previously issued account."""
        if not self.configured:
            raise TeleAutoError("Tele Auto 未配置完整")
        if isinstance(record_or_value, dict):
            key = str(record_or_value.get("telePublicKey") or "").strip()
            if not key:
                key = self.extract_public_key(record_or_value.get("publicUrl") or record_or_value.get("smsUrl") or "")
        else:
            raw = str(record_or_value or "").strip()
            key = self.extract_public_key(raw) or raw
        if not key:
            raise TeleAutoError("Tele Auto 详情缺少 key/url")
        payload = self._request("GET", "/api/auto/account/details", query={"key": key}, auth=True)
        if not isinstance(payload, dict):
            raise TeleAutoError("Tele Auto 详情返回格式异常")
        if str(payload.get("code", "0")) not in {"0", ""}:
            raise TeleAutoError(str(payload.get("msg") or payload.get("error") or "Tele Auto 查询详情失败"))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        phone = str(data.get("phone") or data.get("phoneNumber") or "").strip()
        public_url = str(data.get("url") or data.get("publicUrl") or "").strip()
        return {
            "phoneNumber": phone,
            "publicUrl": public_url,
            "smsUrl": self._localized_public_api_url(public_url) if public_url else "",
            "expiresAt": data.get("expires_at") or data.get("expiresAt") or "",
            "rawExpiresAt": data.get("raw_expires_at") or data.get("rawExpiresAt") or "",
            "teleSuccessCount": int(data.get("success_count") or data.get("successCount") or 0),
            "teleLastUsedAt": data.get("last_used_at") or data.get("lastUsedAt") or "",
            "teleMaxSuccessCount": int(data.get("max_success_count") or data.get("maxSuccessCount") or 3),
            "teleReuseAfterSeconds": int(data.get("reuse_after_seconds") or data.get("reuseAfterSeconds") or 0),
            "status": str(data.get("status") or "").strip(),
            "line": data.get("line") or "",
            "telePublicKey": key,
            "rawDetails": {"code": payload.get("code"), "msg": payload.get("msg"), "data": data},
        }

    @staticmethod
    def _extract_sms_code(payload: Any) -> str:
        texts: list[str] = []
        if isinstance(payload, dict):
            for key in ("sms", "smsCode", "codeValue", "message", "msg", "text", "data", "raw"):
                value = payload.get(key)
                if isinstance(value, (dict, list)):
                    texts.extend(collect_string_values(value))
                elif value not in (None, ""):
                    texts.append(str(value))
        else:
            texts.append(str(payload or ""))
        for text in texts:
            if text.lower().startswith("no|"):
                continue
            match = re.search(r"(?<!\d)(\d{4,8})(?!\d)", text)
            if match:
                return match.group(1)
        return ""

    def get_status(self, record: dict[str, Any]) -> dict[str, Any]:
        sms_url = str(record.get("smsUrl") or record.get("publicUrl") or "").strip()
        if not sms_url:
            raise TeleAutoError("Tele Auto 记录缺少接码 URL")
        payload = self._request("GET", sms_url, auth=False)
        code = self._extract_sms_code(payload)
        if code:
            return {
                "raw": payload,
                "upstreamStatus": "STATUS_OK",
                "localStatus": "code_received",
                "label": "收到验证码",
                "code": code,
            }
        return {
            "raw": payload,
            "upstreamStatus": "STATUS_WAIT_CODE",
            "localStatus": "waiting_for_code",
            "label": "等待验证码",
            "code": None,
        }

    def fail_account(self, record_or_value: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(record_or_value, dict):
            value = (
                record_or_value.get("publicUrl")
                or record_or_value.get("smsUrl")
                or record_or_value.get("line")
                or record_or_value.get("telePublicKey")
                or ""
            )
        else:
            value = record_or_value
        value = str(value or "").strip()
        if not value:
            raise TeleAutoError("Tele Auto 失败反馈缺少 key/url/line")
        payload = self._request("POST", "/api/auto/account/fail", body={"url": value}, auth=True)
        return payload if isinstance(payload, dict) else {"raw": payload}

    def release_account(self, record_or_value: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(record_or_value, dict):
            value = (
                record_or_value.get("publicUrl")
                or record_or_value.get("smsUrl")
                or record_or_value.get("line")
                or record_or_value.get("telePublicKey")
                or ""
            )
        else:
            value = record_or_value
        value = str(value or "").strip()
        if not value:
            raise TeleAutoError("Tele Auto 释放缺少 key/url/line")
        payload = self._request("POST", "/api/auto/account/release", body={"url": value}, auth=True)
        return payload if isinstance(payload, dict) else {"raw": payload}

    def sold_account(self, record_or_value: dict[str, Any] | str, reason: str = "") -> dict[str, Any]:
        if isinstance(record_or_value, dict):
            value = (
                record_or_value.get("publicUrl")
                or record_or_value.get("smsUrl")
                or record_or_value.get("line")
                or record_or_value.get("telePublicKey")
                or ""
            )
        else:
            value = record_or_value
        value = str(value or "").strip()
        if not value:
            raise TeleAutoError("Tele Auto 已售反馈缺少 key/url/line")
        payload = self._request(
            "POST",
            "/api/auto/account/sold",
            body={"url": value, "reason": str(reason or "").strip()},
            auth=True,
        )
        return payload if isinstance(payload, dict) else {"raw": payload}
