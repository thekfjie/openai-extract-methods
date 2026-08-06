from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from uc_signup import BrowserBlocked, PhoneRetry, SignupBot


class FakeElement:
    def __init__(self, **attributes):
        self.attributes = attributes
        self.id = attributes.get("id", "fake-element")

    def get_attribute(self, name):
        return self.attributes.get(name, "")


class EditableElement(FakeElement):
    def __init__(self, **attributes):
        super().__init__(**attributes)
        self.keys = []

    def send_keys(self, *keys):
        self.keys.extend(keys)


class ReactiveDateSegment(EditableElement):
    def __init__(self, part, value):
        super().__init__(**{"data-type": part, "aria-valuenow": str(value), "id": part})

    def send_keys(self, *keys):
        super().send_keys(*keys)
        for key in keys:
            if key == "\ue013":  # Selenium Keys.ARROW_UP
                self.attributes["aria-valuenow"] = str(int(self.attributes["aria-valuenow"]) + 1)
            elif key == "\ue015":  # Selenium Keys.ARROW_DOWN
                self.attributes["aria-valuenow"] = str(int(self.attributes["aria-valuenow"]) - 1)


class SignupInputClassificationTests(unittest.TestCase):
    def setUp(self):
        self.bot = SignupBot.__new__(SignupBot)

    def test_telephone_otp_is_not_phone_number_input(self):
        element = FakeElement(
            name="code",
            type="tel",
            inputmode="numeric",
            maxlength="6",
            autocomplete="one-time-code",
        )
        self.assertFalse(self.bot.looks_like_phone_input(element))
        self.assertTrue(self.bot.looks_like_code_input(element))

    def test_phone_number_field_is_not_otp(self):
        element = FakeElement(
            name="phoneNumberInput",
            type="tel",
            autocomplete="tel",
        )
        self.assertTrue(self.bot.looks_like_phone_input(element))
        self.assertFalse(self.bot.looks_like_code_input(element))

    def test_phone_form_whatsapp_copy_is_not_a_rejection(self):
        self.bot.visible_text = lambda: "Phone number We'll send a one-time code. Send a WhatsApp message"
        self.bot.phone_verification_rate_limited_reason = lambda: ""
        self.assertEqual(self.bot.phone_rejection_reason(), "")

    def test_whatsapp_code_field_is_not_reused_as_phone_input(self):
        self.bot.visible_text = lambda: (
            "Check your phone Enter the verification code we just sent to "
            "+1 (873) 478-4204 on WhatsApp Code Continue Resend WhatsApp message"
        )
        self.bot.visible_elements = lambda selector: [FakeElement(type="tel", inputmode="numeric")]
        self.bot.d = type("Driver", (), {"title": "Check your phone"})()

        self.assertFalse(self.bot.phone_input_visible())
        self.assertTrue(self.bot.whatsapp_code_prompt_visible())

    def test_sms_failure_switched_to_whatsapp_is_a_rejection(self):
        self.bot.visible_text = lambda: "We couldn't send a text message to this phone number, so we switched to WhatsApp."
        self.bot.phone_verification_rate_limited_reason = lambda: ""
        self.assertTrue(self.bot.phone_rejection_reason().startswith("whatsapp_only:"))

    def test_sms_failure_switched_to_whatsapp_is_classified_for_hold(self):
        self.bot.visible_text = lambda: "We couldn't send a text message, so we switched to WhatsApp."
        self.bot.phone_verification_rate_limited_reason = lambda: ""
        self.bot.whatsapp_code_prompt_visible = lambda: False
        self.assertTrue(self.bot.whatsapp_verification_reason().startswith("whatsapp_only:"))

    def test_korean_password_error_can_switch_to_one_time_code(self):
        clicked_terms = []
        completed_steps = []
        self.bot.visible_text = lambda: "비밀번호 incorrect email address or password 또는 일회용 코드로 로그인"
        self.bot.click_text_element = lambda terms, wait_seconds=0: clicked_terms.extend(terms) or True
        self.bot.poll_email = lambda email: "123456"
        self.bot._step = lambda label, action: (completed_steps.append(label), action())
        self.bot.fill_code_input = lambda code: self.assertEqual(code, "123456")
        self.bot.click_primary_action = lambda: True

        self.assertTrue(self.bot.try_one_time_code_login("person@example.com"))
        self.assertIn("일회용 코드로 로그인", clicked_terms)
        self.assertEqual(completed_steps, ["一次性登录邮箱验证码"])

    def test_email_input_matches_detects_cleared_react_field(self):
        expected = "person@example.com"
        email_field = FakeElement(id="email-input", value="")
        self.bot.visible_elements = lambda selector: [email_field] if "email" in selector else []

        self.assertFalse(self.bot.email_input_matches(expected))

        email_field.attributes["value"] = "Person@Example.com"
        self.assertTrue(self.bot.email_input_matches(expected))

    def test_password_input_matches_detects_cleared_react_field(self):
        password_field = FakeElement(id="password-input", value="")
        self.bot.signup_password = "ValidPassword123!"
        self.bot.visible_elements = lambda selector: [password_field] if "password" in selector else []

        self.assertFalse(self.bot.password_input_matches())

        password_field.attributes["value"] = self.bot.signup_password
        self.assertTrue(self.bot.password_input_matches())

    def test_generic_phone_stall_stops_account_instead_of_switching_number(self):
        class Driver:
            current_url = "https://auth.openai.com/add-phone"

        self.bot.d = Driver()
        self.bot.wait_ready = lambda timeout=2: None
        self.bot.phone_verification_rate_limited_reason = lambda: ""
        self.bot.whatsapp_verification_reason = lambda: ""
        self.bot.phone_rejection_reason = lambda: ""
        self.bot.code_input_visible = lambda: False
        self.bot.password_input_visible = lambda: False
        self.bot.capture_network_diagnostics = lambda label="": None
        self.bot.phone_input_validation_hint = lambda: ""

        from unittest.mock import patch
        with patch("uc_signup.time.time", side_effect=[0, 31]):
            with self.assertRaises(PhoneRetry) as raised:
                self.bot.wait_code_input_after_phone()

        self.assertTrue(raised.exception.hold_phone)
        self.assertTrue(raised.exception.stop_account)
        self.assertFalse(raised.exception.return_to_phone)


class SignupEntryGuardTests(unittest.TestCase):
    def setUp(self):
        self.bot = SignupBot.__new__(SignupBot)

    def test_apple_login_is_third_party_auth_page(self):
        self.bot.d = type(
            "Driver",
            (),
            {"current_url": "https://appleid.apple.com/auth/authorize?client_id=example"},
        )()

        self.assertTrue(self.bot.third_party_auth_page())
        self.assertFalse(self.bot.third_party_auth_page("https://auth.openai.com/create-account"))

    def test_apple_redirect_retries_clean_tab_then_stops_without_scanning_buttons(self):
        class Driver:
            current_url = "https://appleid.apple.com/auth/authorize"
            title = "Sign in with Apple Account"

        self.bot.d = Driver()
        self.bot.launch = lambda: None
        self.bot.ensure_cf_clearance = lambda url: None
        self.bot.apply_cf_clearance = lambda: None
        self.bot._discard_signup_checkpoint = Mock()
        self.bot._open_clean_signup_tab = Mock()
        self.bot.click_optional = Mock()
        self.bot.email_input_visible = Mock(return_value=False)

        with self.assertRaises(BrowserBlocked) as raised:
            self.bot.open_signup_email_form()

        self.assertIn("第三方登录页", str(raised.exception))
        self.assertEqual(self.bot._open_clean_signup_tab.call_count, 2)
        self.bot.click_optional.assert_not_called()

    def test_unknown_external_page_stops_without_scanning_buttons(self):
        class Driver:
            current_url = "https://example.net/unexpected"
            title = "Unexpected"

        self.bot.d = Driver()
        self.bot.launch = lambda: None
        self.bot.ensure_cf_clearance = lambda url: None
        self.bot.apply_cf_clearance = lambda: None
        self.bot._discard_signup_checkpoint = Mock()
        self.bot._open_clean_signup_tab = Mock()
        self.bot.click_optional = Mock()
        self.bot.email_input_visible = Mock(return_value=False)

        with self.assertRaises(BrowserBlocked) as raised:
            self.bot.open_signup_email_form()

        self.assertIn("非 OpenAI 页面", str(raised.exception))
        self.bot.click_optional.assert_not_called()


class SignupProfileAgeTests(unittest.TestCase):
    def setUp(self):
        self.bot = SignupBot.__new__(SignupBot)
        self.bot.current_email = "person@example.com"
        self.bot.requested_email = self.bot.current_email
        self.bot.display_name = "Person Example"
        self.bot.signup_age = "30"

    def test_segmented_birthdate_controls_are_filled(self):
        expected_year, expected_month, expected_day = map(int, self.bot.profile_birthdate().split("-"))
        month = ReactiveDateSegment("month", expected_month + 1)
        day = ReactiveDateSegment("day", expected_day - 1)
        year = ReactiveDateSegment("year", expected_year + 2)
        segments = [month, day, year]
        hidden = FakeElement(value=self.bot.profile_birthdate())

        class Driver:
            def find_elements(self, by, selector):
                return [hidden] if "name=birthday" in selector else []

        self.bot.d = Driver()
        self.bot.visible_elements = lambda selector: (
            [segment for segment in segments if f"data-type='{segment.attributes['data-type']}'" in selector]
            if "data-type='" in selector
            else segments if "spinbutton" in selector else []
        )

        class FakeAction:
            def move_to_element(self, element):
                return self

            def click(self):
                return self

            def perform(self):
                return None

        import uc_signup
        original = uc_signup.ActionChains
        uc_signup.ActionChains = lambda driver: FakeAction()
        try:
            self.assertTrue(self.bot.fill_profile_birthdate_segments())
        finally:
            uc_signup.ActionChains = original

        self.assertEqual(int(month.get_attribute("aria-valuenow")), expected_month)
        self.assertEqual(int(day.get_attribute("aria-valuenow")), expected_day)
        self.assertEqual(int(year.get_attribute("aria-valuenow")), expected_year)

    def test_segmented_birthdate_rejects_wrong_hidden_value(self):
        expected_year, expected_month, expected_day = map(int, self.bot.profile_birthdate().split("-"))
        segments = [
            ReactiveDateSegment("year", expected_year),
            ReactiveDateSegment("month", expected_month),
            ReactiveDateSegment("day", expected_day),
        ]
        hidden = FakeElement(value="2022-04-14")

        class Driver:
            def find_elements(self, by, selector):
                return [hidden]

        self.bot.d = Driver()
        self.bot.visible_elements = lambda selector: (
            [segment for segment in segments if f"data-type='{segment.attributes['data-type']}'" in selector]
            if "data-type='" in selector else segments
        )
        with patch("uc_signup.ActionChains") as actions:
            actions.return_value.move_to_element.return_value.click.return_value.perform.return_value = None
            self.assertFalse(self.bot.fill_profile_birthdate_segments())

    def test_age_page_without_birthdate_does_not_submit(self):
        name = EditableElement(id="name", name="name", value="Person Example")
        self.bot.d = type(
            "Driver",
            (),
            {"current_url": "https://auth.openai.com/about-you", "title": "Let's confirm your age"},
        )()
        self.bot.visible_text = lambda: "Let's confirm your age Name Birthday Finish creating account"
        self.bot.visible_elements = lambda selector: [name] if selector == "input[name=name]" else []
        self.bot.fill_profile_birthdate_segments = Mock(return_value=False)
        self.bot.click_primary_action = Mock()
        self.bot.confirm_profile_age_if_present = Mock()

        self.assertFalse(self.bot.fill_profile_if_present())
        self.assertIn("要求生日", self.bot._profile_fill_error)
        self.bot.click_primary_action.assert_not_called()

class ChooseAccountLabelTests(unittest.TestCase):
    def test_english_select_account_scores(self):
        from uc_signup import choose_account_label_score
        score = choose_account_label_score(
            "Scott Anderson\nScottAnderson3615@outlook.com\nSelect account",
            email="ScottAnderson3615@outlook.com",
            display_name="Scott Anderson",
        )
        self.assertGreaterEqual(score, 100)

    def test_japanese_welcome_back_account_card_scores(self):
        from uc_signup import choose_account_label_score
        score = choose_account_label_score(
            "Scott Anderson\nScottAnderson3615@outlook.com",
            email="ScottAnderson3615@outlook.com",
            display_name="Scott Anderson",
        )
        self.assertGreaterEqual(score, 100)
        jp = choose_account_label_score(
            "このアカウントを使用\nScott Anderson",
            email="ScottAnderson3615@outlook.com",
            display_name="Scott Anderson",
        )
        self.assertGreaterEqual(jp, 80)

    def test_create_account_is_excluded(self):
        from uc_signup import choose_account_label_score
        self.assertEqual(
            choose_account_label_score(
                "新しいアカウントを作成",
                email="ScottAnderson3615@outlook.com",
            ),
            0,
        )

    def test_delete_account_is_excluded(self):
        from uc_signup import choose_account_label_score
        self.assertEqual(
            choose_account_label_score(
                "アカウント OvilaRenneke6333@outlook.com を削除する",
                email="OvilaRenneke6333@outlook.com",
                display_name="Ovila Renneke",
            ),
            0,
        )
        self.assertGreaterEqual(
            choose_account_label_score(
                "OR アカウントを選択する Ovila Renneke OvilaRenneke6333@outlook.com",
                email="OvilaRenneke6333@outlook.com",
                display_name="Ovila Renneke",
            ),
            100,
        )

    def test_click_account_button_prefers_email_match(self):
        from uc_signup import SignupBot

        class FakeEl:
            def __init__(self, text, el_id):
                self._text = text
                self.id = el_id
                self.clicked = False

            @property
            def text(self):
                return self._text

            def get_attribute(self, name):
                return ""

            def is_displayed(self):
                return True

        create = FakeEl("新しいアカウントを作成", "1")
        account = FakeEl("Scott Anderson\nScottAnderson3615@outlook.com", "2")
        delete = FakeEl("アカウント ScottAnderson3615@outlook.com を削除する", "4")
        other = FakeEl("Continue with Google", "3")

        class FakeDriver:
            def __init__(self):
                self.current_url = "https://auth.openai.com/choose-an-account"
                self.title = "Choose an account"

            def find_elements(self, by, selector):
                return [create, other, delete, account]

            def execute_script(self, *args, **kwargs):
                return None

        bot = SignupBot.__new__(SignupBot)
        bot.d = FakeDriver()
        bot.current_email = "ScottAnderson3615@outlook.com"
        bot.requested_email = bot.current_email
        bot.display_name = "Scott Anderson"
        bot.wait_ready = lambda timeout=10: None
        bot._sleep = lambda seconds: None
        bot.click_text_element = lambda *a, **k: False
        bot.is_error_page = lambda: False
        bot.transient_auth_error_visible = lambda: False
        bot.auth_session_ended_visible = lambda: False
        bot.visible_text = lambda: "Choose an account Scott Anderson ScottAnderson3615@outlook.com"

        moves = []

        class FakeAction:
            def move_to_element(self, element):
                moves.append(element)
                return self

            def pause(self, seconds):
                return self

            def click(self):
                moves[-1].clicked = True
                # Leave choose-account after a good click.
                bot.d.current_url = "https://auth.openai.com/log-in/password"
                return self

            def perform(self):
                return None

        import uc_signup
        original = uc_signup.ActionChains
        uc_signup.ActionChains = lambda driver: FakeAction()
        try:
            bot._click_account_button()
        finally:
            uc_signup.ActionChains = original

        self.assertTrue(account.clicked)
        self.assertFalse(create.clicked)
        self.assertFalse(delete.clicked)


class SignupDoneAndStageHelpersTests(unittest.TestCase):
    def test_failure_text_redacts_account_secrets(self):
        from uc_signup import SignupBot

        redacted = SignupBot._redact_failure_text(
            "person@example.com pass=Secret! code 123456 phone +1 (555) 123-4567",
            email="person@example.com",
            password="Secret!",
        )
        self.assertNotIn("person@example.com", redacted)
        self.assertNotIn("Secret!", redacted)
        self.assertNotIn("123456", redacted)
        self.assertNotIn("555", redacted)

    def test_web_session_import_is_marked_rt_pending(self):
        import tempfile
        from pathlib import Path
        import uc_signup

        bot = SignupBot("person@example.com")
        bot.extract_chatgpt_web_session = lambda email="": {
            "access_token": "web-access-secret",
            "session_token": "web-session-secret",
            "credentialKind": "chatgpt_web_session",
        }
        fake_client = Mock()
        fake_client.import_openai_oauth.return_value = {"imported": True, "hasAccessToken": True}
        with tempfile.TemporaryDirectory() as tmp:
            old = uc_signup.EMAIL_STAGE_PATH
            uc_signup.EMAIL_STAGE_PATH = Path(tmp) / "stage.json"
            try:
                with patch("uc_signup.OpusMailClient.from_project", return_value=fake_client):
                    result = bot.persist_chatgpt_web_session("person@example.com")
                record = uc_signup.email_stage_record("person@example.com")
            finally:
                uc_signup.EMAIL_STAGE_PATH = old

        self.assertTrue(result["imported"])
        self.assertEqual(record["flowStage"], "web_session_saved_rt_pending")
        self.assertTrue(record["webAccessTokenStoredInMailAdmin"])
        self.assertFalse(record["oauthRefreshTokenStoredInMailAdmin"])

    def test_signup_identity_uses_email_name_and_bounded_stable_age(self):
        from uc_signup import signup_age, signup_display_name

        email = "DylanMaxwell65588W2+ad12@outlook.com"
        self.assertEqual(signup_display_name(email), "Dylan Maxwell")
        age = int(signup_age(email))
        self.assertGreaterEqual(age, 20)
        self.assertLessEqual(age, 50)
        self.assertEqual(signup_age(email), signup_age(email))

    def test_signup_identity_falls_back_to_large_stable_name_pool(self):
        from uc_signup import FALLBACK_NAME_POOL, signup_display_name

        self.assertGreater(len(FALLBACK_NAME_POOL), 100)
        email = "9837465+noise@outlook.com"
        self.assertIn(signup_display_name(email), FALLBACK_NAME_POOL)
        self.assertEqual(signup_display_name(email), signup_display_name(email))

    def test_profile_age_confirmation_clicks_exact_ok(self):
        import uc_signup
        from uc_signup import SignupBot

        class Button:
            text = "OK"

            def __init__(self):
                self.clicked = False

            def get_attribute(self, name):
                return ""

            def is_displayed(self):
                return True

            def is_enabled(self):
                return True

            def click(self):
                self.clicked = True

        class Dialog:
            def __init__(self, button):
                self.button = button

            def find_elements(self, by, selector):
                return [self.button]

        class Action:
            def __init__(self, driver):
                self.button = None

            def move_to_element(self, button):
                self.button = button
                return self

            def click(self):
                self.button.click()
                return self

            def perform(self):
                return None

        button = Button()
        bot = SignupBot("person@example.com")
        bot.d = object()
        bot.visible_elements = lambda selector: [Dialog(button)]
        original = uc_signup.ActionChains
        uc_signup.ActionChains = Action
        try:
            self.assertTrue(bot.confirm_profile_age_if_present(timeout=0.1))
        finally:
            uc_signup.ActionChains = original
        self.assertTrue(button.clicked)

    def test_registration_transition_waits_for_page_kind_change(self):
        from uc_signup import SignupBot

        bot = SignupBot("person@example.com")
        kinds = iter(("email", "email", "password"))
        bot.wait_ready = lambda timeout=2: None
        bot.signup_done = lambda: False
        bot.classify_auth_page = lambda: next(kinds)
        bot._sleep = lambda seconds: None

        self.assertEqual(bot.wait_registration_transition("email", timeout=5), "password")

    def test_email_code_resend_includes_vietnamese_control(self):
        from uc_signup import SignupBot

        bot = SignupBot("person@example.com")
        captured = []
        bot.click_text_element = lambda labels, wait_seconds=0: captured.extend(labels) or True
        bot._sleep = lambda seconds: None

        self.assertTrue(bot.request_email_code_resend())
        self.assertIn("Gửi lại email", captured)

    def test_email_code_resend_starts_a_new_timestamp_batch(self):
        from uc_signup import SignupBot

        bot = SignupBot("person@example.com")
        bot.click_text_element = lambda labels, wait_seconds=0: True
        bot._sleep = lambda seconds: None

        with patch("uc_signup.time.time", return_value=1234.75):
            self.assertTrue(bot.request_email_code_resend())

        self.assertEqual(bot.email_code_not_before["person@example.com"], 1234.0)

    def test_naive_mail_timestamp_is_treated_as_beijing_time(self):
        import uc_signup
        from uc_signup import SignupBot

        bot = SignupBot("person@example.com")
        bot.d = None
        bot.start_email_code_batch(
            "person@example.com",
            requested_at=uc_signup.datetime(2026, 8, 3, 2, 49, 7, tzinfo=uc_signup.BEIJING_TZ).timestamp(),
        )
        replies = iter((
            {
                "item": {
                    "id": "old-message",
                    "date": "2026-08-03 02:49:06",
                    "verificationCode": "111111",
                }
            },
            {
                "item": {
                    "id": "new-message",
                    "date": "2026-08-03 02:49:08",
                    "verificationCode": "222222",
                }
            },
        ))
        bot.request_email_code_resend = lambda email=None: False

        with (
            patch("uc_signup.api", side_effect=lambda *args, **kwargs: next(replies)),
            patch("uc_signup.time.sleep", return_value=None),
        ):
            self.assertEqual(bot.poll_email("person@example.com"), "222222")

        self.assertNotIn("111111", bot.used_email_codes)
        self.assertIn("222222", bot.used_email_codes)

    def test_fill_code_input_force_clears_controlled_input(self):
        from uc_signup import SignupBot

        class Driver:
            def __init__(self):
                self.script_calls = 0

            def execute_script(self, script, element):
                self.script_calls += 1
                element.value = ""

        class CodeInput(EditableElement):
            def __init__(self):
                super().__init__(maxlength="6")
                self.value = "999999"
                self.clear_calls = 0

            def clear(self):
                self.clear_calls += 1
                self.value = ""

            def send_keys(self, *keys):
                super().send_keys(*keys)
                if len(keys) == 1 and isinstance(keys[0], str) and keys[0].isdigit():
                    self.value += keys[0]

        class Action:
            def __init__(self, driver):
                pass

            def move_to_element(self, element):
                return self

            def click(self):
                return self

            def perform(self):
                return None

        element = CodeInput()
        bot = SignupBot("person@example.com")
        bot.d = Driver()
        bot.code_input_elements = lambda: [element]

        with patch("uc_signup.ActionChains", Action):
            bot.fill_code_input("123456")

        self.assertGreaterEqual(element.clear_calls, 1)
        self.assertEqual(bot.d.script_calls, 1)
        self.assertEqual(element.value, "123456")

    def test_password_create_account_url_is_not_profile_page(self):
        from uc_signup import registration_profile_url

        self.assertFalse(registration_profile_url("https://auth.openai.com/create-account/password"))
        self.assertTrue(registration_profile_url("https://auth.openai.com/create-account/about-you"))
        self.assertTrue(registration_profile_url("https://auth.openai.com/about-you"))

    def test_signup_done_rejects_auth_and_error_pages(self):
        from uc_signup import SignupBot

        class FakeDriver:
            def __init__(self, url, title=""):
                self.current_url = url
                self.title = title

        bot = SignupBot("person@example.com")
        bot.d = FakeDriver("https://auth.openai.com/about-you", "About you")
        bot.is_error_page = lambda: False
        bot.transient_auth_error_visible = lambda: False
        bot.auth_session_ended_visible = lambda: False
        self.assertFalse(bot.signup_done())

        bot.d = FakeDriver("https://chatgpt.com/", "不明なエラーが発生しました")
        self.assertFalse(bot.signup_done())

        bot.d = FakeDriver("https://chatgpt.com/", "ChatGPT")
        self.assertTrue(bot.signup_done())

    def test_clear_email_registration_completed(self):
        import tempfile
        from pathlib import Path
        import uc_signup

        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage.json"
            old = uc_signup.EMAIL_STAGE_PATH
            uc_signup.EMAIL_STAGE_PATH = stage
            try:
                uc_signup.mark_email_registration_completed("A@Example.com", password="x")
                self.assertTrue(uc_signup.email_registration_completed("a@example.com"))
                self.assertTrue(uc_signup.clear_email_registration_completed("A@Example.com", reason="auth error"))
                self.assertFalse(uc_signup.email_registration_completed("a@example.com"))
            finally:
                uc_signup.EMAIL_STAGE_PATH = old


if __name__ == "__main__":
    unittest.main()
