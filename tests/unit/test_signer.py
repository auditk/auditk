"""Tests for auditk.attestation.signer."""

import pytest
from cryptography.exceptions import InvalidSignature

from auditk.attestation.canonical import canonicalize
from auditk.attestation.signer import (
    LocalEd25519Signer,
    LocalEd25519Verifier,
    generate_keypair,
)


def test_generate_keypair_creates_two_files(tmp_path: pytest.TempPathFactory) -> None:
    key_base = tmp_path / "signing_key"
    priv_path, pub_path = generate_keypair(key_base)
    assert priv_path.exists()
    assert pub_path.exists()
    assert priv_path.suffix == ".ed25519"
    assert pub_path.name.endswith(".ed25519.pub")


def test_sign_and_verify_round_trip(tmp_path: pytest.TempPathFactory) -> None:
    key_base = tmp_path / "key"
    priv_path, pub_path = generate_keypair(key_base)
    signer = LocalEd25519Signer(priv_path)
    payload = canonicalize({"hello": "world"})
    sig = signer.sign(payload)
    verifier = LocalEd25519Verifier(pub_path.read_text())
    # Should not raise
    verifier.verify(payload, sig.signature)


def test_tampered_payload_fails_verification(tmp_path: pytest.TempPathFactory) -> None:
    key_base = tmp_path / "key"
    priv_path, pub_path = generate_keypair(key_base)
    signer = LocalEd25519Signer(priv_path)
    payload = canonicalize({"hello": "world"})
    sig = signer.sign(payload)
    tampered = canonicalize({"hello": "evil"})
    verifier = LocalEd25519Verifier(pub_path.read_text())
    with pytest.raises(InvalidSignature):
        verifier.verify(tampered, sig.signature)


def test_signature_has_correct_algorithm_field(tmp_path: pytest.TempPathFactory) -> None:
    key_base = tmp_path / "key"
    priv_path, _ = generate_keypair(key_base)
    signer = LocalEd25519Signer(priv_path)
    sig = signer.sign(b"test payload")
    assert sig.algorithm == "ed25519"
