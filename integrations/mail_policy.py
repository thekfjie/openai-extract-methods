from __future__ import annotations

import re
import secrets
import string
from typing import Any


DEFAULT_GROUP_PLAN = {
    "mail_pool": "mail_pool",
    "domain_pool": "domain_pool",
    "oai_pending": "oai_pending",
    "oai_success": "oai_success",
    "oai_old": "oai_old",
    "grok_pending": "grok_pending",
    "grok_success": "grok_success",
    "grok_old": "grok_old",
    "badmail": "badmail",
}

# 与 Outlook 账号池目前使用的格式保持一致：FirstNameLastName + 4 位数字。
DOMAIN_NAME_FIRST_NAMES = (
    "Alex", "Andrew", "Anthony", "Ashley", "Brandon", "Brian", "Charles", "Christopher",
    "Daniel", "David", "Elizabeth", "Emily", "Gary", "James", "Jennifer", "Jessica",
    "John", "Joseph", "Kevin", "Laura", "Leslie", "Lindsay", "Michael", "Robert",
    "Sarah", "Steven", "Thomas", "William", "Adam", "Amy", "April", "Cassandra",
    "Erica", "Jamie", "Jerry", "Julian", "Kimberly", "Melinda", "Nicholas", "Ronald",
    "Terri", "Taylor", "Morgan", "Jordan", "Casey", "Riley",
)
DOMAIN_NAME_LAST_NAMES = (
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson",
    "Moore", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin",
    "Thompson", "Garcia", "Martinez", "Robinson", "Clark", "Rodriguez", "Lewis", "Lee",
    "Walker", "Hall", "Allen", "Young", "King", "Wright", "Scott", "Green", "Baker",
    "Adams", "Nelson", "Hill", "Ramirez", "Campbell", "Mitchell", "Carter", "Roberts",
    "Collins", "Stewart", "Sanchez", "Morris", "Rogers", "Reed", "Cook", "Morgan",
    "Bell", "Murphy", "Bailey", "Rivera", "Cooper", "Richardson", "Cox", "Howard",
    "Ward", "Torres", "Peterson", "Gray", "Ramirez", "James", "Watson", "Brooks",
    "Kelly", "Sanders", "Price", "Bennett", "Wood", "Barnes", "Ross", "Henderson",
    "Coleman", "Jenkins", "Perry", "Powell", "Long", "Patterson", "Hughes", "Flores",
    "Washington", "Butler", "Simmons", "Foster", "Gonzales", "Bryant", "Alexander", "Russell",
    "Griffin", "Diaz", "Hayes", "Myers", "Ford", "Hamilton", "Graham", "Sullivan",
    "Wallace", "Woods", "Cole", "West", "Jordan", "Owens", "Reynolds", "Fisher",
    "Ellis", "Harrison", "Gibson", "Mcdonald", "Cruz", "Marshall", "Ortiz", "Gomez",
    "Murray", "Freeman", "Wells", "Webb", "Simpson", "Stevens", "Tucker", "Porter",
    "Hunter", "Hicks", "Crawford", "Henry", "Boyd", "Mason", "Morales", "Kennedy",
    "Warren", "Dixon", "Ramos", "Reyes", "Burns", "Gordon", "Shaw", "Holmes",
    "Rice", "Robertson", "Hunt", "Black", "Daniels", "Palmer", "Mills", "Nichols",
    "Grant", "Knight", "Ferguson", "Rose", "Stone", "Hawkins", "Dunn", "Perkins",
    "Hudson", "Spencer", "Gardner", "Stephens", "Payne", "Pierce", "Berry", "Matthews",
    "Arnold", "Wagner", "Willis", "Ray", "Watkins", "Olson", "Carroll", "Duncan",
    "Snyder", "Hart", "Cunningham", "Bradley", "Lane", "Andrews", "Ruiz", "Harper",
    "Fox", "Riley", "Armstrong", "Carpenter", "Weaver", "Greer", "Gardner", "Crosby",
)
DEFAULT_DOMAIN_SUBDOMAINS = ("sub", "x", "grok")

LEGACY_GROUP_MAP = {
    "默认分组": "mail_pool",
    "gpt_pending_account": "oai_pending",
    "gpt_new_account": "oai_success",
    "gpt_old_account": "oai_old",
    "badmail": "badmail",
}


def random_label(length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(4, length)))


def normalize_domain_subdomains(root: str, subdomains: list[str] | str | None = None) -> list[str]:
    root = str(root or "").strip().lower().lstrip("@")
    raw_values = subdomains
    if raw_values is None:
        raw_values = list(DEFAULT_DOMAIN_SUBDOMAINS)
    if isinstance(raw_values, str):
        raw_values = re.split(r"[\s,;]+", raw_values)
    hosts: list[str] = []
    for raw in raw_values or []:
        value = str(raw or "").strip().lower().lstrip("@")
        if not value:
            continue
        host = value if value == root or value.endswith("." + root) else f"{value}.{root}"
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) and host not in hosts:
            hosts.append(host)
    return hosts


def outlook_style_local(*, digits: int = 4, local_prefix: str = "") -> str:
    digits = max(1, min(int(digits or 4), 8))
    prefix = re.sub(r"[^A-Za-z0-9]+", "", str(local_prefix or "").strip())
    number = secrets.randbelow(10 ** digits)
    return f"{prefix}{secrets.choice(DOMAIN_NAME_FIRST_NAMES)}{secrets.choice(DOMAIN_NAME_LAST_NAMES)}{number:0{digits}d}" if prefix else f"{secrets.choice(DOMAIN_NAME_FIRST_NAMES)}{secrets.choice(DOMAIN_NAME_LAST_NAMES)}{number:0{digits}d}"


def is_domain_email(email: str, domain_suffixes: list[str]) -> bool:
    email = str(email or "").strip().lower()
    if "@" not in email:
        return False
    host = email.rsplit("@", 1)[-1]
    for suffix in domain_suffixes:
        suffix = str(suffix or "").strip().lower().lstrip("@")
        if not suffix:
            continue
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def generate_domain_emails(
    *,
    root_domain: str,
    count: int = 1,
    prefer_subdomain: bool = True,
    local_prefix: str = "",
    subdomain_length: int = 8,
    local_length: int = 10,
    subdomains: list[str] | str | None = None,
    name_style: str = "outlook",
    name_digits: int = 4,
) -> list[str]:
    root = str(root_domain or "").strip().lower().lstrip("@")
    if not root:
        raise ValueError("root_domain 不能为空")
    emails: list[str] = []
    seen: set[str] = set()
    hosts = normalize_domain_subdomains(root, subdomains) if prefer_subdomain else [root]
    if prefer_subdomain and not hosts:
        hosts = [root]
    for _ in range(max(1, int(count))):
        if str(name_style or "outlook").strip().lower() in {"outlook", "name_number", "name+number"}:
            local = outlook_style_local(digits=name_digits, local_prefix=local_prefix)
        else:
            local = (str(local_prefix or "").strip() + random_label(local_length)).lower()
        if prefer_subdomain:
            host = secrets.choice(hosts)
        else:
            host = root
        email = f"{local}@{host}"
        while email in seen:
            local = outlook_style_local(digits=name_digits, local_prefix=local_prefix) if str(name_style or "outlook").strip().lower() in {"outlook", "name_number", "name+number"} else (str(local_prefix or "").strip() + random_label(local_length)).lower()
            email = f"{local}@{host}"
        seen.add(email)
        emails.append(email)
    return emails


def pick_email_source_order(prefer_inventory: bool = True) -> list[str]:
    """Return ordered source tags for registration email selection."""
    if prefer_inventory:
        return ["inventory", "domain_sub", "domain_root"]
    return ["domain_sub", "domain_root", "inventory"]


def normalize_group_name(name: str, plan: dict[str, str] | None = None) -> str:
    plan = plan or DEFAULT_GROUP_PLAN
    text = str(name or "").strip()
    if text in LEGACY_GROUP_MAP:
        mapped = LEGACY_GROUP_MAP[text]
        return plan.get(mapped, mapped)
    return plan.get(text, text)
