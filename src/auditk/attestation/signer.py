"""Ed25519 signing and verification for auditk evidence packs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from auditk.schema import Signature


def generate_keypair(path: Path) -> tuple[Path, Path]:
    """Generate an Ed25519 keypair and write to <path>.ed25519 and <path>.ed25519.pub."""
    private_key = Ed25519PrivateKey.generate()
    priv_path = path.with_suffix(".ed25519")
    pub_path = path.with_suffix(".ed25519.pub")

    priv_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return priv_path, pub_path


class LocalEd25519Signer:
    """Signs payloads using a local PEM-encoded Ed25519 private key."""

    def __init__(self, key_path: Path | str) -> None:
        raw = Path(key_path).read_bytes()
        self._key: Ed25519PrivateKey = serialization.load_pem_private_key(raw, password=None)  # type: ignore[assignment]
        pub_bytes = self._key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._pub_pem = pub_bytes.decode()

    def sign(self, payload: bytes) -> Signature:
        import base64

        sig_bytes = self._key.sign(payload)
        return Signature(
            signer="local-ed25519",
            algorithm="ed25519",
            public_key=self._pub_pem,
            signature=base64.b64encode(sig_bytes).decode(),
            issued_at=datetime.now(UTC),
        )


class LocalEd25519Verifier:
    """Verifies Ed25519 signatures from a PEM public key."""

    def __init__(self, public_key_pem: str) -> None:
        self._key: Ed25519PublicKey = serialization.load_pem_public_key(  # type: ignore[assignment]
            public_key_pem.encode()
        )

    def verify(self, payload: bytes, signature_b64: str) -> None:
        """Raise cryptography.exceptions.InvalidSignature if verification fails."""
        import base64

        sig_bytes = base64.b64decode(signature_b64)
        self._key.verify(sig_bytes, payload)
