import json
import re
from http.cookiejar import Cookie

import httpx
from loguru import logger
from typing import Any, Mapping, Optional
from paypal.models import SessionState
from config import USER_AGENT


class PayPalAuthChallengeError(RuntimeError):
    """Typed result for an HTML authentication/risk challenge from PayPal.

    GraphQL normally returns JSON, but PayPal can route a request to its
    browser challenge page and still answer HTTP 200.  Keeping this as a
    typed error prevents the caller from misclassifying the event as a card
    rejection or an OTP failure.
    """

    def __init__(
        self,
        *,
        operation: str,
        status: int,
        paypal_debug_id: str = "",
        page_family: str = "authchallengenodeweb",
        challenge_kind: str = "authentication_or_risk",
        form_action: str = "/auth/validatecaptcha",
        session_id: str = "",
        csrf_present: bool = False,
        request_id_present: bool = False,
        hash_present: bool = False,
        recaptcha_site_key: str = "",
        challenge_form: Optional[dict[str, object]] = None,
    ) -> None:
        self.operation = operation
        self.status = int(status)
        self.paypal_debug_id = str(paypal_debug_id or "")
        self.page_family = str(page_family or "authchallengenodeweb")
        self.challenge_kind = str(challenge_kind or "authentication_or_risk")
        self.form_action = str(form_action or "/auth/validatecaptcha")
        self.session_id = str(session_id or "")
        self.csrf_present = bool(csrf_present)
        self.request_id_present = bool(request_id_present)
        self.hash_present = bool(hash_present)
        self.recaptcha_site_key = str(recaptcha_site_key or "")
        # Keep only non-secret form metadata needed to resume a browser
        # checkpoint. Never retain captcha responses or raw challenge HTML.
        self.challenge_form = {
            "formAction": str((challenge_form or {}).get("formAction") or form_action or "/auth/validatecaptcha"),
            "sessionId": str((challenge_form or {}).get("sessionId") or session_id or ""),
            "csrfPresent": bool((challenge_form or {}).get("csrfPresent") or csrf_present),
            "requestIdPresent": bool((challenge_form or {}).get("requestIdPresent") or request_id_present),
            "hashPresent": bool((challenge_form or {}).get("hashPresent") or hash_present),
            "recaptchaSiteKey": str((challenge_form or {}).get("recaptchaSiteKey") or recaptcha_site_key or ""),
            "captchaIframePresent": bool((challenge_form or {}).get("captchaIframePresent")),
        }
        debug = self.paypal_debug_id or "<missing>"
        super().__init__(
            f"PayPal returned an authentication/risk challenge during {operation} "
            f"(HTTP {self.status}, page={self.page_family}, paypal_debug_id={debug})"
        )


def parse_auth_challenge_form(body: str) -> dict[str, object]:
    """Extract challenge form metadata without retaining a captcha token.

    The form fields prove whether the browser challenge can be resumed. The
    reCAPTCHA result itself is generated in the browser and is intentionally
    never logged or persisted here.
    """
    text = str(body or "")

    def field(name: str) -> str:
        # Accept either quote style used by captured challenge pages.
        pattern = r"<input[^>]+name=[\"']%s[\"'][^>]*value=[\"']([^\"']*)" % re.escape(name)
        match = re.search(pattern, text, re.I)
        return match.group(1) if match else ""

    action_match = re.search(r"<form[^>]+action=[\"']([^\"']+)", text, re.I)
    site_key = field("_adsRecaptchaSiteKey")
    session_id = field("_sessionID") or re.search(r"data-sessionid=[\"']([^\"']+)", text, re.I)
    if hasattr(session_id, "group"):
        session_id = session_id.group(1)
    return {
        "formAction": (action_match.group(1) if action_match else "/auth/validatecaptcha"),
        "sessionId": str(session_id or ""),
        "csrfPresent": bool(field("_csrf")),
        "requestIdPresent": bool(field("_requestId")),
        "hashPresent": bool(field("_hash")),
        "recaptchaSiteKey": site_key,
        "captchaIframePresent": bool(re.search(r'<iframe[^>]+recaptcha', text, re.I)),
    }


def _challenge_page_family(body: str) -> str:
    """Extract a value-free PayPal challenge page family from HTML."""
    text = str(body or "")
    match = re.search(r"(?:pgrp|page|comp)=((?:authchallenge|[A-Za-z0-9_-])*nodeweb)", text, re.I)
    if match:
        return match.group(1)
    if "authchallenge" in text.lower() or "captcha-standalone" in text.lower():
        return "authchallengenodeweb"
    return "html_non_graphql"


def _challenge_kind(body: str) -> str:
    text = str(body or "").lower()
    if "recaptcha" in text or "captcha-standalone" in text:
        return "recaptcha"
    if "authchallenge" in text:
        return "authentication"
    return "authentication_or_risk"


def _accept_language(locale: str, lang: str) -> str:
    locale = (locale or "en_US").replace("_", "-")
    lang = lang or locale.split("-")[0]
    if locale.lower().startswith("pt"):
        return "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
    if locale.lower().startswith("en-gb") or locale.lower().endswith("-gb"):
        return "en-GB,en;q=0.9,en-US;q=0.8"
    return f"{locale},{lang};q=0.9,en-US;q=0.8,en;q=0.7"


def build_common_headers(locale: str = "pt_BR", lang: str = "pt", profile: Mapping | None = None) -> dict:
    active = dict(profile or {})
    return {
        "User-Agent": str(active.get("user_agent") or USER_AGENT),
        "Accept": "*/*",
        "Accept-Language": str(active.get("accept_language") or _accept_language(locale, lang)),
        "sec-ch-ua": str(active.get("sec_ch_ua") or '"Not;A=Brand";v="8", "Chromium";v="150", "Google Chrome";v="150"'),
        "sec-ch-ua-mobile": str(active.get("sec_ch_ua_mobile") or "?0"),
        "sec-ch-ua-platform": str(active.get("sec_ch_ua_platform") or '"Windows"'),
        "sec-ch-ua-arch": '"x86"',
        "sec-ch-device-memory": str(active.get("deviceMemory") or 32),
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
            "headers": build_common_headers(state.locale, state.lang, state.fingerprint_profile),
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

        content_type = str(resp.headers.get("content-type") or "").lower()
        looks_like_html = "text/html" in content_type or resp.text.lstrip().lower().startswith(("<!doctype html", "<html"))
        if looks_like_html:
            page_family = _challenge_page_family(resp.text)
            challenge_kind = _challenge_kind(resp.text)
            challenge_form = parse_auth_challenge_form(resp.text)
            logger.error(
                "GraphQL {} returned PayPal challenge HTML: status={} paypal_debug_id={} page_family={} challenge_kind={}",
                operation_name,
                resp.status_code,
                debug_id or "<missing>",
                page_family,
                challenge_kind,
            )
            raise PayPalAuthChallengeError(
                operation=operation_name,
                status=resp.status_code,
                paypal_debug_id=debug_id,
                page_family=page_family,
                challenge_kind=challenge_kind,
                challenge_form=challenge_form,
            )

        try:
            result = resp.json()
        except ValueError as error:
            logger.error(
                "GraphQL {} returned non-JSON response: status={} paypal_debug_id={} content_type={}",
                operation_name,
                resp.status_code,
                debug_id or "<missing>",
                content_type or "<missing>",
            )
            raise RuntimeError(
                f"PayPal returned an invalid GraphQL response for {operation_name} "
                f"(HTTP {resp.status_code}, paypal_debug_id={debug_id or '<missing>'})"
            ) from error

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
