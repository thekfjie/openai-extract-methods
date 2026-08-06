"""Main PayPal Billing Agreement approval flow orchestrator.

Implements the complete protocol:
  Phase 0: DataDome verification + initial page load
  Phase 1: Device fingerprint + Tealeaf + hCaptcha
  Phase 2: Create account (email submission → signup page)
  Phase 3: Fill signup form + submit (triggers 2FA SMS)
  Phase 4: OTP verification + final authorize mutation
"""
import re
import time
import json
import urllib.parse
from typing import Callable
from loguru import logger

from paypal.models import (
    SessionState,
    UserInfo,
    CardInfo,
    BillingAddress,
    generate_card,
    generate_random_email,
    normalize_locale,
    normalize_phone,
)
from paypal.session import PayPalSession, sanitize_for_log
from paypal.proxy import build_proxy_config, ProxyConfig
from paypal.fingerprint import (
    build_fn_sync_data,
    build_signup_fn_sync_data,
    send_device_fingerprint,
    send_signup_field_events,
)
from paypal.tealeaf import send_tealeaf_data
from paypal.analytics import (
    send_xo_logger,
    send_analytics_ts,
    send_observability_emit,
    send_weasley_log,
)
from paypal.graphql import (
    CHECKOUT_SESSION_DATA_QUERY,
    GRIFFIN_METADATA_QUERY,
    SUPPORTED_FUNDING_SOURCES_QUERY,
    DEFERRED_FEATURE_QUERY,
    COOKIE_BANNER_QUERY,
    INITIAL_DATA_QUERY,
    INSTALLMENT_OPTIONS_QUERY,
    ADDRESS_AUTOCOMPLETE_FROM_POSTAL_CODE_QUERY,
    INITIATE_2FA_PHONE_MUTATION,
    CONFIRM_2FA_PHONE_MUTATION,
    SIGNUP_NEW_MEMBER_MUTATION,
    AUTHORIZE_BILLING_MUTATION,
)


# Captured UK Guest -> Member uplift uses the base64-encoded reason R_ERROR.
# Keep this separate from card/signup error labels; Hermes routes on this value.
HERMES_GUEST_REASON = "Ul9FUlJPUg=="


class PayPalFlow:
    def __init__(
        self,
        ba_token: str,
        user: UserInfo,
        card: CardInfo,
        address: BillingAddress,
        max_card_attempts: int = 5,
        proxy_enabled: bool | None = None,
        proxy_index: int | None = None,
        proxy_config: ProxyConfig | None = None,
        country: str | None = None,
        locale: str | None = None,
        ec_token: str | None = None,
        prefer_skip_addfi: bool = True,
        otp_provider: Callable[[dict], str] | None = None,
        event_callback: Callable[[dict], None] | None = None,
    ):
        self.ba_token = ba_token
        self.user = user
        if not self.user.email:
            self.user.email = generate_random_email()
        self.card = card
        self.address = address
        self.max_card_attempts = max(1, max_card_attempts)
        self.prefer_skip_addfi = prefer_skip_addfi
        self.otp_provider = otp_provider
        self.event_callback = event_callback
        self.buyer_mode = "original"
        self.identity_elevation = {
            "buyer_ready": False,
            "user_id": "",
            "auth_refreshed": False,
            "funding_selected": False,
            "funding_available": False,
            "funding_available_count": 0,
            "funding_errors": [],
            "funding_checkpoints": [],
            "fatal_contingency": "",
        }
        self.proxy_config: ProxyConfig = proxy_config or build_proxy_config(
            enabled=proxy_enabled,
            index=proxy_index,
        )

        resolved_country, resolved_locale, resolved_lang = normalize_locale(
            country or address.country or "BR",
            locale,
        )
        self.address.country = resolved_country
        if not getattr(self.user, "nationality", None):
            self.user.nationality = resolved_country

        self.state = SessionState(
            ba_token=ba_token,
            ec_token=(ec_token or "").strip(),
            country=resolved_country,
            locale=resolved_locale,
            lang=resolved_lang,
        )
        self.session = PayPalSession(
            self.state,
            proxy_url=self.proxy_config.url,
            proxy_label=self.proxy_config.label,
        )

    def _emit(self, phase: str, message: str, **details) -> None:
        if not self.event_callback:
            return
        event = {"phase": phase, "message": message, **details}
        try:
            self.event_callback(event)
        except Exception as error:
            logger.warning("Protocol event callback failed: {}", error)

    def close(self):
        self.session.close()

    def run(self) -> dict:
        """Execute the complete flow. Returns result dict with status and return_url."""
        try:
            logger.info(f"=== PayPal Billing Agreement Flow ===")
            logger.info("BA Token: {}", sanitize_for_log({"ba_token": self.ba_token})["ba_token"])
            logger.info("Email: {}", sanitize_for_log({"email": self.user.email})["email"])
            logger.info("Phone: {}", sanitize_for_log({"phone": self.user.phone})["phone"])
            logger.info("Country/Locale: {} / {}", self.state.country, self.state.locale)
            if self.state.ec_token:
                logger.info(
                    "Pre-seeded EC Token: {}",
                    sanitize_for_log({"ec_token": self.state.ec_token})["ec_token"],
                )
            logger.info(f"Proxy: {self.proxy_config.label}")

            self._emit("initial_load", "正在加载协议授权页")
            self._phase0_initial_load()
            self._emit("risk_controls", "正在同步风控与设备信号")
            self._phase1_risk_controls()
            self._emit("account", "正在创建 Guest 买家上下文")
            self._phase2_create_account()
            self._emit("verification", "正在进入短信验证和身份提升")
            self._phase3_signup_and_2fa()
            self._emit("authorize", "正在提交 Billing Agreement 授权")
            result = self._phase4_authorize()

            if result.get("status") == "success":
                logger.success(f"=== Flow completed successfully ===")
            else:
                logger.error(f"=== Flow completed with error status ===")
            return result
        except Exception as e:
            logger.error(f"Flow failed: {e}")
            raise
        finally:
            self.close()

    def _phase0_initial_load(self):
        """Load the agreement approval page, handle DataDome if needed."""
        logger.info("--- Phase 0: Initial page load ---")

        url = f"https://www.paypal.com/agreements/approve?ba_token={self.ba_token}"

        # First GET - may return 403 with DataDome challenge or 302 redirect
        resp = self.session.get(url, headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Upgrade-Insecure-Requests": "1",
        })

        if resp.status_code == 403:
            logger.info("Got 403 - DataDome challenge detected")
            # DataDome returns a page with embedded dd object and ct.ddc.paypal.com/c.js
            # The browser solves this via iframe. In protocol mode we need the datadome
            # cookie that was set on the 403 response, then POST with adsddtoken.
            # For now, log that DataDome was encountered - the cookie is already stored.
            logger.warning("DataDome challenge requires browser-level solving. "
                           "Cookie from 403 response stored, attempting to proceed...")

            # Try the POST approach that the browser uses after DataDome resolves
            post_url = f"{url}&YWRzZGRjYXB0Y2hh=1"
            resp = self.session.post(post_url, headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.paypal.com",
            }, data={"adsddtoken": ""})

        if resp.status_code == 302:
            redirect_url = resp.headers.get("Location", "")
            logger.info(f"Redirected to: {redirect_url}")
            # Extract ssrt from redirect URL
            ssrt_match = re.search(r"ssrt=(\d+)", redirect_url)
            if ssrt_match:
                self.state.ssrt = ssrt_match.group(1)
            # Follow the redirect
            if redirect_url.startswith("/"):
                redirect_url = f"https://www.paypal.com{redirect_url}"
            resp = self.session.get(redirect_url)

        # Parse the login/signup page
        html = resp.text
        logger.info(f"Page loaded: {resp.status_code}, {len(html)} bytes")
        self._extract_modxo_action_ids(html, str(resp.url))

        # Extract ctxId
        ctx_match = re.search(r'"ctxId"[^"]*"([^"]+)"', html)
        if ctx_match:
            self.state.ctx_id = ctx_match.group(1)
            logger.info(f"Context ID: {self.state.ctx_id}")

        # Extract ssrt if not yet found
        if not self.state.ssrt:
            ssrt_match = re.search(r"ssrt=(\d+)", str(resp.url))
            if not ssrt_match:
                ssrt_match = re.search(r"ssrt=(\d+)", html)
            if ssrt_match:
                self.state.ssrt = ssrt_match.group(1)
                logger.info(f"SSRT: {self.state.ssrt}")

    @staticmethod
    def _extract_window_initial_data(html: str) -> dict:
        """Extract checkoutweb/weasley window.__INITIAL_DATA__ JSON."""
        # The page contains many reads of window.__INITIAL_DATA__ before the
        # actual server-side assignment.  Anchor on `= {` so we do not parse a
        # JavaScript function body from an earlier reference.
        marker = re.search(r"window\.__INITIAL_DATA__\s*=", html or "")
        if not marker:
            return {}

        start = html.find("{", marker.end())
        if start < 0:
            return {}

        depth = 0
        in_str = False
        escape = False
        for idx in range(start, len(html)):
            ch = html[idx]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start:idx + 1])
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse __INITIAL_DATA__: {e}")
                        return {}

        return {}

    @staticmethod
    def _extract_content_identifier(html: str, country: str = "BR", lang: str = "pt") -> str:
        """Extract or build the dynamic signup terms contentIdentifier."""
        for pattern in (
            r'"contentIdentifier"\s*:\s*"([^"]*signupTerms[^"]*)"',
            r'\\"contentIdentifier\\"\s*:\s*\\"([^"\\]*signupTerms[^"\\]*)\\"',
            r'([A-Z]{2}:[a-z]{2}:[0-9a-f]{16,64}:compliance\.signupTerms)',
        ):
            match = re.search(pattern, html or "", re.I)
            if match:
                return match.group(1).replace("\\/", "/")
        return f"{country}:{lang}:compliance.signupTerms"

    def _build_signup_url(self) -> str:
        """Build the canonical checkoutweb/signup URL used as GraphQL Referer."""
        params: list[tuple[str, str]] = []
        if self.state.ssrt:
            params.append(("ssrt", self.state.ssrt))
        params.extend([
            ("ul", "1"),
            ("modxo_redirect_reason", "guest_user"),
            ("locale.x", self.state.locale or "pt_BR"),
            ("country.x", self.state.country or self.address.country or "BR"),
            ("ba_token", self.ba_token),
            ("token", self.state.ec_token),
            ("rcache", "1"),
            ("cookieBannerVariant", "hidden"),
        ])
        return "https://www.paypal.com/checkoutweb/signup?" + urllib.parse.urlencode(params)

    def _lang_code(self) -> str:
        return self.state.lang or (self.state.locale.split("_")[0] if self.state.locale else "pt")

    def _locale_code(self) -> str:
        return self.state.locale or f"{self._lang_code()}_{self.state.country or 'BR'}"

    def _country_code(self) -> str:
        return self.state.country or self.address.country or "BR"

    def _assert_identity_uplift(self, stage: str = "pre-authorize") -> None:
        """Hard gate: EUAT + buyer.userId must exist before billing.authorize."""
        missing = []
        if not self.state.euat_token:
            missing.append("euat_token")
        if not self.state.user_id:
            missing.append("buyer.userId")
        if not (self.state.ec_token or self.ba_token):
            missing.append("ec_or_ba_token")
        if missing:
            raise RuntimeError(
                f"Identity uplift incomplete at {stage}; missing: {', '.join(missing)}. "
                "Refuse to call billing.authorize without Guest->Member uplift."
            )
        logger.info(
            "Identity uplift OK at {}: userId={} euat=<present> token={}",
            stage,
            self.state.user_id,
            sanitize_for_log({"token": self.state.ec_token or self.ba_token})["token"],
        )
        self.identity_elevation.update(
            buyer_ready=True,
            user_id=self.state.user_id,
            auth_refreshed=bool(self.state.checkout_drop_loaded and self.state.hermes_loaded),
        )

    def _persist_euat_cookie(self):
        if not self.state.euat_token:
            return
        self.session.client.cookies.set(
            "AV894Kt2TSumQQrJwe-8mzmyREO",
            self.state.euat_token,
            domain=".paypal.com",
        )

    def _load_checkoutweb_drop(self) -> None:
        """Load the post-signup checkout drop page before entering Hermes.

        The successful UK capture performs this request after
        SignUpNewMemberMutation.  Besides refreshing the EUAT cookie, it
        switches the PayPal routing cookie to checkoutuinodeweb and provides
        the browser context expected by the following Hermes request.
        """
        if not self.state.signup_url:
            raise RuntimeError("checkoutweb/drop requires a signup URL")
        headers = {
            "Accept": "*/*",
            "Referer": self.state.signup_url,
        }
        if self.state.euat_token:
            headers["X-PayPal-Internal-EUAT"] = self.state.euat_token
        logger.info("Loading checkoutweb/drop post-signup context...")
        response = self.session.get(
            "https://www.paypal.com/checkoutweb/drop",
            headers=headers,
        )
        if response.status_code not in (200, 204):
            raise RuntimeError(
                f"checkoutweb/drop returned HTTP {response.status_code}"
            )
        self.session._sync_state_cookies()
        if self.state.euat_token:
            self._persist_euat_cookie()
        self.state.checkout_drop_loaded = True
        logger.info(
            "checkoutweb/drop loaded: status={} bytes={} euat={}",
            response.status_code,
            len(response.content),
            "present" if self.state.euat_token else "missing",
        )

    def _extract_user_id_from_text(self, text: str) -> str:
        if not text:
            return ""
        patterns = (
            r'"userId"\s*:\s*"([A-Z0-9]{8,20})"',
            r'"party_id"\s*:\s*"([A-Z0-9]{8,20})"',
            r'(?:party_id|cust|userId)["=:]+([A-Z0-9]{8,20})',
        )
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_onboarding_redirect(rsc_text: str) -> str:
        """Extract onboardingRedirectUrl from Next/RSC server-action response."""
        match = re.search(r'"onboardingRedirectUrl"\s*:\s*"([^"]+)"', rsc_text or "")
        if not match:
            return ""
        return match.group(1).replace("\\/", "/")

    @staticmethod
    def _find_access_token(value) -> str:
        """Find an accessToken recursively in GraphQL data/errorData."""
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "accessToken" and isinstance(item, str) and item:
                    return item
                found = PayPalFlow._find_access_token(item)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = PayPalFlow._find_access_token(item)
                if found:
                    return found
        return ""

    @staticmethod
    def _has_buyer_not_set(result) -> bool:
        items = result if isinstance(result, list) else [result]
        for item in items:
            if not isinstance(item, dict):
                continue
            for err in item.get("errors") or []:
                data = err.get("data") or {}
                if data.get("contingency") == "BUYER_NOT_SET":
                    return True
                if err.get("message") == "BUYER_NOT_SET":
                    return True
        return False

    def _extract_modxo_action_ids(self, html: str, base_url: str):
        """Extract Next server-action IDs from ModXO JS chunks.

        The browser sends these values in the Next-Action header. They are
        deployment-specific, so hard-coding the values from one capture breaks
        after PayPal ships a new bundle.
        """
        action_names = {
            "show_create_account_action_id": "showCreateAccountAction",
            "create_user_action_id": "createUserAction",
        }

        def scan(text: str) -> bool:
            changed = False
            for attr, action_name in action_names.items():
                if getattr(self.state, attr):
                    continue
                name_idx = text.find(f'"{action_name}"')
                if name_idx < 0:
                    continue
                window = text[max(0, name_idx - 500):name_idx]
                ids = re.findall(r'"([0-9a-f]{32,64})"', window)
                if ids:
                    action_id = ids[-1]
                    setattr(self.state, attr, action_id)
                    logger.info(f"ModXO action {attr}: {action_id}")
                    changed = True
            return changed

        scan(html or "")
        if self.state.show_create_account_action_id and self.state.create_user_action_id:
            return

        script_urls = []
        for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html or "", re.I):
            if "/pay/_next/static/chunks/" not in src:
                continue
            url = urllib.parse.urljoin(base_url, src)
            if url not in script_urls:
                script_urls.append(url)

        for script_url in script_urls[:80]:
            try:
                js_resp = self.session.get(
                    script_url,
                    headers={
                        "Accept": "*/*",
                        "Referer": base_url,
                        "Sec-Fetch-Dest": "script",
                        "Sec-Fetch-Mode": "no-cors",
                        "Sec-Fetch-Site": "same-origin",
                    },
                )
                if js_resp.status_code == 200:
                    scan(js_resp.text)
                if self.state.show_create_account_action_id and self.state.create_user_action_id:
                    return
            except Exception as e:
                logger.debug(f"Failed to inspect ModXO chunk {script_url}: {e}")

    def _card_issuer_type(self) -> str:
        """PayPal GraphQL CardIssuerType enum."""
        prefix2 = int(self.card.number[:2]) if self.card.number[:2].isdigit() else 0
        prefix4 = int(self.card.number[:4]) if self.card.number[:4].isdigit() else 0
        if 51 <= prefix2 <= 55 or 2221 <= prefix4 <= 2720:
            return "MASTER_CARD"
        if self.card.number.startswith("4"):
            return "VISA"
        if self.card.number.startswith("3"):
            return "AMEX"
        if self.card.number.startswith("6"):
            return "DISCOVER"
        return "VISA"

    def _masked_card_number(self) -> str:
        return sanitize_for_log({"cardNumber": self.card.number})["cardNumber"]

    def _masked_phone(self) -> str:
        return sanitize_for_log({"phone": self.user.phone})["phone"]

    def _update_user_phone(self, phone: str):
        """Update phone fields used by the signup/2FA GraphQL calls."""
        full, local, country_code = normalize_phone(phone, default_country=self._country_code())
        if len(local) < 8:
            raise ValueError("local phone number is too short")
        self.user.phone = full
        self.user.phone_country_code = country_code
        self.user.phone_local = local
        logger.info("Phone updated for OTP retry: {}", self._masked_phone())

    def _initiate_2fa_phone_confirmation(self, token: str, signup_url: str) -> tuple[str, str]:
        """Send a new 2FA SMS and return authId/challengeId."""
        logger.info("Step 1: Initiating 2FA phone confirmation for {}...", self._masked_phone())
        send_weasley_log(
            self.session,
            self.state.ec_token,
            signup_url,
            [
                "weasley_risk_based_phone_confirmation_modal_component_mounted",
                "weasley_initiate_phone_confirmation_start",
                "weasley_api_request_initiate_risk_based_two_factor_phone_confirmation_mutation",
            ],
            country=self._country_code(),
            lang=self._lang_code(),
        )
        initiate_result = self.session.graphql(
            "InitiateRiskBasedTwoFactorPhoneConfirmationMutation",
            INITIATE_2FA_PHONE_MUTATION,
            {
                "phoneNumber": self.user.phone_local,
                "locale": {"country": self._country_code(), "lang": self._lang_code()},
                "phoneCountry": self._country_code(),
                "token": token,
            },
        )
        logger.info(
            "2FA initiation result (sanitized): {}",
            json.dumps(sanitize_for_log(initiate_result), ensure_ascii=False, indent=2)[:500],
        )

        result_obj = initiate_result[0] if isinstance(initiate_result, list) else initiate_result
        tfa_data = result_obj.get("data", {}).get(
            "initiateRiskBasedTwoFactorPhoneConfirmation", {}
        )
        auth_id = tfa_data.get("authId", "")
        challenge_id = tfa_data.get("challengeId", "")
        state = tfa_data.get("state", "")
        logger.info("2FA state: {}, authId=<redacted>, challengeId=<redacted>", state)

        if not auth_id or not challenge_id:
            raise RuntimeError("Failed to get authId/challengeId from 2FA initiation")
        return auth_id, challenge_id

    def _confirm_2fa_phone_confirmation(
        self,
        token: str,
        signup_url: str,
        auth_id: str,
        challenge_id: str,
        otp: str,
    ) -> bool:
        """Confirm one OTP attempt. Return True only on CONFIRMED."""
        logger.info("Step 2: Confirming OTP: <redacted>")
        send_weasley_log(
            self.session,
            self.state.ec_token,
            signup_url,
            [
                "weasley_confirm_phone_confirmation_start",
                "weasley_api_request_confirm_risk_based_two_factor_phone_confirmation_mutation",
            ],
            country=self._country_code(),
            lang=self._lang_code(),
        )
        confirm_result = self.session.graphql(
            "ConfirmRiskBasedTwoFactorPhoneConfirmationMutation",
            CONFIRM_2FA_PHONE_MUTATION,
            {
                "pin": otp,
                "authId": auth_id,
                "challengeId": challenge_id,
                "token": token,
            },
        )
        logger.info(
            "OTP confirmation result (sanitized): {}",
            json.dumps(sanitize_for_log(confirm_result), ensure_ascii=False, indent=2)[:500],
        )

        result_obj = confirm_result[0] if isinstance(confirm_result, list) else confirm_result
        confirm_data = result_obj.get("data", {}).get(
            "confirmRiskBasedTwoFactorPhoneConfirmation", {}
        ) or {}
        confirm_state = confirm_data.get("state", "")
        if confirm_state == "CONFIRMED":
            logger.success("OTP confirmed successfully!")
            return True

        errors = result_obj.get("errors") or []
        if errors:
            logger.warning(
                "OTP confirmation failed with errors: {}",
                json.dumps(sanitize_for_log(errors), ensure_ascii=False, indent=2),
            )
        else:
            logger.warning("OTP confirmation failed, state: {}", confirm_state or "<missing>")
        return False

    def _confirm_phone_with_retry(self, token: str, signup_url: str):
        """Loop until OTP is confirmed; user can enter a new phone to resend."""
        while True:
            try:
                auth_id, challenge_id = self._initiate_2fa_phone_confirmation(token, signup_url)
            except Exception as e:
                logger.error("Failed to initiate OTP for {}: {}", self._masked_phone(), e)
                while True:
                    value = self._read_operator_value(
                        "resend_phone",
                        "发送验证码失败，请输入新的手机号重新发送",
                    )
                    if value.lower() in {"q", "quit", "exit"}:
                        raise RuntimeError("OTP confirmation cancelled by user") from e
                    try:
                        self._update_user_phone(value)
                        break
                    except ValueError as phone_error:
                        logger.warning("手机号无效：{}。请重新输入。", phone_error)
                continue
            logger.info("SMS verification code sent to phone: {}", self._masked_phone())
            self._emit(
                "waiting_otp",
                "短信验证码已发送",
                phone=self._masked_phone(),
                accepts_phone=True,
            )

            while True:
                value = self._read_operator_value(
                    "otp",
                    "输入6位短信验证码；也可输入新手机号重新发送",
                )

                if value.lower() in {"q", "quit", "exit"}:
                    raise RuntimeError("OTP confirmation cancelled by user")

                if len(value) == 6 and value.isdigit():
                    if self._confirm_2fa_phone_confirmation(
                        token,
                        signup_url,
                        auth_id,
                        challenge_id,
                        value,
                    ):
                        return
                    logger.warning(
                        "验证码验证失败。可以继续输入新的6位验证码，或输入新手机号重新发送验证码。"
                    )
                    self._emit("waiting_otp", "验证码校验失败，请重新输入", phone=self._masked_phone())
                    continue

                try:
                    self._update_user_phone(value)
                    logger.info("Re-sending OTP to the new phone...")
                    break
                except ValueError as e:
                    logger.warning(
                        "输入既不是6位验证码，也不是有效手机号：{}。请重新输入。",
                        e,
                    )

    def _read_operator_value(self, kind: str, prompt: str) -> str:
        if self.otp_provider:
            value = self.otp_provider({
                "kind": kind,
                "prompt": prompt,
                "phone": self._masked_phone(),
                "country": self._country_code(),
            })
            return str(value or "").strip()
        return input(f"\n>>> {prompt}；输入 q 退出: " ).strip()

    def _card_expiration_date(self) -> str:
        exp_parts = self.card.expiry.split("/")
        return f"{exp_parts[0]}/{exp_parts[1]}" if len(exp_parts) == 2 else self.card.expiry

    def _dob_payload(self) -> dict:
        dob_parts = self.user.dob.split("/")
        return (
            {"day": dob_parts[0], "month": dob_parts[1], "year": dob_parts[2]}
            if len(dob_parts) == 3
            else {}
        )

    def _build_line1(self) -> str:
        street = (self.address.street or "").strip()
        house = (self.address.house_number or "").strip()
        if house and house not in street:
            return f"{street}, {house}" if street else house
        return street

    def _build_signup_variables(self, token: str) -> dict:
        card_type = self._card_issuer_type()
        country = self._country_code()
        lang = self._lang_code()
        line1 = self._build_line1()
        content_identifier = self.state.content_identifier or (
            f"{country}:{lang}:"
            f"{self.state.content_hash or 'cbb52e297bf39e0b49f4a60d001f4013'}:"
            "compliance.signupTerms"
        )
        variables = {
            "card": {
                "cardNumber": self.card.number,
                "expirationDate": self._card_expiration_date(),
                "securityCode": self.card.cvv,
                "type": card_type,
            },
            "country": country,
            "email": self.user.email,
            "firstName": self.user.first_name,
            "lastName": self.user.last_name,
            "phone": {
                "countryCode": self.user.phone_country_code.lstrip("+"),
                "number": self.user.phone_local,
                "type": "MOBILE",
            },
            "supportedThreeDsExperiences": ["IFRAME"],
            "token": token,
            "billingAddress": {
                "postalCode": self.address.postal_code,
                "line1": line1,
                "city": self.address.city,
                "accountQuality": {
                    "autoCompleteType": "MANUAL" if country != "BR" else "ANS",
                    "isUserModified": True,
                },
                "country": country,
                "familyName": self.user.last_name,
                "givenName": self.user.first_name,
            },
            "shippingAddress": {
                "postalCode": "",
                "line1": "",
                "city": "",
                "accountQuality": {
                    "autoCompleteType": "MANUAL",
                    "isUserModified": False,
                },
                "country": country,
                "familyName": self.user.last_name,
                "givenName": self.user.first_name,
            },
            "residentialAddress": {
                "postalCode": self.address.postal_code,
                "line1": line1,
                "city": self.address.city,
                "accountQuality": {
                    "autoCompleteType": "MANUAL" if country != "BR" else "ANS",
                    "isUserModified": True,
                },
                "country": country,
                "familyName": self.user.last_name,
                "givenName": self.user.first_name,
            },
            "contentIdentifier": content_identifier,
            "marketingOptOut": False if country != "BR" else True,
            "password": self.user.password,
            "dateOfBirth": self._dob_payload(),
            "nationality": getattr(self.user, "nationality", None) or country,
            "legalAgreements": {},
        }

        if self.address.district:
            variables["billingAddress"]["line2"] = self.address.district
            variables["residentialAddress"]["line2"] = self.address.district
        if self.address.state:
            variables["billingAddress"]["state"] = self.address.state
            variables["residentialAddress"]["state"] = self.address.state
            variables["shippingAddress"]["state"] = ""

        if country == "BR" and getattr(self.user, "cpf", ""):
            variables["identityDocument"] = {
                "type": "CPF",
                "value": self.user.cpf,
            }
            variables["crsData"] = None
        else:
            variables["crsData"] = {
                "firstName": self.user.first_name,
                "lastName": self.user.last_name,
                "subjectToTaxOutsideLegalCountry": False,
                "taxDetails": [{"countryCode": country}],
            }

        return variables

    def _send_signup_attempt(self, token: str, signup_url: str) -> dict:
        card_type = self._card_issuer_type()
        try:
            self.session.graphql(
                "InstallmentOptionsQuery",
                INSTALLMENT_OPTIONS_QUERY,
                {
                    "buyerCountry": self._country_code(),
                    "cardNumber": self.card.number,
                    "cardType": card_type,
                    "token": token,
                },
            )
        except Exception as e:
            logger.warning(f"InstallmentOptionsQuery failed: {e}")

        send_signup_field_events(
            self.session,
            token,
            [
                "email",
                "phone",
                "cardNumber",
                "cardExpiry",
                "cardCvv",
                "password",
                "firstName",
                "lastName",
                "billingLine1",
                "billingCity",
                "billingPostalCode",
                "billingState",
                "dateOfBirth",
                "identityDocumentNumber",
            ],
        )
        send_weasley_log(
            self.session,
            self.state.ec_token,
            signup_url,
            [
                "weasley_create_account_and_pay_submit",
                "weasley_api_request_sign_up_new_member_mutation",
            ],
            country=self._country_code(),
            lang=self._lang_code(),
        )
        signup_result = self.session.graphql(
            "SignUpNewMemberMutation",
            SIGNUP_NEW_MEMBER_MUTATION,
            self._build_signup_variables(token),
            extra_body={"fn_sync_data": build_signup_fn_sync_data(token)},
        )
        logger.info(
            "Signup result (sanitized): {}",
            json.dumps(
                sanitize_for_log(signup_result),
                ensure_ascii=False,
                indent=2,
            )[:4000],
        )
        return signup_result

    def _consume_signup_result(self, signup_result) -> tuple[bool, list[dict]]:
        """Apply successful signup data to state. Return (success, errors)."""
        result_obj = signup_result[0] if isinstance(signup_result, list) else signup_result
        onboard_data = result_obj.get("data", {}).get("onboardAccount", {})
        if onboard_data:
            buyer = onboard_data.get("buyer", {}) or {}
            user_id = buyer.get("userId", "") or ""
            auth = buyer.get("auth", {}) or {}
            access_token = auth.get("accessToken", "") or ""
            if not access_token:
                access_token = self._find_access_token(result_obj)
            if user_id:
                self.state.user_id = user_id
            if access_token:
                self.state.euat_token = access_token
                self._persist_euat_cookie()
            logger.success(
                "Account created/uplifted! User ID: {} EUAT: {}",
                self.state.user_id or "<missing>",
                "present" if self.state.euat_token else "missing",
            )
            if self.state.euat_token and self.state.user_id:
                return True, []
            if self.state.euat_token:
                logger.warning("Signup returned EUAT without userId; will try uplift from Hermes page")
                return True, []
            return False, result_obj.get("errors", []) or []

        errors = result_obj.get("errors", []) or []
        if errors:
            for err in errors:
                logger.error(
                    "Signup error detail: {}",
                    json.dumps(
                        sanitize_for_log({
                            "message": err.get("message"),
                            "name": err.get("_name"),
                            "statusCode": err.get("statusCode"),
                            "checkpoints": err.get("checkpoints"),
                            "contingency": err.get("contingency"),
                            "path": err.get("path"),
                            "data": err.get("data"),
                            "errorData": err.get("errorData"),
                            "meta": err.get("meta"),
                            "extensions": err.get("extensions"),
                        }),
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
        logger.error(
            "Signup failed because onboardAccount is empty. Sanitized response: {}",
            json.dumps(
                sanitize_for_log(result_obj),
                ensure_ascii=False,
                indent=2,
            )[:8000],
        )
        return False, errors

    @staticmethod
    def _dict_contains_card_field(value) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                compact_key = str(key).lower().replace("_", "").replace("-", "")
                if compact_key in {"cardnumber", "card", "cardnumberfield"}:
                    return True
                if isinstance(item, str):
                    item_lower = item.lower()
                    if item_lower in {"cardnumber", "card_generic_error"}:
                        return True
                if PayPalFlow._dict_contains_card_field(item):
                    return True
        elif isinstance(value, list):
            return any(PayPalFlow._dict_contains_card_field(item) for item in value)
        return False

    @staticmethod
    def _is_card_related_signup_error(errors: list[dict]) -> bool:
        card_messages = {
            "CARD_GENERIC_ERROR",
            "INSTRUMENT_SHARING_LIMIT_EXCEEDED",
            "CC_LINKED_TO_FULL_ACCOUNT",
            "CREATE_CARD_ACCOUNT_CANDIDATE_VALIDATION_ERROR",
        }
        for err in errors or []:
            checkpoints = set(err.get("checkpoints") or [])
            if checkpoints.intersection({"addCard", "validate.fi", "card", "fi"}):
                return True
            message = str(err.get("message") or "")
            if message in card_messages:
                return True
            if PayPalFlow._dict_contains_card_field(err.get("errorData")):
                return True
        return False

    @staticmethod
    def _has_signup_error_message(errors: list[dict], message: str) -> bool:
        return any(str(err.get("message") or "") == message for err in errors or [])

    def _signup_with_card_retry(self, token: str, signup_url: str):
        """Retry SignUpNewMember with a fresh generated Visa/MasterCard on card errors."""
        self.state.euat_token = ""
        last_errors: list[dict] = []
        last_access_token = ""

        for attempt in range(1, self.max_card_attempts + 1):
            logger.info(
                "Step 3: Creating account (SignUpNewMember), card attempt {}/{}: {}",
                attempt,
                self.max_card_attempts,
                self._masked_card_number(),
            )
            signup_result = self._send_signup_attempt(token, signup_url)
            success, errors = self._consume_signup_result(signup_result)
            if success:
                return

            last_errors = errors
            access_token = self._find_access_token(errors)
            if access_token:
                last_access_token = access_token

            if self._has_signup_error_message(errors, "ACCOUNT_ALREADY_EXISTS"):
                if last_access_token:
                    self.state.euat_token = last_access_token
                    logger.warning(
                        "Signup returned ACCOUNT_ALREADY_EXISTS after a previous "
                        "response already issued an access token. Reusing that "
                        "token and continuing instead of re-submitting signup."
                    )
                    return
                raise RuntimeError(
                    "Signup failed: ACCOUNT_ALREADY_EXISTS and no prior access "
                    "token is available for this session."
                )

            if self._is_card_related_signup_error(errors):
                if access_token:
                    self.state.euat_token = access_token
                    self.buyer_mode = "identity_elevation"
                    self.identity_elevation.update(
                        funding_errors=sorted({
                            str(error.get("message") or "")
                            for error in errors
                            if str(error.get("message") or "")
                        }),
                        funding_checkpoints=sorted({
                            str(checkpoint)
                            for error in errors
                            for checkpoint in (error.get("checkpoints") or [])
                            if str(checkpoint)
                        }),
                    )
                    logger.warning(
                        "Card/addCard failed but PayPal returned an access token. "
                        "The member account is already created at this point, so "
                        "re-sending SignUpNewMember with a new card would produce "
                        "ACCOUNT_ALREADY_EXISTS. Continuing with the returned token."
                    )
                    return

                if attempt >= self.max_card_attempts:
                    raise RuntimeError(
                        "Signup failed: card was rejected after "
                        f"{self.max_card_attempts} attempts"
                    )

                logger.warning(
                    "Card rejected by signup/addCard. Fetching a fresh random "
                    "Visa/MasterCard from suijidaquan and retrying..."
                )
                self.card = generate_card(proxy_url=self.proxy_config.url)
                logger.info(
                    "New generated card for retry: {} exp={}",
                    self._masked_card_number(),
                    self.card.expiry,
                )
                continue

            if access_token:
                self.state.euat_token = access_token
                logger.info("Got access token from signup error response")
                return

            break

        raise RuntimeError(
            "Signup failed: no usable access token obtained. "
            f"Last errors: {json.dumps(sanitize_for_log(last_errors), ensure_ascii=False)[:1000]}"
        )

    def _follow_modxo_action_redirect(self, resp, referer: str):
        """Follow Next server-action redirects emitted by ModXO.

        PayPal's server action may return a normal Location header or an
        x-action-redirect header such as "/?...;push". In the latter case the
        path is relative to the /pay app, not the site root.
        """
        redirect_url = resp.headers.get("Location") or resp.headers.get("x-action-redirect") or ""
        if not redirect_url:
            return resp
        redirect_url = redirect_url.split(";", 1)[0]
        if redirect_url.startswith("/?"):
            redirect_url = f"https://www.paypal.com/pay{redirect_url}"
        elif redirect_url.startswith("/"):
            redirect_url = f"https://www.paypal.com{redirect_url}"
        logger.info(f"Following ModXO action redirect: {redirect_url[:140]}...")
        return self.session.get(
            redirect_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": referer,
                "Upgrade-Insecure-Requests": "1",
            },
        )

    def _phase1_risk_controls(self):
        """Send device fingerprints, Tealeaf data, analytics."""
        logger.info("--- Phase 1: Risk control signals ---")

        # Device fingerprint (p1, p2, w endpoints)
        send_device_fingerprint(self.session, self.ba_token)

        # Tealeaf initial data
        page_url = f"https://www.paypal.com/pay?ssrt={self.state.ssrt}&token={self.ba_token}&ul=1"
        send_tealeaf_data(self.session, page_url)

        # Analytics
        send_analytics_ts(self.session, "main:xo:modxo:login", self.ba_token)
        send_observability_emit(self.session, self.ba_token)

        logger.info("Risk control signals sent")

    def _phase2_create_account(self):
        """Submit 'Create Account' action to get to the signup page."""
        logger.info("--- Phase 2: Create account flow ---")

        resp = None
        # Browser trace (2026-07-04): ModXO is a Next server-action flow.
        # First click "Pay with Card", then submit an email/createAccount
        # action, whose RSC payload returns onboardingRedirectUrl.
        pay_url = (
            f"https://www.paypal.com/pay/?ssrt={self.state.ssrt}"
            f"&token={self.ba_token}&ul=1&ctxId={self.state.ctx_id}"
            f"&country.x={self._country_code()}"
        )
        try:
            if not self.state.show_create_account_action_id or not self.state.create_user_action_id:
                raise RuntimeError("missing dynamic ModXO Next-Action ids")

            logger.info("Submitting browser-like Pay_With_Card server action...")
            pay_with_card_url = f"{pay_url}&paypal_client_cfci=modxo_vaulted_not_recurring-Pay_With_Card"
            pay_resp = self.session.post(
                pay_with_card_url,
                files=[
                    ("_1_ctxId", (None, self.state.ctx_id)),
                    ("_1_formName", (None, "createAccountAction")),
                    ("0", (None, '["$K1"]')),
                ],
                headers={
                    "Accept": "text/x-component",
                    "Origin": "https://www.paypal.com",
                    "Referer": pay_url,
                    "Next-Action": self.state.show_create_account_action_id,
                },
            )
            if pay_resp.status_code in (301, 302, 303, 307, 308) or pay_resp.headers.get("x-action-redirect"):
                self._follow_modxo_action_redirect(pay_resp, pay_url)

            logger.info("Submitting browser-like Continue_To_Payment server action...")
            continue_url = f"{pay_url}&paypal_client_cfci=modxo_vaulted_not_recurring-Continue_To_Payment"
            rsc_resp = self.session.post(
                continue_url,
                files=[
                    ("_1_ctxId", (None, self.state.ctx_id)),
                    ("_1_token", (None, self.ba_token)),
                    ("_1_login_email", (None, self.user.email)),
                    ("_1_formName", (None, "createAccount")),
                    ("0", (None, f'["$K1",{{"emailSubmitTime":{int(time.time() * 1000)}}}]')),
                ],
                headers={
                    "Accept": "text/x-component",
                    "Origin": "https://www.paypal.com",
                    "Referer": pay_with_card_url,
                    "Next-Action": self.state.create_user_action_id,
                },
            )
            onboarding_url = self._extract_onboarding_redirect(rsc_resp.text)
            if onboarding_url:
                logger.info(f"Onboarding redirect URL: {onboarding_url[:140]}...")
                resp = self.session.get(
                    onboarding_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": pay_url,
                        "Upgrade-Insecure-Requests": "1",
                    },
                )
        except Exception as e:
            logger.warning(f"Browser-like ModXO server-action path failed: {e}")

        if resp is None:
            # Fallback for older deployments that still accept a compact form.
            base_url = (
                f"https://www.paypal.com/pay?ssrt={self.state.ssrt}"
                f"&token={self.ba_token}&ul=1"
                f"&paypal_client_cfci=modxo_vaulted_not_recurring-Pay_With_Card"
            )

            form_data = {
                "ctxId": self.state.ctx_id,
                "formName": "createAccountAction",
                "fn_sync_data": build_fn_sync_data(self.ba_token),
            }

            resp = self.session.post(base_url, data=form_data, headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://www.paypal.com",
                "Referer": f"https://www.paypal.com/pay?ssrt={self.state.ssrt}&token={self.ba_token}&ul=1",
            })

        # Handle redirect chain
        while resp.status_code in (302, 303):
            redirect_url = resp.headers.get("Location", "")
            if redirect_url.startswith("/"):
                redirect_url = f"https://www.paypal.com{redirect_url}"
            logger.info(f"Following redirect: {redirect_url[:100]}...")
            resp = self.session.get(redirect_url)

        html = resp.text

        # Extract EC token from the new URL or page content
        ec_match = re.search(r"token=(EC-\w+)", str(resp.url))
        if ec_match:
            self.state.ec_token = ec_match.group(1)
            logger.info("EC Token: {}", sanitize_for_log({"ec_token": self.state.ec_token})["ec_token"])
        else:
            ec_match = re.search(r"EC-\w+", html)
            if ec_match:
                self.state.ec_token = ec_match.group(0)
                logger.info("EC Token (from HTML): {}", sanitize_for_log({"ec_token": self.state.ec_token})["ec_token"])

        # The real browser next loads checkoutweb/weasley.  This request is not
        # just cosmetic: it sets checkout cookies (for example l7_az/x-pp-s),
        # exposes the current content hash, and matches the Referer/context
        # expected by the following GraphQL mutations.
        if self.state.ec_token:
            signup_url = self._build_signup_url()
            self.state.signup_url = signup_url
            logger.info(f"Loading checkout signup app: {signup_url}")
            signup_resp = self.session.get(
                signup_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": str(resp.url),
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            logger.info(
                "Checkout signup app loaded: {} bytes={}",
                signup_resp.status_code,
                len(signup_resp.content),
            )
            if signup_resp.status_code in (301, 302, 303, 307, 308):
                redirect_url = signup_resp.headers.get("Location", "")
                if redirect_url:
                    redirect_url = urllib.parse.urljoin(signup_url, redirect_url)
                    if "/checkoutweb/signup" in redirect_url:
                        self.state.signup_url = redirect_url
                    logger.warning(
                        "Checkout signup app redirected to {}; preserving signup referer {}",
                        redirect_url[:140],
                        self.state.signup_url[:140],
                    )
            initial_data = self._extract_window_initial_data(signup_resp.text)
            content_hash = initial_data.get("contentHash")
            if content_hash:
                self.state.content_hash = content_hash
                logger.info(f"Content hash: {self.state.content_hash}")
            content_identifier = self._extract_content_identifier(
                signup_resp.text,
                self._country_code(),
                self._lang_code(),
            )
            if content_hash and content_identifier.endswith(":compliance.signupTerms") and content_hash not in content_identifier:
                content_identifier = f"{self._country_code()}:{self._lang_code()}:{content_hash}:compliance.signupTerms"
            elif content_identifier == f"{self._country_code()}:{self._lang_code()}:compliance.signupTerms":
                # Prefer a country/lang scoped hash when PayPal does not expose one.
                content_identifier = (
                    f"{self._country_code()}:{self._lang_code()}:"
                    f"{'759169e5b7de230616d673bd3498ac79' if self._country_code() == 'BR' else 'cbb52e297bf39e0b49f4a60d001f4013'}:"
                    "compliance.signupTerms"
                )
            self.state.content_identifier = content_identifier
            logger.info(f"Content identifier: {self.state.content_identifier}")

        # Send Tealeaf for new page
        send_tealeaf_data(
            self.session,
            self.state.signup_url if self.state.signup_url else str(resp.url),
        )
        send_observability_emit(self.session, self.ba_token)

        if self.state.ec_token:
            # Browser trace sends signup-page Weasley logs and EC-token risk
            # beacons before phone/card submission.  Missing these correlates
            # with opaque OAS_ERROR/createMemberAccount buckets.
            send_weasley_log(
                self.session,
                self.state.ec_token,
                self.state.signup_url,
                [
                    "weasley_client_eligibility_check_success",
                    "WEASLEY_PAGE_INTERACTIVE_FPTI",
                    "WEASLEY_PREPARE_BILLING_PAGE_FPTI",
                    "weasley_payment_request_api_available",
                ],
                country=self._country_code(),
                lang=self._lang_code(),
            )
            send_device_fingerprint(
                self.session,
                self.state.ec_token,
                app_id="CHECKOUTUINODEWEB_ONBOARDING_LITE",
                referer=self.state.signup_url,
                wrapped=True,
            )

        # Send the initial GraphQL queries
        logger.info("Sending checkout session GraphQL queries...")
        try:
            self.session.graphql(
                "CookieBannerQuery",
                COOKIE_BANNER_QUERY,
                {},
            )
        except Exception as e:
            logger.warning(f"CookieBannerQuery failed: {e}")

        try:
            self.session.graphql(
                "InitialDataQuery",
                INITIAL_DATA_QUERY,
                {
                    "channel": "WEB",
                    "countryCode": self._country_code(),
                    "countryCodeAsString": self._country_code(),
                    "isBasl": False,
                    "isBaslAsString": "false",
                    "languageCode": self._lang_code(),
                    "token": self.state.ec_token or self.ba_token,
                },
            )
        except Exception as e:
            logger.warning(f"InitialDataQuery failed: {e}")

        try:
            self.session.graphql(
                "DeferredFeature",
                DEFERRED_FEATURE_QUERY,
                {
                    "integrationType": "XoSignupAuth",
                    "token": self.state.ec_token or self.ba_token,
                },
            )
        except Exception as e:
            logger.warning(f"DeferredFeature failed: {e}")

        try:
            self.session.graphql(
                "CheckoutSessionDataQuery",
                CHECKOUT_SESSION_DATA_QUERY,
                {"token": self.state.ec_token or self.ba_token},
            )
        except Exception as e:
            logger.warning(f"CheckoutSessionDataQuery failed: {e}")

        try:
            self.session.graphql(
                "GriffinMetadataQuery",
                GRIFFIN_METADATA_QUERY,
                {
                    "countryCode": self._country_code(),
                    "languageCode": self._lang_code(),
                    "shippingCountryCode": self._country_code(),
                },
            )
        except Exception as e:
            logger.warning(f"GriffinMetadataQuery failed: {e}")

        try:
            self.session.graphql(
                "SupportedFundingSourcesQuery",
                SUPPORTED_FUNDING_SOURCES_QUERY,
                {
                    "token": self.state.ec_token or self.ba_token,
                    "userCountry": self._country_code(),
                },
            )
        except Exception as e:
            logger.warning(f"SupportedFundingSourcesQuery failed: {e}")

        try:
            address_result = self.session.graphql(
                "AddressAutocompleteFromPostalCodeQuery",
                ADDRESS_AUTOCOMPLETE_FROM_POSTAL_CODE_QUERY,
                {
                    "country": self._country_code(),
                    "postalCode": self.address.postal_code,
                    "token": self.state.ec_token or self.ba_token,
                },
            )
            result_obj = address_result[0] if isinstance(address_result, list) else address_result
            normalized = result_obj.get("data", {}).get("addressNormalization") or {}
            if normalized:
                logger.info(
                    "Address normalized: {}, {}, {} {}",
                    normalized.get("line1"),
                    normalized.get("line2"),
                    normalized.get("city"),
                    normalized.get("state"),
                )
                self.address.street = normalized.get("line1") or self.address.street
                self.address.district = normalized.get("line2") or self.address.district
                self.address.city = normalized.get("city") or self.address.city
                self.address.state = normalized.get("state") or self.address.state
                self.address.postal_code = normalized.get("postalCode") or self.address.postal_code
        except Exception as e:
            logger.warning(f"AddressAutocompleteFromPostalCodeQuery failed: {e}")

    def _phase3_signup_and_2fa(self):
        """Submit the signup form and trigger 2FA SMS.

        Actual flow discovered from traffic capture:
        1. InitiateRiskBasedTwoFactorPhoneConfirmationMutation → sends SMS, returns authId + challengeId
        2. ConfirmRiskBasedTwoFactorPhoneConfirmationMutation → verifies OTP pin with authId + challengeId
        3. SignUpNewMemberMutation → creates account with all user data + card + address
        """
        logger.info("--- Phase 3: Signup form + 2FA ---")

        # Send Tealeaf to simulate form interaction
        signup_url = self.state.signup_url or "https://www.paypal.com/checkoutweb/signup"
        send_tealeaf_data(self.session, signup_url)

        token = self.state.ec_token or self.ba_token

        # Step 1/2: Send SMS and confirm OTP. If the OTP is wrong, the
        # operator can either retry a code for the same phone or enter a new
        # phone number to trigger a fresh challenge.
        self._confirm_phone_with_retry(token, signup_url)

        # Step 3: Sign up new member with all user data. If PayPal rejects the
        # card at addCard/validate.fi/cardNumber, fetch a new generated
        # Visa/MasterCard and submit SignUpNewMember again.
        self._signup_with_card_retry(token, signup_url)

        if not self.state.euat_token:
            raise RuntimeError(
                "Signup failed: no access token obtained. "
                "Cannot proceed to authorization without authentication."
            )

        # The UK capture inserts checkoutweb/drop between signup and Hermes.
        # Do this only after SignUpNewMember has issued the member EUAT.
        self._load_checkoutweb_drop()

        self._persist_euat_cookie()
        if self.state.user_id:
            logger.info("Post-signup buyer.userId present: {}", self.state.user_id)
        else:
            logger.warning(
                "Post-signup buyer.userId missing; Hermes review page must provide uplift before authorize"
            )

        # Send analytics for signup completion
        send_analytics_ts(
            self.session,
            "main:billing:hagrid:billingwithoutpurchase:member:review",
            self.ba_token,
            ec_token=self.state.ec_token,
            user_id=self.state.user_id,
            country=self._country_code(),
            locale=self._locale_code(),
        )

    def _build_hermes_url(self, *, billing_lite: bool = False) -> str:
        """Build the captured UK Hermes URL with stable parameter ordering."""
        params = [
            ("ssrt", self.state.ssrt),
            ("ul", "1"),
            ("modxo_redirect_reason", "guest_user"),
            ("locale.x", self._locale_code()),
            ("country.x", self._country_code()),
            ("ba_token", self.ba_token),
            ("token", self.state.ec_token),
            ("rcache", "1"),
            ("fromSignupLite", "true"),
            ("addFIContingency", "noretry" if self.prefer_skip_addfi else "retry"),
            ("redirectToHermes", "true"),
            ("fallback", "1"),
            ("reason", HERMES_GUEST_REASON),
        ]
        if billing_lite:
            params.append(("billingLite", "1"))
        return "https://www.paypal.com/webapps/hermes?" + urllib.parse.urlencode(
            [(key, value) for key, value in params if value]
        )

    def _phase4_authorize(self) -> dict:
        """Send the final authorize mutation to approve the billing agreement."""
        logger.info("--- Phase 4: Final authorization ---")

        hermes_base_url = self._build_hermes_url()
        review_referer = self._build_hermes_url(billing_lite=True)
        review_url = f"{review_referer}#/billingweb/review"

        # Browser trace shows that Hagrid/Hermes is actually loaded before the
        # authorize mutation. This GET binds the EUAT/cookies to a buyer
        # context; without it GraphQL authorize can return BUYER_NOT_SET.
        try:
            logger.info("Loading Hermes/Hagrid review context...")
            base_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Referer": self.state.signup_url,
                "Upgrade-Insecure-Requests": "1",
            }
            review_resp = self.session.get(hermes_base_url, headers=base_headers)
            if review_resp.status_code in (301, 302, 303, 307, 308):
                location = review_resp.headers.get("Location", "")
                if location:
                    location = urllib.parse.urljoin(hermes_base_url, location)
                    logger.info(f"Following Hermes review redirect: {location[:140]}...")
                    review_resp = self.session.get(
                        location,
                        headers={**base_headers, "Referer": hermes_base_url},
                    )
            logger.info(
                "Hermes/Hagrid review loaded: {} bytes={}",
                review_resp.status_code,
                len(review_resp.content),
            )
            self.state.hermes_url = hermes_base_url
            self.state.hermes_loaded = review_resp.status_code == 200
            # Prefer cookie/page EUAT if Hermes refreshed it.
            self.session._sync_state_cookies()
            if self.state.euat_token:
                self._persist_euat_cookie()

            if not self.state.user_id:
                page_user_id = self._extract_user_id_from_text(review_resp.text)
                if page_user_id:
                    self.state.user_id = page_user_id
                    logger.info(f"User ID from Hermes page: {self.state.user_id}")
        except Exception as e:
            logger.warning(f"Loading Hermes/Hagrid review context failed: {e}")

        # Send Tealeaf for the review page
        send_tealeaf_data(self.session, review_url)

        # Hard gate: Guest->Member uplift must complete before authorize.
        self._assert_identity_uplift("pre-authorize")
        if not self.state.ec_token:
            logger.warning("EC token missing; authorize will fall back to BA token")

        # The critical authorize mutation. UK HAR uses original EC token.
        billing_agreement_id = self.state.ec_token or self.ba_token
        logger.info(
            "Authorizing billing agreement: {}",
            sanitize_for_log({"billingAgreementId": billing_agreement_id})["billingAgreementId"],
        )
        balance_pref = "OPT_OUT" if self._country_code() == "BR" else "OPT_IN"

        def send_authorize():
            if not self.state.euat_token:
                raise RuntimeError("EUAT missing at authorize call")
            return self.session.graphql(
                "authorize",
                AUTHORIZE_BILLING_MUTATION,
                {
                    "billingAgreementId": billing_agreement_id,
                    "fundingPreference": {
                        "balancePreference": balance_pref,
                    },
                    "legalAgreements": {},
                },
                extra_headers={
                    "Referer": review_referer,
                    "X-App-Name": "checkoutuinodeweb",
                    "PayPal-Client-Context": None,
                    "PayPal-Client-Metadata-Id": self.state.paypal_client_metadata_id,
                    "X-Country": None,
                    "X-Locale": None,
                    "X-PayPal-Internal-EUAT": self.state.euat_token,
                },
                batched=True,
                endpoint="https://www.paypal.com/graphql/",
            )

        result = send_authorize()
        if self._has_buyer_not_set(result):
            logger.warning("authorize returned BUYER_NOT_SET; reloading billingLite review context and retrying once...")
            try:
                self.session.get(
                    review_referer,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": hermes_base_url,
                        "Upgrade-Insecure-Requests": "1",
                    },
                )
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Reloading billingLite review context failed: {e}")
            result = send_authorize()

        logger.info(
            "Authorization result (sanitized): {}",
            json.dumps(sanitize_for_log(result), ensure_ascii=False, indent=2)[:1000],
        )

        # Extract return URL and user ID from response
        try:
            result_obj = result[0] if isinstance(result, list) else result
            billing_data = result_obj.get("data", {}).get("billing", {})
            auth_data = billing_data.get("authorize") if isinstance(billing_data, dict) else None
            if not isinstance(auth_data, dict):
                errors = result_obj.get("errors") if isinstance(result_obj, dict) else None
                logger.error(
                    "Authorization failed: authorize is empty. Errors: {}",
                    json.dumps(sanitize_for_log(errors or []), ensure_ascii=False, indent=2),
                )
                return {
                    "status": "error",
                    "error": "authorize returned empty result",
                    "raw_response": result,
                }
            self.state.return_url = auth_data["returnURL"]["href"]
            self.state.user_id = auth_data["buyer"]["userId"]
            ba_token_resp = auth_data["billingAgreementToken"]

            logger.success(
                "Billing Agreement Token: {}",
                sanitize_for_log({"billingAgreementToken": ba_token_resp})["billingAgreementToken"],
            )
            logger.success(f"Payment Action: {auth_data['paymentAction']}")
            logger.success(f"Buyer User ID: {self.state.user_id}")
            logger.success("Return URL: <redacted>")

            final_redirect_url = ""
            try:
                logger.info("Following merchant return URL...")
                return_resp = self.session.get(
                    self.state.return_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Referer": review_url,
                        "Upgrade-Insecure-Requests": "1",
                    },
                )
                for _ in range(8):
                    if return_resp.status_code not in (301, 302, 303, 307, 308):
                        break
                    location = return_resp.headers.get("Location", "")
                    if not location:
                        break
                    final_redirect_url = urllib.parse.urljoin(str(return_resp.url), location)
                    return_resp = self.session.get(
                        final_redirect_url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Referer": str(return_resp.url),
                            "Upgrade-Insecure-Requests": "1",
                        },
                    )
                if not final_redirect_url:
                    final_redirect_url = str(return_resp.url)
                logger.success("Final merchant URL: <redacted>")
            except Exception as e:
                logger.warning(f"Following merchant return URL failed: {e}")

            # Send final analytics
            send_analytics_ts(
                self.session,
                "main:billing:hagrid:billingwithoutpurchase:member:submitButtonFullEvent",
                self.ba_token,
                ec_token=self.state.ec_token,
                user_id=self.state.user_id,
                event="cl",
                country=self._country_code(),
                locale=self._locale_code(),
            )

            return {
                "status": "success",
                "ba_token": ba_token_resp,
                "ec_token": self.state.ec_token,
                "user_id": self.state.user_id,
                "return_url": self.state.return_url,
                "final_redirect_url": final_redirect_url,
                "payment_action": auth_data["paymentAction"],
                **self._classify_merchant_result(final_redirect_url),
                "buyer_mode": self.buyer_mode,
                "identity_elevation": dict(self.identity_elevation),
            }
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to parse authorization response: {e}")
            return {
                "status": "error",
                "error": str(e),
                "raw_response": result,
            }

    @staticmethod
    def _classify_merchant_result(final_redirect_url: str) -> dict:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(final_redirect_url or "").query)
        redirect_status = str((query.get("redirect_status") or [""])[0]).lower()
        success_values = {"success", "succeeded", "confirmed", "complete", "completed"}
        # PayPal can authorize the Billing Agreement before the merchant's
        # checkout/Stripe verification finishes.  Preserve that intermediate
        # state instead of collapsing it into a generic "authorized" result.
        # The task layer still exposes ``authorized`` for terminal-state
        # compatibility, while ``result.settlement_status`` carries the
        # precise merchant state to the UI and callers.
        pending_values = {
            "pending",
            "processing",
            "requires_action",
            "requires_verification",
            "requires_confirmation",
        }
        if redirect_status in success_values:
            settlement_status = "confirmed"
        elif redirect_status in pending_values:
            settlement_status = "pending_verification"
        else:
            settlement_status = "authorized"
        verification_url = str((query.get("return_url") or [""])[0])
        return {
            "verification_url": verification_url,
            "pending_url": "" if settlement_status == "confirmed" else final_redirect_url,
            "redirect_status": redirect_status,
            "settlement_status": settlement_status,
        }
