"""Extractors in the patterns stage: IBANs, legal references, amounts, user regex lists."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from oss_core.contracts import StageContext, WorkingDocument
from oss_pipeline.extractors import amounts, iban, law, problems, regexlists
from oss_pipeline.stages.index import to_solr_document
from oss_pipeline.stages.patterns import PatternsStage

TEXT = (
    "Invoice 2026-114. Pay AUD 1,250.50 into DE89 3704 0044 0532 0130 00 by Friday, or "
    "€12,50 to GB82WEST12345698765432. Under the Privacy Act 1988 (Cth) and section "
    "12(1)(a) of the Competition and Consumer Act 2010 the claim stands; see also "
    "Regulation (EU) 2016/679. Ticket TCK-4471 and TCK-4472, case CASE-2026-00017."
)


def _doc(text: str = TEXT) -> WorkingDocument:
    return WorkingDocument(
        id="d", collection="c", source_id="1", uri="u", content_hash="h", text=text
    )


def _run(settings: dict[str, object] | None = None, text: str = TEXT) -> WorkingDocument:
    ctx = StageContext(collection="c", profile="default", settings=settings or {})
    return PatternsStage().run(_doc(text), ctx)


# --- IBAN ------------------------------------------------------------------------------------


def test_ibans_are_found_grouped_or_not_and_checked() -> None:
    text = (
        "DE89 3704 0044 0532 0130 00 EUR; GB82WEST12345698765432; "
        "a typo DE89 3704 0044 0532 0130 01; BE68 5390 0754 7034 CODE; XX12 3456 7890 1234"
    )
    assert [v for v, _s, _e in iban.find(text)] == [
        "DE89370400440532013000",
        "GB82WEST12345698765432",
        "BE68539007547034",
    ]
    value, start, end = iban.find(text)[0]
    assert text[start:end] == "DE89 3704 0044 0532 0130 00"


def test_the_iban_checksum() -> None:
    assert iban.valid("NL91 ABNA 0417 1643 00") == "NL91ABNA0417164300"
    assert iban.valid("NL91 ABNA 0417 1643 01") is None
    assert iban.valid("NL91 ABNA 0417 1643") is None  # too short for NL


def test_iban_matching_stays_fast_on_hostile_text() -> None:
    hostile = ("AB12" + "C" * 29 + "a ") * 2000 + ("DE89 " + "1234 " * 7) * 2000
    started = time.perf_counter()
    iban.find(hostile)
    assert time.perf_counter() - started < 2.0


# --- amounts -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("1,234.56", "1234.56"),
        ("1.234,56", "1234.56"),
        ("1 234 567", "1234567"),
        ("12,5", "12.5"),
        ("1,234", "1234"),
        ("1'000", "1000"),
        ("42", "42"),
    ],
)
def test_numbers_with_either_decimal_mark(raw: str, value: str) -> None:
    assert amounts.number(raw) == Decimal(value)


def test_amounts_carry_their_currency() -> None:
    text = (
        "AUD 1,250.50; €12,50; 1.234,56 €; US$ 3m; $40; 20 euros; 5 bn USD; A$ 2.5 million; "
        "500 CHF; ¥1200; the year 2024 had 12 months"
    )
    found = [(c, a) for c, a, _s, _e in amounts.find(text, "AUD")]
    assert found == [
        ("AUD", "AUD 1250.50"),
        ("EUR", "EUR 12.50"),
        ("EUR", "EUR 1234.56"),
        ("USD", "USD 3000000.00"),
        ("AUD", "AUD 40.00"),
        ("EUR", "EUR 20.00"),
        ("USD", "USD 5000000000.00"),
        ("AUD", "AUD 2500000.00"),
        ("CHF", "CHF 500.00"),
        ("JPY", "JPY 1200.00"),
    ]
    assert amounts.find("$40", "XYZ")[0][0] == "USD"  # an unknown default falls back to USD


# --- law -------------------------------------------------------------------------------------


def test_default_law_references() -> None:
    text = (
        "Under the Privacy Act 1988 (Cth) and section 12(1)(a) of the Competition and Consumer "
        "Act 2010, the Regulation (EU) 2016/679 and Directive 2009/24/EC apply. See 42 U.S.C. "
        "§ 1983 and 17 CFR 240.10b-5. Nach § 823 BGB und Art. 3 GG. In [2020] HCA 5 and "
        "[2019] EWCA Civ 123 the court held. The Act says so."
    )
    refs = [value for value, _s, _e in law.find(text)]
    assert refs == [
        "Privacy Act 1988 (Cth)",
        "section 12(1)(a) of the Competition and Consumer Act 2010",
        "Regulation (EU) 2016/679",
        "Directive 2009/24/EC",
        "42 U.S.C. § 1983",
        "17 CFR 240.10b-5",
        "§ 823 BGB",
        "Art. 3 GG",
        "[2020] HCA 5",
        "[2019] EWCA Civ 123",
    ]
    for value, start, end in law.find(text):
        assert text[start:end] == value


def test_collections_add_law_patterns_and_can_drop_the_defaults() -> None:
    extra = regexlists.compile_lists({"law": [r"Policy (?P<value>P-\d+)"]})["law"]
    text = "Policy P-17 and the Privacy Act 1988 apply."
    assert [v for v, _s, _e in law.find(text, extra)] == ["P-17", "Privacy Act 1988"]
    assert [v for v, _s, _e in law.find(text, extra, defaults=False)] == ["P-17"]


# --- regex lists -----------------------------------------------------------------------------


def test_regex_lists_from_a_mapping_or_tsv() -> None:
    mapping = regexlists.compile_lists({"ticket": r"TCK-(\d+)", "case_no": [r"CASE-\d{4}-\d+"]})
    tsv = regexlists.compile_lists("# comment\nticket\tTCK-(\\d+)\ncase_no\tCASE-\\d{4}-\\d+\n")
    for lists in (mapping, tsv):
        found = regexlists.find(lists, TEXT)
        assert [v for v, _s, _e in found["ticket"]] == ["4471", "4472"]  # the first group
        assert [v for v, _s, _e in found["case_no"]] == ["CASE-2026-00017"]


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({"ticket": r"(a)\1"}, "invalid escape"),  # back-references are not RE2
        ({"ticket": r"(?=x)"}, "ticket"),  # nor is look-ahead
        ({"Bad Name": "x"}, "name"),
        ({"emails": "x"}, "name"),  # never one of the index's own fields (emails_ss)
        ({"ticket": 5}, "strings"),
        ({"ticket": "x" * 1001}, "1000"),
        ("ticket only", "TAB"),
        ([1, 2], "mapping"),
    ],
)
def test_regex_lists_are_validated(raw: object, reason: str) -> None:
    with pytest.raises(regexlists.RegexListError, match=reason):
        regexlists.compile_lists(raw)


def test_catastrophic_patterns_run_in_linear_time() -> None:
    lists = regexlists.compile_lists({"evil": r"(a+)+$", "evil2": r"(x|x)*y"})
    text = "a" * 50_000 + "!" + "x" * 50_000
    started = time.perf_counter()
    regexlists.find(lists, text)
    assert time.perf_counter() - started < 2.0  # Python's re would take for ever


def test_settings_problems_name_the_refused_keys() -> None:
    assert problems({"regex_lists": {"ok": "x+"}, "dollar_currency": "AUD"}) == []
    assert problems(
        {
            "regex_lists": {"bad": "(a)\\1"},
            "law_patterns": "not a list",
            "law_defaults": "yes",
            "dollar_currency": "XYZ",
        }
    ) == ["regex_lists", "law_patterns", "law_defaults", "dollar_currency"]


# --- in the patterns stage ---------------------------------------------------------------------


def test_the_patterns_stage_fills_the_new_facets() -> None:
    doc = _run({"regex_lists": {"ticket": r"TCK-\d+"}, "dollar_currency": "AUD"})
    assert doc.fields["x_iban_ss"] == ["DE89370400440532013000", "GB82WEST12345698765432"]
    assert doc.fields["x_currency_ss"] == ["AUD", "EUR"]
    assert doc.fields["x_amount_ss"] == ["AUD 1250.50", "EUR 12.50"]
    assert "Privacy Act 1988 (Cth)" in doc.fields["x_law_ss"]
    assert doc.fields["x_ticket_ss"] == ["TCK-4471", "TCK-4472"]
    law_tags = [t for t in doc.tags if "#LegalReference/" in t.iri]
    assert {t.label for t in law_tags} == set(doc.fields["x_law_ss"])
    assert all(t.stage == "patterns" and t.spans for t in law_tags)
    solr = to_solr_document(doc)
    assert solr["iban_ss"] and solr["law_ss"] and solr["currency_ss"] and solr["ticket_ss"]


def test_a_collection_can_leave_extractors_out_and_dropped_lists_are_cleared() -> None:
    doc = _run({"patterns": ["emails"], "regex_lists": {"ticket": r"TCK-\d+"}})
    assert not {"x_iban_ss", "x_law_ss", "x_currency_ss"} & set(doc.fields)
    assert doc.fields["x_ticket_ss"]
    # a replay after the list was removed (reindex keeps the stored fields)
    again = PatternsStage().run(doc, StageContext(collection="c", profile="default", settings={}))
    assert "x_ticket_ss" not in again.fields and "regex_list_names" not in again.fields
