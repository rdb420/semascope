"""Amounts of money with their currency, normalised: `AUD 1250000.00`, and the ISO 4217 code.

An amount is a number with a currency before it (`AUD 1,250`, `€12.50`, `US$ 3m`) or after
it (`1.234,56 €`, `500 EUR`, `20 euros`). The number may group thousands with commas,
points, spaces or apostrophes and use a comma or a point for decimals; a scale word
(`thousand`, `k`, `million`, `m`, `mn`, `billion`, `bn`) multiplies it. A bare `$` is the
collection's `dollar_currency` (USD unless set); `A$`, `NZ$`, `US$` and the like say which
dollar they are.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

CODES = (
    "AED AUD BRL CAD CHF CNY CZK DKK EUR GBP HKD HUF IDR ILS INR JPY KRW MXN MYR NOK NZD PHP "
    "PLN RUB SAR SEK SGD THB TRY TWD USD ZAR"
).split()
SYMBOLS = {
    "A$": "AUD",
    "AU$": "AUD",
    "US$": "USD",
    "NZ$": "NZD",
    "C$": "CAD",
    "CA$": "CAD",
    "HK$": "HKD",
    "S$": "SGD",
    "R$": "BRL",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₩": "KRW",
    "₽": "RUB",
    "₺": "TRY",
    "₪": "ILS",
}
NAMES = {
    "euro": "EUR",
    "euros": "EUR",
    "yen": "JPY",
    "rupees": "INR",
    "pounds sterling": "GBP",
    "us dollars": "USD",
    "australian dollars": "AUD",
    "new zealand dollars": "NZD",
    "canadian dollars": "CAD",
    "swiss francs": "CHF",
}
SCALES = {
    "thousand": 1_000,
    "k": 1_000,
    "million": 1_000_000,
    "m": 1_000_000,
    "mn": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
}


def _alternatives(words: list[str]) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_NUMBER = r"\d{1,3}(?:[,.'  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_SCALE = r"(?:\s?(?P<scale>thousand|million|billion|mn|bn|k|m)\b)?"
_BEFORE = _alternatives([*CODES, *SYMBOLS, "$"])
_AFTER = _alternatives([*CODES, "€", "£", *NAMES, "dollars"])
PATTERN = re.compile(
    rf"(?<![\w$])(?P<cur>{_BEFORE})\s?(?P<num>{_NUMBER}){_SCALE}"
    rf"|(?<![\w.,])(?P<num2>{_NUMBER}){_SCALE.replace('scale', 'scale2')}"
    rf"\s?(?P<cur2>{_AFTER})(?!\w)",
    re.IGNORECASE,
)


def number(raw: str) -> Decimal | None:
    """`1,234.56`, `1.234,56`, `1 234`, `12,5` -> a Decimal; None when it is not a number."""

    value = raw.replace(" ", " ").replace("'", "").replace(" ", "")
    last_comma, last_point = value.rfind(","), value.rfind(".")
    if last_comma >= 0 and last_point >= 0:
        decimal = "," if last_comma > last_point else "."
    elif last_comma >= 0 or last_point >= 0:
        sep = "," if last_comma >= 0 else "."
        tail = len(value) - value.rfind(sep) - 1
        decimal = sep if tail != 3 else ""  # `1,234` and `1.234` group thousands
    else:
        decimal = ""
    grouping = {",", "."} - {decimal}
    for sep in grouping:
        value = value.replace(sep, "")
    if decimal:
        value = value.replace(decimal, ".")
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _currency(raw: str, dollar: str) -> str | None:
    key = raw.strip()
    if key.upper() in CODES:
        return key.upper()
    if key in SYMBOLS:
        return SYMBOLS[key]
    if key.upper() in {k.upper() for k in SYMBOLS}:  # `a$`, `us$`
        return next(v for k, v in SYMBOLS.items() if k.upper() == key.upper())
    if key == "$" or key.lower() == "dollars":
        return dollar
    return NAMES.get(key.lower())


def find(text: str, dollar: str = "USD") -> list[tuple[str, str, int, int]]:
    """(currency code, normalised amount, start, end) of each amount in the text."""

    dollar = dollar.upper() if dollar and dollar.upper() in CODES else "USD"
    out: list[tuple[str, str, int, int]] = []
    for m in PATTERN.finditer(text):
        cur = m.group("cur") or m.group("cur2") or ""
        raw = m.group("num") or m.group("num2") or ""
        scale = (m.group("scale") or m.group("scale2") or "").lower()
        code = _currency(cur, dollar)
        amount = number(raw)
        if code is None or amount is None:
            continue
        amount *= SCALES.get(scale, 1)
        out.append((code, f"{code} {amount:.2f}", m.start(), m.end()))
    return out
