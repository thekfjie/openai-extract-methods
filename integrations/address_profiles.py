"""Passthrough bridge to the public meiguodizhi / americaaddress endpoints.

Upstream fields are forwarded as-is (including any payment-card or government-ID
keys the third-party site returns). This module does not filter or drop fields.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MEIGUO_ADDRESS_ENDPOINT = "https://www.meiguodizhi.com/api/v1/dz"
MEIGUO_SOURCE_URL = "https://www.meiguodizhi.com/"
AMERICA_ADDRESS_BASE = "https://cn.americaaddress.com"
US_ADDRESS_GEN_BASE = "https://usaddressgen.com"
US_ADDRESS_GEN_POOL_ENDPOINT = f"{US_ADDRESS_GEN_BASE}/api/address-pool"
US_TAX_FREE_STATES = {
    "AK": "Alaska",
    "DE": "Delaware",
    "MT": "Montana",
    "NH": "New Hampshire",
    "OR": "Oregon",
}
US_PROFILE_NAMES = (
    ("James", "Wilson", "male"), ("Michael", "Brown", "male"),
    ("Daniel", "Miller", "male"), ("David", "Anderson", "male"),
    ("Emma", "Davis", "female"), ("Olivia", "Taylor", "female"),
    ("Charlotte", "Moore", "female"), ("Amelia", "Johnson", "female"),
)
US_STATE_AREA_CODES = {
    "AK": ("907",), "DE": ("302",), "MT": ("406",),
    "NH": ("603",), "OR": ("503", "541", "971"),
}
MAX_CITY_LENGTH = 80
MAX_RESPONSE_BYTES = 128 * 1024
MAX_ADDRESS_POOL_BYTES = 1024 * 1024
UPSTREAM_TIMEOUT_SECONDS = 12
MIN_REQUEST_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class AddressCountry:
    code: str
    label: str
    path: str
    provider: str = "meiguodizhi.com"


ADDRESS_COUNTRIES: tuple[AddressCountry, ...] = (
    AddressCountry("US", "美国", "/"),
    AddressCountry("BR", "巴西", "/brazil-address/", "cn.americaaddress.com"),
    AddressCountry("CA", "加拿大", "/ca-address"),
    AddressCountry("AU", "澳大利亚", "/au-address"),
    AddressCountry("JP", "日本", "/jp-address"),
    AddressCountry("TW", "台湾", "/tw-address"),
    AddressCountry("KR", "韩国", "/kr-address"),
    AddressCountry("HK", "香港", "/hk-address"),
    AddressCountry("GB", "英国", "/uk-address"),
    AddressCountry("DE", "德国", "/de-address"),
    AddressCountry("SG", "新加坡", "/sg-address"),
    AddressCountry("FR", "法国", "/fr-address"),
    AddressCountry("IT", "意大利", "/it-address"),
    AddressCountry("ES", "西班牙", "/es-address"),
    AddressCountry("NL", "荷兰", "/nl-address"),
    AddressCountry("MY", "马来西亚", "/my-address"),
    AddressCountry("RU", "俄罗斯", "/ru-address"),
    AddressCountry("CN", "中国", "/cn-address"),
    AddressCountry("TH", "泰国", "/th-address"),
    AddressCountry("PH", "菲律宾", "/ph-address"),
    AddressCountry("AR", "阿根廷", "/ar-address"),
    AddressCountry("TR", "土耳其", "/tr-address"),
    AddressCountry("VN", "越南", "/vn-address"),
)
_COUNTRY_BY_CODE = {item.code: item for item in ADDRESS_COUNTRIES}



class AddressProfileError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = int(status_code)


_RATE_LOCK = threading.Lock()
_LAST_REQUEST_BY_CLIENT: dict[str, float] = {}


def address_country_catalog() -> list[dict[str, str]]:
    return [
        {"code": item.code, "label": item.label, "path": item.path, "provider": item.provider}
        for item in ADDRESS_COUNTRIES
    ]


def _clean_text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_city(value: Any) -> str:
    city = _clean_text(value)
    if len(city) > MAX_CITY_LENGTH:
        raise AddressProfileError(f"城市名称不能超过 {MAX_CITY_LENGTH} 个字符", 400)
    if any(ord(char) < 32 for char in city):
        raise AddressProfileError("城市名称包含不可用字符", 400)
    return city


def _resolve_country(value: Any, *, require_city_filter: bool = False) -> tuple[AddressCountry, bool]:
    code = _clean_text(value).upper()
    if not code or code == "RANDOM":
        choices = [item for item in ADDRESS_COUNTRIES if not require_city_filter or item.provider == "meiguodizhi.com"]
        return secrets.choice(choices), True
    country = _COUNTRY_BY_CODE.get(code)
    if country is None:
        raise AddressProfileError("不支持的国家代码", 400)
    return country, False


def _enforce_rate_limit(client_key: str) -> None:
    key = _clean_text(client_key) or "unknown"
    now = time.monotonic()
    with _RATE_LOCK:
        previous = _LAST_REQUEST_BY_CLIENT.get(key, 0.0)
        if now - previous < MIN_REQUEST_INTERVAL_SECONDS:
            retry = max(1, int(MIN_REQUEST_INTERVAL_SECONDS - (now - previous) + 0.999))
            raise AddressProfileError(f"请求过快，请 {retry} 秒后再试", 429)
        _LAST_REQUEST_BY_CLIENT[key] = now
        # Keep this process-local map bounded when a reverse proxy supplies many
        # one-off forwarded addresses.
        if len(_LAST_REQUEST_BY_CLIENT) > 2048:
            cutoff = now - 300
            for old_key, timestamp in list(_LAST_REQUEST_BY_CLIENT.items()):
                if timestamp < cutoff:
                    _LAST_REQUEST_BY_CLIENT.pop(old_key, None)


def _read_limited(response: Any) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(16 * 1024, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise AddressProfileError("地址站点响应过大", 502)
    return b"".join(chunks)


def _read_pool_limited(response: Any) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, MAX_ADDRESS_POOL_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_ADDRESS_POOL_BYTES:
            raise AddressProfileError("US 地址池响应过大", 502)
    return b"".join(chunks)


def _weighted_choice(items: Any) -> dict[str, Any]:
    candidates = [item for item in (items or []) if isinstance(item, dict)]
    if not candidates:
        raise AddressProfileError("US 地址池没有可用数据", 502)
    weights = [max(1, int(item.get("weight") or 1)) for item in candidates]
    cursor = secrets.randbelow(sum(weights))
    for item, weight in zip(candidates, weights):
        if cursor < weight:
            return item
        cursor -= weight
    return candidates[-1]


def fetch_us_tax_free_address(state: Any = "", *, client_key: str = "") -> dict[str, Any]:
    """Read one address from usaddressgen.com's public tax-free-state pool."""
    state_code = _clean_text(state).upper()
    if state_code and state_code not in US_TAX_FREE_STATES:
        raise AddressProfileError("州代码必须是 AK、DE、MT、NH 或 OR", 400)
    if not state_code:
        state_code = secrets.choice(tuple(US_TAX_FREE_STATES))
    _enforce_rate_limit(f"tax-free:{client_key}")
    url = f"{US_ADDRESS_GEN_POOL_ENDPOINT}?country=US&region={state_code}"
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Origin": US_ADDRESS_GEN_BASE,
            "Referer": f"{US_ADDRESS_GEN_BASE}/tax-free-address/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0 Safari/537.36",
        },
    )
    try:
        with urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = _read_pool_limited(response)
    except HTTPError as error:
        raise AddressProfileError(f"US 地址站点返回 HTTP {error.code}", 502) from error
    except (URLError, TimeoutError, OSError) as error:
        raise AddressProfileError("US 地址站点暂时无法访问", 502) from error
    if status < 200 or status >= 300:
        raise AddressProfileError(f"US 地址站点返回 HTTP {status}", 502)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AddressProfileError("US 地址站点返回了无效 JSON", 502) from error
    if not isinstance(payload, dict) or payload.get("country") != "US" or payload.get("region") != state_code:
        raise AddressProfileError("US 地址池返回地区不匹配", 502)
    city = _weighted_choice(payload.get("cities"))
    street = _weighted_choice(city.get("streets"))
    postcode = _weighted_choice(street.get("postcodes"))
    numbers = street.get("houseNumbers") if isinstance(street.get("houseNumbers"), dict) else {}
    number = _weighted_choice([*(numbers.get("numeric") or []), *(numbers.get("numericAlpha") or [])])
    line1 = f"{number.get('value')} {street.get('name')}"
    city_name = _clean_text(city.get("name"))
    postal_code = _clean_text(postcode.get("value"))
    if not line1.strip() or not city_name or not postal_code:
        raise AddressProfileError("US 地址池资料不完整", 502)
    first_name, last_name, gender = secrets.choice(US_PROFILE_NAMES)
    area_code = secrets.choice(US_STATE_AREA_CODES[state_code])
    subscriber = f"{secrets.randbelow(900) + 100:03d}{secrets.randbelow(10000):04d}"
    email_suffix = secrets.randbelow(9000) + 1000
    profile = {
        "name": f"{first_name} {last_name}",
        "firstName": first_name,
        "lastName": last_name,
        "gender": gender,
        "email": f"{first_name.lower()}.{last_name.lower()}{email_suffix}@outlook.com",
        "phone": f"+1{area_code}{subscriber}",
    }
    return {
        "schema": "automyai.us-tax-free-address.v1",
        "source": {
            "provider": "usaddressgen.com",
            "url": f"{US_ADDRESS_GEN_BASE}/tax-free-address/",
            "endpoint": "/api/address-pool",
            "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "generatedAt": _clean_text(payload.get("generatedAt")),
        },
        "address": {
            "line1": line1,
            "city": city_name,
            "state": state_code,
            "stateName": US_TAX_FREE_STATES[state_code],
            "postalCode": postal_code,
            "country": "US",
            "formatted": f"{line1}, {city_name}, {state_code} {postal_code}, US",
        },
        # The upstream pool contains geographic records.  Its public page adds
        # name, phone and email in the browser; expose the same complete profile
        # from our local API so every consumer receives one coherent record.
        "profile": profile,
    }


def _fetch_meiguodizhi(country: AddressCountry, city: str) -> dict[str, Any]:
    payload = {"city": city, "path": country.path, "method": "refresh" if city else "address"}
    request = Request(
        MEIGUO_ADDRESS_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "AutoMyAI-address-fixture/1.0",
        },
    )
    try:
        with urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = _read_limited(response)
    except HTTPError as error:
        raise AddressProfileError(f"地址站点返回 HTTP {error.code}", 502) from error
    except (URLError, TimeoutError, OSError) as error:
        raise AddressProfileError("地址站点暂时无法访问", 502) from error
    if status < 200 or status >= 300:
        raise AddressProfileError(f"地址站点返回 HTTP {status}", 502)
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AddressProfileError("地址站点返回了无效 JSON", 502) from error
    if not isinstance(result, dict) or result.get("status") != "ok" or not isinstance(result.get("address"), dict):
        raise AddressProfileError("地址站点没有返回可用资料", 502)
    return result["address"]


def _strip_html(value: str) -> str:
    return _clean_text(unescape(re.sub(r"<[^>]+>", " ", value)))


def _fetch_america_address(country: AddressCountry, city: str) -> dict[str, Any]:
    if city:
        raise AddressProfileError("巴西资料源不支持城市筛选，请留空随机获取", 400)
    url = f"{AMERICA_ADDRESS_BASE}{country.path}"
    request = Request(
        url,
        method="GET",
        headers={"Accept": "text/html", "User-Agent": "AutoMyAI-address-fixture/1.0"},
    )
    try:
        with urlopen(request, timeout=UPSTREAM_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = _read_limited(response)
    except HTTPError as error:
        raise AddressProfileError(f"巴西地址站点返回 HTTP {error.code}", 502) from error
    except (URLError, TimeoutError, OSError) as error:
        raise AddressProfileError("巴西地址站点暂时无法访问", 502) from error
    if status < 200 or status >= 300:
        raise AddressProfileError(f"巴西地址站点返回 HTTP {status}", 502)
    html = raw.decode("utf-8", errors="replace")
    box_match = re.search(r'<div[^>]+id=["\']address-box["\'][^>]*>(.*?)<div class="panel-footer', html, flags=re.I | re.S)
    content = box_match.group(1) if box_match else html
    pairs = re.findall(
        r"<dd[^>]*>\s*<label[^>]*>(.*?)</label>\s*<span[^>]*>\s*<b[^>]*>(.*?)</b>",
        content,
        flags=re.I | re.S,
    )
    label_map = {
        "全名": "Full_Name", "性别": "Gender", "Title": "Title", "生日": "Birthday",
        "街道": "Address", "城市": "City", "省(州)全称": "State_Full", "邮编": "Zip_Code",
        "电话号码": "Telephone", "就业情况": "Employment_Status", "月薪": "Monthly_Salary",
        "职称": "Occupation", "公司名称": "Company_Name", "公司规模": "Company_Size",
        "行业": "Industry", "身高": "Height", "体重": "Weight", "用户名": "Username",
        "密码": "Password", "安全问题": "Security_Question", "安全答案": "Security_Answer",
        "浏览器User Agent": "Browser_User_Agent", "操作系统": "System",
    }
    result: dict[str, Any] = {}
    for raw_label, raw_value in pairs:
        label = _strip_html(raw_label)
        key = label_map.get(label)
        value = _strip_html(raw_value)
        if key and value:
            result[key] = value
    if result.get("State_Full"):
        result["State"] = result["State_Full"]
    if not result.get("Address") or not result.get("City"):
        raise AddressProfileError("巴西地址站点页面结构已变化", 502)
    return result


def _fetch_upstream(country: AddressCountry, city: str) -> dict[str, Any]:
    if country.provider == "cn.americaaddress.com":
        return _fetch_america_address(country, city)
    return _fetch_meiguodizhi(country, city)


def _normalize_fields(source: dict[str, Any]) -> dict[str, str]:
    """Forward every upstream field as-is after light text cleanup."""
    fields: dict[str, str] = {}
    for key, raw in source.items():
        name = _clean_text(key)
        if not name:
            continue
        value = _clean_text(raw)
        if value:
            fields[name] = value
    return fields


def fetch_address_profile(country: Any = "RANDOM", city: Any = "", *, client_key: str = "") -> dict[str, Any]:
    """Fetch one profile and forward the third-party fields without filtering."""
    city_value = _normalize_city(city)
    country_item, was_random = _resolve_country(country, require_city_filter=bool(city_value))
    _enforce_rate_limit(client_key)
    source = _fetch_upstream(country_item, city_value)
    fields = _normalize_fields(source)
    required = ("City", "Zip_Code", "Address", "Trans_Address")
    if not any(fields.get(key) for key in required):
        raise AddressProfileError("地址站点返回资料缺少地址字段", 502)
    return {
        "schema": "automyai.remote-address-profile.v1",
        "source": {
            "provider": country_item.provider,
            "url": AMERICA_ADDRESS_BASE if country_item.provider == "cn.americaaddress.com" else MEIGUO_SOURCE_URL,
            "endpoint": country_item.path if country_item.provider == "cn.americaaddress.com" else "/api/v1/dz",
            "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "country": {
            "code": country_item.code,
            "label": country_item.label,
            "path": country_item.path,
            "random": was_random,
        },
        "query": {"city": city_value, "randomCountry": was_random},
        "fields": fields,
    }
