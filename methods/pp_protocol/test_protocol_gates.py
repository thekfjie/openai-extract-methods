#!/usr/bin/env python3
"""Minimal unit checks for protocol gates / locale helpers."""
from paypal.models import normalize_locale, normalize_phone, generate_user, generate_address
from paypal.flow import PayPalFlow
from paypal.models import UserInfo, CardInfo, BillingAddress, SessionState


def test_normalize_locale_gb():
    country, locale, lang = normalize_locale("GB")
    assert country == "GB"
    assert locale == "en_GB"
    assert lang == "en"


def test_normalize_locale_br():
    country, locale, lang = normalize_locale("BR")
    assert country == "BR"
    assert locale == "pt_BR"
    assert lang == "pt"


def test_normalize_phone_gb():
    full, local, cc = normalize_phone("+447529501310", default_country="GB")
    assert full.startswith("+44")
    assert cc == "+44"
    assert local == "7529501310"


def test_normalize_phone_br():
    full, local, cc = normalize_phone("+5591980133818", default_country="BR")
    assert full.startswith("+55")
    assert cc == "+55"
    assert local.startswith("91")


def test_generate_user_address_gb():
    user = generate_user("+447512345678", country="GB")
    addr = generate_address("GB")
    assert user.phone_country_code == "+44"
    assert user.cpf == ""
    assert addr.country == "GB"
    assert addr.postal_code


def test_identity_uplift_gate():
    user = UserInfo(
        first_name="A", last_name="B", email="a@b.com",
        phone="+4475", phone_local="75", phone_country_code="+44",
        password="x", dob="01/01/1990", cpf="", nationality="GB",
    )
    card = CardInfo(number="4111111111111111", expiry="01/2030", cvv="123")
    address = BillingAddress(street="1", house_number="", district="", city="London", state="", postal_code="SW1A 1AA", country="GB")
    flow = PayPalFlow(ba_token="BA-TEST", user=user, card=card, address=address, country="GB", locale="en_GB")
    try:
        flow._assert_identity_uplift("unit")
        raise AssertionError("expected RuntimeError for missing uplift")
    except RuntimeError as e:
        assert "euat_token" in str(e)
        assert "buyer.userId" in str(e)

    flow.state.euat_token = "S23-test-euat"
    flow.state.user_id = "JGY7KGM6TTXUY"
    flow.state.ec_token = "EC-TEST"
    flow._assert_identity_uplift("unit")


def test_signup_variables_gb_shape():
    user = generate_user("+447512345678", country="GB")
    card = CardInfo(number="5162928143859309", expiry="05/2030", cvv="926")
    address = generate_address("GB")
    flow = PayPalFlow(ba_token="BA-TEST", user=user, card=card, address=address, country="GB", locale="en_GB", ec_token="EC-TEST")
    variables = flow._build_signup_variables("EC-TEST")
    assert variables["country"] == "GB"
    assert variables["nationality"] == "GB"
    assert "identityDocument" not in variables
    assert variables["crsData"]["taxDetails"][0]["countryCode"] == "GB"
    assert "residentialAddress" in variables
    assert variables["contentIdentifier"].startswith("GB:en:")


def test_signup_variables_br_keeps_cpf():
    user = generate_user("+5591980133818", country="BR")
    card = CardInfo(number="4111111111111111", expiry="01/2030", cvv="123")
    address = generate_address("BR")
    flow = PayPalFlow(ba_token="BA-TEST", user=user, card=card, address=address, country="BR", locale="pt_BR", ec_token="EC-TEST")
    variables = flow._build_signup_variables("EC-TEST")
    assert variables["country"] == "BR"
    assert variables["identityDocument"]["type"] == "CPF"
    assert variables["crsData"] is None


if __name__ == "__main__":
    tests = [
        test_normalize_locale_gb,
        test_normalize_locale_br,
        test_normalize_phone_gb,
        test_normalize_phone_br,
        test_generate_user_address_gb,
        test_identity_uplift_gate,
        test_signup_variables_gb_shape,
        test_signup_variables_br_keeps_cpf,
    ]
    for fn in tests:
        fn()
        print(f"OK {fn.__name__}")
    print("ALL PASS")
