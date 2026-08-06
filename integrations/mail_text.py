"""Reading a fetched mail item: MIME decoding and verification-code extraction."""
from __future__ import annotations

import re
from email import policy
from email.parser import Parser
from typing import Any

from integrations.text_utils import html_to_text


def decode_mail_payload(raw: str) -> dict[str, str]:
    payload = {"subject": "", "text": "", "html": ""}
    source = str(raw or "").strip()
    if not source:
        return payload
    try:
        message = Parser(policy=policy.default).parsestr(source)
    except Exception:
        payload["text"] = source
        return payload

    payload["subject"] = str(message.get("subject") or "").strip()
    text_parts: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if getattr(part, "is_multipart", lambda: False)():
            continue
        content_type = str(part.get_content_type() or "").lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except Exception:
            try:
                content = part.get_payload(decode=True)
            except Exception:
                content = ""
            if isinstance(content, bytes):
                charset = part.get_content_charset() or "utf-8"
                content = content.decode(charset, errors="replace")
        if not isinstance(content, str):
            content = str(content or "")
        if content_type == "text/plain":
            text_parts.append(content)
        else:
            html_parts.append(content)
    payload["text"] = re.sub(r"\s+", " ", " ".join(text_parts)).strip()
    payload["html"] = " ".join(html_parts).strip()
    return payload


def extract_verification_code_from_mail(item: dict[str, Any] | None) -> tuple[str | None, str, str]:
    if not isinstance(item, dict):
        return None, "", ""
    decoded = decode_mail_payload(str(item.get("raw") or ""))
    subject = decoded["subject"] or str(item.get("subject") or item.get("decodedSubject") or "").strip()

    text_sources: list[str] = []

    def append_text(value: Any, *, maybe_html: bool = False) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if maybe_html or re.search(r"</?[a-z][\s\S]*>", text, flags=re.IGNORECASE):
            text = html_to_text(text)
        else:
            text = re.sub(r"\s+", " ", text).strip()
        if text and text not in text_sources:
            text_sources.append(text)

    append_text(decoded["html"], maybe_html=True)
    append_text(decoded["text"])
    append_text(item.get("decodedText"))
    append_text(item.get("text"))
    append_text(item.get("content"))
    append_text(item.get("body_preview"))
    append_text(item.get("bodyPreview"))
    append_text(item.get("body"))
    append_text(item.get("html"))
    append_text(item.get("html_content"), maybe_html=True)

    visible_text = "\n".join(text_sources)
    for source in (*text_sources, subject):
        match = re.search(r"(?<!\d)(\d{6})(?!\d)", source or "")
        if match:
            return match.group(1), subject, visible_text
    combined = f"{subject}\n{visible_text}\n{decoded['text']}"
    targeted_patterns = [
        r"(?:verification code|temporary code|验证码|临时验证码|输入此临时验证码以继续)[^\d]{0,40}(\d{6})",
        r"(?<!\d)(\d{6})(?!\d)",
    ]
    for pattern in targeted_patterns:
        match = re.search(pattern, combined, flags=re.IGNORECASE)
        if match:
            return match.group(1), subject, visible_text
    return None, subject, visible_text


def enrich_temp_mail_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return item
    code, subject, visible_text = extract_verification_code_from_mail(item)
    enriched: dict[str, Any] = {}
    if code:
        enriched["verificationCode"] = code
    if subject:
        enriched["decodedSubject"] = subject
    if visible_text:
        enriched["decodedText"] = visible_text
    enriched.update(item)
    return enriched
