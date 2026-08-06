from dataclasses import dataclass, field
from typing import Optional
import httpx
import random
import string
import time
import uuid


@dataclass
class UserInfo:
    first_name: str
    last_name: str
    email: str
    phone: str
    phone_local: str
    phone_country_code: str
    password: str
    dob: str  # DD/MM/YYYY
    cpf: str = ""  # BR only; empty for other countries
    nationality: str = "BR"


@dataclass
class CardInfo:
    number: str
    expiry: str  # MM/YYYY
    cvv: str
    card_type: str = "CREDIT"


@dataclass
class BillingAddress:
    street: str
    house_number: str
    district: str
    city: str
    state: str
    postal_code: str
    country: str = "BR"


@dataclass
class SessionState:
    ba_token: str = ""
    ec_token: str = ""
    ssrt: str = ""
    ctx_id: str = ""
    nsid: str = ""
    d_id: str = ""
    user_id: str = ""
    datadome_cookie: str = ""
    tltsid: str = ""
    tltdid: str = ""
    paypal_client_metadata_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    euat_token: str = ""
    return_url: str = ""
    content_hash: str = ""
    content_identifier: str = ""
    signup_url: str = ""
    checkout_drop_loaded: bool = False
    hermes_url: str = ""
    hermes_loaded: bool = False
    show_create_account_action_id: str = ""
    create_user_action_id: str = ""
    country: str = "BR"
    locale: str = "pt_BR"  # e.g. en_GB / pt_BR
    lang: str = "pt"       # language part for contentIdentifier / griffin
    # One task-scoped, internally coherent browser profile.  Every protocol
    # surface (HTTP headers, FraudNet, analytics and Tealeaf) reads this same
    # snapshot instead of independently inventing values.
    fingerprint_profile: dict = field(default_factory=dict)

    def update_from_cookies(self, cookies: dict):
        if "nsid" in cookies:
            self.nsid = cookies["nsid"]
        if "d_id" in cookies:
            self.d_id = cookies["d_id"]
        if "datadome" in cookies:
            self.datadome_cookie = cookies["datadome"]
        if "TLTSID" in cookies:
            self.tltsid = cookies["TLTSID"]
        if "TLTDID" in cookies:
            self.tltdid = cookies["TLTDID"]
        # Browser checkout uses the persistent fn_dt device id as the
        # independent Client-Metadata-Id on the final authorize request.
        if "fn_dt" in cookies and cookies["fn_dt"]:
            self.paypal_client_metadata_id = cookies["fn_dt"]
        euat_key = "AV894Kt2TSumQQrJwe-8mzmyREO"
        if euat_key in cookies:
            # Prefer freshest cookie EUAT after signup/uplift.
            self.euat_token = cookies[euat_key]


def generate_random_email() -> str:
    chars = string.ascii_lowercase + string.digits
    user = "".join(random.choice(chars) for _ in range(12))
    return f"{user}@gmail.com"


def generate_eteid() -> list:
    return [
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        random.randint(-10000000000, 20000000000),
        None,
        None,
    ]


# --- Random generators ---

_BR_FIRST_NAMES = [
    "Lucas", "Gabriel", "Miguel", "Arthur", "Matheus", "Pedro", "Rafael",
    "Gustavo", "Felipe", "Bernardo", "Henrique", "Daniel", "Leonardo",
    "Ana", "Maria", "Julia", "Beatriz", "Larissa", "Fernanda", "Camila",
    "Leticia", "Amanda", "Carolina", "Bruna", "Mariana", "Isabela",
]

_BR_LAST_NAMES = [
    "Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira",
    "Almeida", "Nascimento", "Lima", "Araujo", "Pereira", "Carvalho",
    "Ribeiro", "Gomes", "Martins", "Costa", "Barbosa", "Moreira",
    "Mendes", "Cardoso", "Teixeira", "Vieira", "Correia", "Nunes",
]

_GB_FIRST_NAMES = [
    "James", "Oliver", "Harry", "Jack", "George", "Noah", "Charlie",
    "Thomas", "Oscar", "William", "Henry", "Leo", "Alfie", "Archie",
    "Emily", "Olivia", "Amelia", "Isla", "Ava", "Mia", "Isabella",
    "Sophie", "Grace", "Lily", "Freya", "Ella", "Chloe", "Poppy",
]

_GB_LAST_NAMES = [
    "Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson",
    "Davies", "Patel", "Wright", "Walker", "Thompson", "White", "Edwards",
    "Hughes", "Green", "Hall", "Lewis", "Harris", "Clarke", "Jackson",
]

_KNOWN_BR_ADDRESSES = [
    ("Avenida Cristovao Colombo", "287", "Savassi", "Belo Horizonte", "MG", "30140-140"),
    ("Rua Siqueira Campos, 946", "1001", "Centro Historico", "Porto Alegre", "RS", "90010-001"),
    ("Avenida Paulista", "1000", "Bela Vista", "Sao Paulo", "SP", "01310-100"),
    ("Rua da Assembleia", "10", "Centro", "Rio de Janeiro", "RJ", "20011-901"),
    ("Rua XV de Novembro", "100", "Centro", "Curitiba", "PR", "80020-310"),
    ("Avenida Sete de Setembro", "1200", "Centro", "Salvador", "BA", "40060-001"),
]

_KNOWN_GB_ADDRESSES = [
    ("Flat 14, 56 Queen Street", "", "", "Cardiff", "", "CF10 2HE"),
    ("12 Baker Street", "", "", "London", "", "NW1 6XE"),
    ("45 High Street", "", "", "Manchester", "", "M1 1AE"),
    ("8 King Street", "", "", "Bristol", "", "BS1 4EQ"),
    ("27 Church Road", "", "", "Birmingham", "", "B3 2BB"),
    ("3 Station Road", "", "", "Leeds", "", "LS1 4DY"),
]


def _luhn_checksum(partial: str) -> int:
    digits = [int(d) for d in partial]
    for i in range(len(digits) - 1, -1, -2):
        digits[i] *= 2
        if digits[i] > 9:
            digits[i] -= 9
    total = sum(digits)
    return (10 - (total % 10)) % 10


SUIJIDAQUAN_CARD_API = "https://api2.suijidaquan.com/api/v2/random-credit-card"
SUIJIDAQUAN_CARD_REFERER = "https://www.suijidaquan.com/credit-card-generator"


def _normalize_suijidaquan_card_type(value: str) -> str:
    normalized = (value or "").strip().lower().replace(" ", "")
    if normalized == "visa":
        return "VISA"
    if normalized in {"mastercard", "master", "master_card"}:
        return "MASTER_CARD"
    return (value or "").strip().upper()


def _fetch_suijidaquan_card(proxy_url: str | None = None, count: int = 5) -> CardInfo:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
        ),
        "Origin": "https://www.suijidaquan.com",
        "Referer": SUIJIDAQUAN_CARD_REFERER,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }
    payload = {"count": count, "method": "random_credit_card"}

    client_kwargs = {
        "timeout": httpx.Timeout(15.0),
        "headers": headers,
        "trust_env": False,
    }
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    with httpx.Client(**client_kwargs) as client:
        resp = client.post(SUIJIDAQUAN_CARD_API, json=payload)
        resp.raise_for_status()
        result = resp.json()

    if result.get("status") != "ok" or not isinstance(result.get("data"), list):
        raise RuntimeError(f"Unexpected suijidaquan response: {result!r}")

    candidates: list[CardInfo] = []
    for item in result["data"]:
        if not isinstance(item, dict):
            continue
        issuer = _normalize_suijidaquan_card_type(item.get("Credit_Card_Type", ""))
        if issuer not in {"VISA", "MASTER_CARD"}:
            continue
        number = "".join(ch for ch in str(item.get("Credit_Card_Number", "")) if ch.isdigit())
        expiry = str(item.get("Expires", "")).strip()
        cvv = "".join(ch for ch in str(item.get("CVV2", "")) if ch.isdigit())
        if len(number) < 13 or "/" not in expiry or len(cvv) < 3:
            continue
        candidates.append(CardInfo(number=number, expiry=expiry, cvv=cvv[:4], card_type="CREDIT"))

    if not candidates:
        raise RuntimeError("suijidaquan returned no Visa/MasterCard candidates")
    return random.choice(candidates)


_FALLBACK_CARD_PREFIXES = [
    ("4", "VISA"),
    ("51", "MASTER_CARD"),
    ("52", "MASTER_CARD"),
    ("53", "MASTER_CARD"),
    ("54", "MASTER_CARD"),
    ("55", "MASTER_CARD"),
]


def generate_card(proxy_url: str | None = None) -> CardInfo:
    for _ in range(3):
        try:
            card = _fetch_suijidaquan_card(proxy_url=proxy_url)
            if card:
                return card
        except Exception:
            pass

    prefix, _issuer = random.choice(_FALLBACK_CARD_PREFIXES)
    remaining = 16 - len(prefix) - 1
    body = prefix + "".join(str(random.randint(0, 9)) for _ in range(remaining))
    check = _luhn_checksum(body)
    number = body + str(check)

    month = random.randint(1, 12)
    year = random.randint(2027, 2031)
    expiry = f"{month:02d}/{year}"
    cvv = f"{random.randint(0, 999):03d}"
    return CardInfo(number=number, expiry=expiry, cvv=cvv, card_type="CREDIT")


def generate_cpf() -> str:
    digits = [random.randint(0, 9) for _ in range(9)]
    s = sum(d * w for d, w in zip(digits, range(10, 1, -1)))
    r = s % 11
    d1 = 0 if r < 2 else 11 - r
    digits.append(d1)
    s = sum(d * w for d, w in zip(digits, range(11, 1, -1)))
    r = s % 11
    d2 = 0 if r < 2 else 11 - r
    digits.append(d2)
    d = digits
    return f"{d[0]}{d[1]}{d[2]}.{d[3]}{d[4]}{d[5]}.{d[6]}{d[7]}{d[8]}-{d[9]}{d[10]}"


def generate_dob() -> str:
    day = random.randint(1, 28)
    month = random.randint(1, 12)
    year = random.randint(1970, 2000)
    return f"{day:02d}/{month:02d}/{year}"


def generate_password() -> str:
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits_chars = string.digits
    symbols = "!@#$%^"
    pwd = (
        [random.choice(lower) for _ in range(6)]
        + [random.choice(upper) for _ in range(3)]
        + [random.choice(digits_chars) for _ in range(3)]
        + [random.choice(symbols) for _ in range(2)]
    )
    random.shuffle(pwd)
    return "".join(pwd)


def normalize_locale(country: str, locale: str | None = None) -> tuple[str, str, str]:
    """Return (country, locale, lang)."""
    country = (country or "BR").strip().upper()
    if locale:
        loc = locale.strip().replace("-", "_")
        if "_" in loc:
            lang, country_part = loc.split("_", 1)
            return country_part.upper() if country_part else country, f"{lang.lower()}_{country_part.upper() if country_part else country}", lang.lower()
        return country, f"{loc.lower()}_{country}", loc.lower()
    defaults = {
        "BR": ("pt_BR", "pt"),
        "GB": ("en_GB", "en"),
        "UK": ("en_GB", "en"),
        "US": ("en_US", "en"),
        "TH": ("th_TH", "th"),
    }
    if country == "UK":
        country = "GB"
    locale_code, lang = defaults.get(country, ("en_US", "en"))
    return country, locale_code, lang


def normalize_phone(phone: str, default_country: str = "BR") -> tuple[str, str, str]:
    """Return (full_e164, local_digits, country_code_with_plus)."""
    raw = (phone or "").strip()
    if raw.lower().startswith("phone:"):
        raw = raw.split(":", 1)[1].strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 8:
        raise ValueError("phone number is too short")

    # Common dialing codes used by this project.
    dial_map = {
        "BR": "55",
        "GB": "44",
        "UK": "44",
        "US": "1",
        "TH": "66",
    }
    default_cc = dial_map.get((default_country or "BR").upper(), "55")

    # Prefer explicit international forms when present.
    if digits.startswith("44") and len(digits) >= 12:
        return f"+{digits}", digits[2:].lstrip("0"), "+44"
    if digits.startswith("55") and len(digits) >= 12:
        return f"+{digits}", digits[2:], "+55"
    if digits.startswith("66") and len(digits) >= 11:
        return f"+{digits}", digits[2:].lstrip("0"), "+66"
    if digits.startswith("1") and len(digits) == 11:
        return f"+{digits}", digits[1:], "+1"

    local = digits.lstrip("0") if default_cc in {"44", "66"} else digits
    return f"+{default_cc}{local}", local, f"+{default_cc}"


def generate_user(phone: str, country: str = "BR") -> UserInfo:
    country, _locale, _lang = normalize_locale(country)
    if country == "GB":
        first = random.choice(_GB_FIRST_NAMES)
        last = random.choice(_GB_LAST_NAMES)
        cpf = ""
    else:
        first = random.choice(_BR_FIRST_NAMES)
        last = random.choice(_BR_LAST_NAMES)
        cpf = generate_cpf() if country == "BR" else ""

    full, local, cc = normalize_phone(phone, default_country=country)
    return UserInfo(
        first_name=first,
        last_name=last,
        email=generate_random_email(),
        phone=full,
        phone_local=local,
        phone_country_code=cc,
        password=generate_password(),
        dob=generate_dob(),
        cpf=cpf,
        nationality=country,
    )


def generate_address(country: str = "BR") -> BillingAddress:
    country, _locale, _lang = normalize_locale(country)
    if country == "GB":
        street, house_number, district, city, state, postal_code = random.choice(_KNOWN_GB_ADDRESSES)
        return BillingAddress(
            street=street,
            house_number=house_number,
            district=district,
            city=city,
            state=state,
            postal_code=postal_code,
            country="GB",
        )

    street, house_number, district, city, state, postal_code = random.choice(_KNOWN_BR_ADDRESSES)
    return BillingAddress(
        street=street,
        house_number=house_number,
        district=district,
        city=city,
        state=state,
        postal_code=postal_code,
        country="BR",
    )
