"""etag_matches: RFC 9110 weak comparison (§11.3)."""

from __future__ import annotations

from geometry_api.conditional import etag_matches


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
