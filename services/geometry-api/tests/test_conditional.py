"""etag_matches: RFC 9110 weak comparison (§11.3). accepts_gzip: RFC 9110 §12.5.3 quality
values (W3) — q=0 means "not acceptable", not "the substring 'gzip' happens to be present"."""

from __future__ import annotations

from geometry_api.conditional import accepts_gzip, etag_matches


def test_no_header_never_matches() -> None:
    assert etag_matches(None, '"abc"') is False
    assert etag_matches("", '"abc"') is False


def test_star_always_matches() -> None:
    assert etag_matches("*", '"anything"') is True


def test_exact_match() -> None:
    assert etag_matches('"abc"', '"abc"') is True


def test_a_weak_prefix_on_the_clients_copy_still_matches() -> None:
    assert etag_matches('W/"abc"', '"abc"') is True


def test_one_of_several_comma_separated_values_matches() -> None:
    assert etag_matches('"zzz", "abc", "yyy"', '"abc"') is True


def test_whitespace_around_values_is_tolerated() -> None:
    assert etag_matches(' "zzz" ,  "abc"  ', '"abc"') is True


def test_a_non_matching_value_does_not_match() -> None:
    assert etag_matches('"zzz"', '"abc"') is False


def test_no_accept_encoding_header_does_not_accept_gzip() -> None:
    assert accepts_gzip("") is False


def test_plain_gzip_is_accepted() -> None:
    assert accepts_gzip("gzip") is True
    assert accepts_gzip("gzip, deflate, br") is True


def test_gzip_with_q_zero_is_not_accepted() -> None:
    """The exact bug: a naive substring check treats 'gzip;q=0' as containing 'gzip'."""
    assert accepts_gzip("gzip;q=0") is False
    assert accepts_gzip("gzip;q=0.0") is False
    assert accepts_gzip("deflate, gzip;q=0, br") is False


def test_gzip_with_a_positive_q_is_accepted() -> None:
    assert accepts_gzip("gzip;q=0.5") is True
    assert accepts_gzip("gzip; q=1.0") is True


def test_wildcard_accepts_gzip_when_gzip_is_not_named() -> None:
    assert accepts_gzip("*") is True
    assert accepts_gzip("deflate, *;q=0.3") is True


def test_wildcard_with_q_zero_does_not_accept_gzip() -> None:
    assert accepts_gzip("*;q=0") is False


def test_explicit_gzip_overrides_a_wildcard_rejection() -> None:
    assert accepts_gzip("*;q=0, gzip") is True


def test_explicit_gzip_rejection_overrides_a_wildcard_acceptance() -> None:
    assert accepts_gzip("*, gzip;q=0") is False


def test_only_other_codings_named_does_not_accept_gzip() -> None:
    assert accepts_gzip("deflate, br") is False
