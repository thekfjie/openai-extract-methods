#!/usr/bin/env python3
"""Deterministic local fixture for the OpenAI3 headless protocol.

This exercises the HAR-shaped state machine without contacting external
registration, mail, proxy, or account services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Scenario:
    name: str
    create_result: str
    reauth_result: str = ""
    login_otp: bool = True
    no_commit: bool = False
    cloudflare: bool = False


def simulate(scenario: Scenario) -> dict:
    events: list[str] = ["passwordless_signup", "otp_received", "otp_accepted"]
    if scenario.cloudflare:
        return {"scenario": scenario.name, "events": events + ["cloudflare_challenge"], "at": False}
    events.append("about_you")
    if scenario.no_commit:
        return {"scenario": scenario.name, "events": events + ["no_commit_stop"], "at": False}

    events.append("create_account")
    if scenario.create_result == "ok":
        events.extend(["callback", "session"])
        return {"scenario": scenario.name, "events": events, "at": True}
    if scenario.create_result in {"409", "500"}:
        events.append("existing_login")
        if scenario.login_otp:
            events.extend(["login_otp_received", "login_otp_accepted", "callback", "session"])
            return {"scenario": scenario.name, "events": events, "at": True}
        return {"scenario": scenario.name, "events": events + ["login_otp_timeout"], "at": False}
    if scenario.create_result == "registration_disallowed":
        events.append("registration_disallowed")
        if scenario.reauth_result == "callback":
            events.extend(["reauthorize", "callback", "session"])
            return {"scenario": scenario.name, "events": events, "at": True}
        events.append("reauthorize_email_verification")
        events.append("existing_login")
        if scenario.login_otp:
            events.extend(["login_otp_received", "login_otp_accepted", "callback", "session"])
            return {"scenario": scenario.name, "events": events, "at": True}
        return {"scenario": scenario.name, "events": events + ["login_otp_timeout"], "at": False}
    return {"scenario": scenario.name, "events": events + ["terminal_error"], "at": False}


def run_matrix(scenarios: Iterable[Scenario]) -> list[dict]:
    results = [simulate(item) for item in scenarios]
    assert all("create_account" not in result["events"] or result["events"].count("create_account") == 1 for result in results)
    return results


if __name__ == "__main__":
    matrix = [
        Scenario("normal", "ok"),
        Scenario("registration_disallowed_direct_callback", "registration_disallowed", "callback"),
        Scenario("registration_disallowed_login_recovery", "registration_disallowed", "email_verification"),
        Scenario("create_409_login_recovery", "409"),
        Scenario("create_500_login_otp_timeout", "500", login_otp=False),
        Scenario("no_commit", "ok", no_commit=True),
        Scenario("cloudflare", "ok", cloudflare=True),
    ]
    for result in run_matrix(matrix):
        print(f"{result['scenario']}: at={result['at']} events={' -> '.join(result['events'])}")
