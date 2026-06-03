"""Tests for auditk.attestation.canonical."""

from auditk.attestation.canonical import canonicalize


def test_dict_key_order_is_normalised() -> None:
    # Keys should be sorted regardless of insertion order
    result_a = canonicalize({"b": 1, "a": 2})
    result_b = canonicalize({"a": 2, "b": 1})
    assert result_a == result_b
    assert result_a == b'{"a":2,"b":1}'


def test_nested_dict_keys_are_sorted() -> None:
    obj = {"z": {"y": 1, "x": 2}, "a": 0}
    result = canonicalize(obj)
    assert result == b'{"a":0,"z":{"x":2,"y":1}}'


def test_output_is_bytes() -> None:
    result = canonicalize({"key": "value"})
    assert isinstance(result, bytes)
