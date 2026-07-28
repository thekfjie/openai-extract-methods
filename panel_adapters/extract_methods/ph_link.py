from __future__ import annotations
from typing import Any
from .paper_card import run_paper_card_extract

def run_ph_link_extract(access_token: str, *, checkout_proxy: str = "", promotion_proxy: str = "", use_promo: bool = True, timeout: int = 45) -> dict[str, Any]:
    result = run_paper_card_extract(
        access_token,
        billing_country="PH",
        currency="PHP",
        checkout_proxy=checkout_proxy,
        promotion_proxy=promotion_proxy if use_promo else checkout_proxy,
        checkout_proxy_country="PH",
        promotion_proxy_country="PH",
        timeout=timeout,
    )
    result["method"] = "ph_link"
    return result
