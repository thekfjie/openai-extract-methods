#!/usr/bin/env python3
"""PayPal Billing Agreement approval automation.

Usage:
    python main.py --ba-token BA-xxx --phone +4475xxxxxxxx --country GB --locale en_GB
    python main.py --ba-token BA-xxx --phone +5591xxxxxxxx --country BR
"""
import argparse
import json
import sys
from loguru import logger

from paypal.models import generate_user, generate_card, generate_address, normalize_locale
from paypal.flow import PayPalFlow
from paypal.proxy import build_proxy_config
from paypal.session import sanitize_for_log


def main():
    parser = argparse.ArgumentParser(
        description="PayPal Billing Agreement Approval Automation"
    )
    parser.add_argument(
        "--ba-token", required=True,
        help="Billing Agreement token (e.g. BA-3AX328361P111131W)"
    )
    parser.add_argument(
        "--phone", required=True,
        help="Phone number with country code (e.g. +447512345678 / +5591980133818)"
    )
    parser.add_argument(
        "--country",
        default="GB",
        help="Checkout country code (default: GB). Use BR for Brazil skeleton path.",
    )
    parser.add_argument(
        "--locale",
        default=None,
        help="Checkout locale (e.g. en_GB / pt_BR). Defaults from --country.",
    )
    parser.add_argument(
        "--ec-token",
        default=None,
        help="Optional pre-known EC token. Normally extracted during flow.",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--max-card-attempts",
        type=int,
        default=5,
        help="Max SignUpNewMember retries with fresh generated Visa/MasterCard when addCard fails",
    )
    parser.add_argument(
        "--allow-addfi-retry",
        action="store_true",
        help="Do not force addFIContingency=noretry (default is noretry / skip-addFI path)",
    )
    proxy_group = parser.add_mutually_exclusive_group()
    proxy_group.add_argument(
        "--proxy",
        dest="proxy_enabled",
        action="store_true",
        default=None,
        help="Enable configured outbound proxy for this run",
    )
    proxy_group.add_argument(
        "--no-proxy",
        dest="proxy_enabled",
        action="store_false",
        help="Disable outbound proxy for this run",
    )
    parser.add_argument(
        "--proxy-index",
        type=int,
        default=None,
        help="Use a specific configured proxy index (0-based). Default: random when proxy is enabled",
    )

    args = parser.parse_args()

    logger.remove()
    if args.debug:
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.add(sys.stderr, level="INFO")

    country, locale, lang = normalize_locale(args.country, args.locale)
    proxy_config = build_proxy_config(enabled=args.proxy_enabled, index=args.proxy_index)

    user = generate_user(args.phone, country=country)
    card = generate_card(proxy_url=proxy_config.url)
    address = generate_address(country=country)

    logger.info(f"User: {user.first_name} {user.last_name}")
    logger.info("Email: {}", sanitize_for_log({"email": user.email})["email"])
    logger.info("Phone: {}", sanitize_for_log({"phone": user.phone})["phone"])
    logger.info("Country/Locale/Lang: {} / {} / {}", country, locale, lang)
    if country == "BR":
        logger.info("CPF: <redacted>")
    logger.info("DOB: <redacted>")
    logger.info(
        "Card: {} exp={} cvv=<redacted>",
        sanitize_for_log({"cardNumber": card.number})["cardNumber"],
        card.expiry,
    )
    logger.info(
        "Address generated: {}, {}-{}",
        address.district or address.street,
        address.city,
        address.state or address.country,
    )
    logger.info(f"Proxy: {proxy_config.label}")

    flow = PayPalFlow(
        ba_token=args.ba_token,
        user=user,
        card=card,
        address=address,
        max_card_attempts=args.max_card_attempts,
        proxy_config=proxy_config,
        country=country,
        locale=locale,
        ec_token=args.ec_token,
        prefer_skip_addfi=not args.allow_addfi_retry,
    )

    result = flow.run()

    print("\n" + "=" * 60)
    print("RESULT:")
    print(json.dumps(sanitize_for_log(result), indent=2, ensure_ascii=False))
    print("=" * 60)

    if result.get("status") == "success":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
