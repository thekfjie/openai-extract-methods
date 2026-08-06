"""Text normalization shared by the upstream clients and the catalog search."""
from __future__ import annotations

import re
from html import unescape as html_unescape
from typing import Any


ZH_COUNTRY_CHAR_MAP = str.maketrans(
    {
        "國": "国",
        "爾": "尔",
        "亞": "亚",
        "達": "达",
        "蘭": "兰",
        "義": "义",
        "羅": "罗",
        "馬": "马",
        "維": "维",
        "貝": "贝",
        "麥": "麦",
        "臘": "腊",
        "盧": "卢",
        "門": "门",
        "臺": "台",
        "灣": "湾",
        "烏": "乌",
        "魯": "鲁",
        "薩": "萨",
        "聖": "圣",
        "幾": "几",
        "納": "纳",
        "剛": "刚",
        "島": "岛",
        "裡": "里",
        "蘇": "苏",
        "聯": "联",
        "長": "长",
        "茲": "兹",
        "團": "团",
        "圓": "圆",
        "贊": "赞",
        "歐": "欧",
        "愛": "爱",
        "倫": "伦",
        "屬": "属",
        "與": "与",
        "內": "内",
        "庫": "库",
        "錫": "锡",
    }
)

def normalize_text(value: Any) -> str:
    text = str(value or "").translate(ZH_COUNTRY_CHAR_MAP).lower()
    return "".join(ch for ch in text if ch.isalnum())


def collect_string_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            values.extend(collect_string_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(collect_string_values(item))
    elif isinstance(value, str):
        text = value.strip()
        if text:
            values.append(text)
    return values

def html_to_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html_unescape(text)
    return re.sub(r"\s+", " ", text).strip()
