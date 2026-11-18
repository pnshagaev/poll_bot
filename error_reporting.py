"""Short exception details suitable for Telegram; shared with the process monitor."""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from urllib.parse import quote


REDACTED = "[скрыто]"


def redact_secrets(text: str, secrets: Iterable[str] = ()) -> str:
    values = list(secrets)
    values.extend(
        value for name, value in os.environ.items()
        if re.search(r"TOKEN|PASSWORD|SECRET|PRIVATE_KEY|API_KEY", name, re.IGNORECASE)
    )
    # Replace complete values before clipping, including URL-encoded credentials.
    for value in sorted({v for v in values if v}, key=len, reverse=True):
        text = text.replace(value, REDACTED).replace(quote(value, safe=""), REDACTED)
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", REDACTED, text, flags=re.DOTALL)
    text = re.sub(r"\b[0-9]{5,}:[A-Za-z0-9_-]{20,}\b", REDACTED, text)
    text = re.sub(r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@", rf"\1{REDACTED}@", text)
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;]+", rf"\1 {REDACTED}", text)
    text = re.sub(
        r'''(?i)(\b(?:\w+[_-])*(?:token|password|secret|api[_-]?key|private[_-]?key)(?:[_-]\w+)*["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''',
        rf"\1{REDACTED}", text,
    )
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)


def error_details(error: BaseException | None, *, secrets: Iterable[str] = ()) -> str:
    if error is None:
        return "Причина ошибки не передана."
    try:
        detail = f"{type(error).__name__}: {error}"
    except Exception:
        detail = type(error).__name__
    detail = redact_secrets(detail, secrets).strip()
    return detail if len(detail) <= 1200 else detail[:1199] + "…"


def error_message(summary: str, error: BaseException | None, *, secrets: Iterable[str] = ()) -> str:
    return f"{summary}\n{error_details(error, secrets=secrets)}"


def journal_error_excerpt(text: str, *, secrets: Iterable[str] = ()) -> str:
    """Pick exception messages, not stack frames, HTTP logs or arbitrary log contents."""
    lines = redact_secrets(text, secrets).splitlines()
    pattern = re.compile(
        r"(?:\b[\w.]*(?:Error|Exception|Exit)\b|\btelegram\.error\.\w+)\s*:|"
        r"Failed at step|Permission denied|No space left on device|Segmentation fault"
    )
    details = [line.strip() for line in lines if pattern.search(line) and not line.lstrip().startswith('File "')]
    result = "\n".join(details[-4:])
    if not result:
        return "Текст исключения в журнале не найден; см. состояние и код завершения выше."
    return result if len(result) <= 1800 else result[:1799] + "…"
