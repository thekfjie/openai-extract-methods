import json
from http.cookiejar import Cookie

import httpx
from loguru import logger
from typing import Any, Optional
from paypal.models import SessionState
from config import USER_AGENT


def _accept_language(locale: str, lang: str) -> str:
    locale = (locale or "en_US").replace("_", "-")
    lang = lang or locale.split("-")[0]
    if locale.lower().startswith("pt"):
        return "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
    if locale.lower().startswith("en-gb") or locale.lower().endswith("-gb"):
        return "en-GB,en;q=0.9,en-US;q=0.8"
    return f"{locale},{lang};q=0.9,en-US;q=0.8,en;q=0.7"


def build_common_headers(locale: str = "pt_BR", lang: str = "pt") -> dict:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": _accept_language(locale, lang),
        "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-device-memory": "32",
    }


def _mask_middle(value: str, left: int = 6, right: int = 4) -> str:
    if len(value) <= left + right:
        return "<redacted>"
    return f"{value[:left]}...{value[-right:]}"


def _mask_email(value: str) -> str:
    if "@" not in value:
        return "<redacted>"
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[:2]}***{local[-1:]}@{domain}"


def _mask_digits(value: str, keep: int = 4) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= keep:
        return "<redacted>"
    return f"{'*' * (len(digits) - keep)}{digits[-keep:]}"


def sanitize_for_log(value: Any, key: str = "") -> Any:
    """Remove secrets and high-risk PII before writing diagnostics."""
    if isinstance(value, dict):
        return {k: sanitize_for_log(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_log(item, key) for item in value]
    if not isinstance(value, str):
        return value

    lowered_key = key.lower()
    compact_key = lowered_key.replace("_", "").replace("-", "")

    if compact_key in {"password", "securitycode", "cvv", "pin"}:
        return "<redacted>"
    if "authorization" in compact_key or "cookie" in compact_key:
        return "<redacted>"
    if "accesstoken" in compact_key or "euat" in compact_key:
        return "<redacted>"
    if compact_key in {"token", "batoken", "ectoken", "billingagreementid"}:
        return _mask_middle(value)
    if compact_key in {"cardnumber", "encryptednumber"}:
        return _mask_digits(value)
    if compact_key in {"cpf", "identitydocument", "document", "value"}:
        return "<redacted>"
    if compact_key == "email":
        return _mask_email(value)
    if compact_key in {"phonenumber", "phone", "number"} and sum(ch.isdigit() for ch in value) >= 8:
        return _mask_digits(value)

    return value


def _paypal_debug_id(headers: httpx.Headers) -> str:
    for name in ("paypal-debug-id", "Paypal-Debug-Id", "PayPal-Debug-Id"):
        value = headers.get(name)
        if value:
            return value
    return ""


class PayPalSession:
    """Manages HTTP session with cookie persistence and logging."""

    def __init__(
        self,
        state: SessionState,
        proxy_url: str | None = None,
        proxy_label: str = "",
    ):
        self.state = state
        self.proxy_url = proxy_url
        self.proxy_label = proxy_label or ("proxy-on" if proxy_url else "proxy-off")
        client_kwargs = {
            "follow_redirects": False,
            "timeout": httpx.Timeout(30.0),
            "headers": build_common_headers(state.locale, state.lang),
            # Ensure proxy-off is not overridden by HTTP_PROXY/HTTPS_PROXY env vars.
            "trust_env": False,
        }
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        self.client = httpx.Client(**client_kwargs)
        logger.info("HTTP outbound proxy: {}", self.proxy_label)
        logger.info("Session locale/country: {} / {}", state.locale, state.country)

    def close(self):
        self.client.close()

    def _sync_state_cookies(self):
        """Pull important cookies into SessionState after each request."""
        jar = self.client.cookies
        cookie_dict = {}
        # PayPal may set the same cookie name for multiple domain/path scopes
        # (ddgl is a common example). httpx.Cookies.items() raises
        # CookieConflict in that case, so iterate the underlying jar instead.
        for cookie in jar.jar:
            if isinstance(cookie, Cookie):
                cookie_dict[cookie.name] = cookie.value
        self.state.update_from_cookies(cookie_dict)

    def get(self, url: str, **kwargs) -> httpx.Response:
        logger.debug(f"GET {url}")
        resp = self.client.get(url, **kwargs)
        self._sync_state_cookies()
        logger.debug(f"  -> {resp.status_code} ({len(resp.content)} bytes)")
        return resp

    def post(self, url: str, **kwargs) -> httpx.Response:
        logger.debug(f"POST {url}")
        resp = self.client.post(url, **kwargs)
        self._sync_state_cookies()
        logger.debug(f"  -> {resp.status_code} ({len(resp.content)} bytes)")
        return resp

    def graphql(self, operation_name: str, query: str, variables: dict,
                extra_headers: Optional[dict] = None,
                extra_body: Optional[dict] = None,
                batched: bool = False,
                endpoint: Optional[str] = None) -> dict:
        """Send a GraphQL request to PayPal's graphql endpoint."""
        url = endpoint or "https://www.paypal.com/graphql"
        if operation_name and endpoint is None:
            url = f"{url}?{operation_name}"

        context_token = str(
            variables.get("token")
            or variables.get("billingAgreementId")
            or self.state.ec_token
            or self.state.ba_token
        )
        # UK authorize uses a separate CMID; keep EC token as client-context for
        # Weasley ops, while authorize can override via extra_headers.
        metadata_id = (
            self.state.paypal_client_metadata_id
            if operation_name == "authorize"
            else context_token
        )
        referer = (
            self.state.signup_url
            if self.state.ec_token
            else f"https://www.paypal.com/pay?token={self.state.ba_token}&ul=1"
        )
        app_name = "checkoutuinodeweb" if operation_name == "authorize" else "checkoutuinodeweb_weasley"
        headers = {
            "Content-Type": "application/json",
            "X-App-Name": app_name,
            "X-Requested-With": "fetch",
            "PayPal-Client-Context": context_token,
            "PayPal-Client-Metadata-Id": metadata_id,
            "X-Country": self.state.country or "BR",
            "X-Locale": self.state.locale or "pt_BR",
            "Origin": "https://www.paypal.com",
            "Referer": referer,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }
        if self.state.euat_token:
            headers["X-PayPal-Internal-EUAT"] = self.state.euat_token
        if extra_headers:
            # Passing None removes a default header. This is needed for the
            # browser-captured final Hagrid authorize call, which posts to
            # /graphql/ without PayPal-Client-Context/X-Country/X-Locale.
            for key, value in extra_headers.items():
                if value is None:
                    headers.pop(key, None)
                else:
                    headers[key] = value

        payload_item = {
            "operationName": operation_name,
            "variables": variables,
            "query": query,
        }
        if extra_body:
            # checkoutweb/weasley injects fn_sync_data at the top level of the
            # GraphQL JSON body for SignUpNewMemberMutation.
            payload_item.update(extra_body)

        payload = [payload_item] if batched else payload_item

        resp = self.post(url, json=payload, headers=headers)
        debug_id = _paypal_debug_id(resp.headers)
        logger.info(
            "GraphQL {} HTTP {} bytes={} paypal_debug_id={}",
            operation_name,
            resp.status_code,
            len(resp.content),
            debug_id or "<missing>",
        )

        try:
            result = resp.json()
        except ValueError:
            logger.error(
                "GraphQL {} returned non-JSON response: status={} paypal_debug_id={} body={}",
                operation_name,
                resp.status_code,
                debug_id or "<missing>",
                resp.text[:2000],
            )
            raise

        result_items = result if isinstance(result, list) else [result]
        for item in result_items:
            if not isinstance(item, dict) or not item.get("errors"):
                continue

            logger.error(
                "GraphQL {} returned errors: status={} paypal_debug_id={} errors={}",
                operation_name,
                resp.status_code,
                debug_id or "<missing>",
                json.dumps(
                    sanitize_for_log(item.get("errors")),
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            logger.debug(
                "GraphQL {} sanitized variables: {}",
                operation_name,
                json.dumps(
                    sanitize_for_log(variables),
                    ensure_ascii=False,
                    indent=2,
                ),
            )

        return result
