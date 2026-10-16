"""IBANs: candidates in the text, kept only when their length and check digits are right.

A candidate is a country code, two check digits and the account part, written as one run
or in groups separated by single spaces (`DE89 3704 0044 0532 0130 00`). It is kept when
its country has IBANs of that length (the ISO 13616 registry's lengths below) and the
ISO 7064 MOD 97-10 check gives 1. Values are stored without spaces, upper case.
"""

from __future__ import annotations

import re

# Country code -> IBAN length, as published in the ISO 13616 IBAN registry.
LENGTHS: dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22,
    "BH": 22, "BI": 27, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24,
    "DE": 22, "DJ": 27, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24, "FI": 18,
    "FK": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27,
    "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26, "IT": 27,
    "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "LY": 25, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MN": 20, "MR": 27,
    "MT": 31, "MU": 30, "NI": 28, "NL": 18, "NO": 15, "OM": 23, "PK": 24, "PL": 28,
    "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "RU": 33, "SA": 24, "SC": 31,
    "SD": 18, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "SO": 23, "ST": 25, "SV": 28,
    "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20, "YE": 30,
}  # fmt: skip

# a country code and check digits, then the account part in one run or in printed groups of
# four (the last one shorter); no nested open-ended repetition, so no backtracking blow-up
CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}"
    r"(?:[A-Z0-9]{11,30}|(?: [A-Z0-9]{4}){2,7}(?: [A-Z0-9]{1,4})?)"
    r"(?![a-z0-9])"
)


def checksum_ok(iban: str) -> bool:
    """ISO 7064 MOD 97-10: the rearranged IBAN, letters as numbers, modulo 97 is 1."""

    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


def valid(value: str) -> str | None:
    """The IBAN without spaces when it is a valid one, else None."""

    compact = value.replace(" ", "").upper()
    expected = LENGTHS.get(compact[:2])
    if expected is None or len(compact) != expected or not compact.isalnum():
        return None
    return compact if checksum_ok(compact) else None


def find(text: str) -> list[tuple[str, int, int]]:
    """(IBAN, start, end) of each valid IBAN in the text.

    A candidate that runs on into the next word (`... 0130 00 EUR`) is cut back to its
    country's length, at a group boundary.
    """

    out: list[tuple[str, int, int]] = []
    for match in CANDIDATE.finditer(text):
        raw = match.group(0)
        expected = LENGTHS.get(raw[:2])
        if expected is None:
            continue
        count, end = 0, 0
        for i, ch in enumerate(raw):
            if ch != " ":
                count += 1
            if count == expected:
                end = i + 1
                break
        if not end or (end < len(raw) and raw[end] != " "):
            continue
        iban = valid(raw[:end])
        if iban:
            out.append((iban, match.start(), match.start() + end))
    return out
