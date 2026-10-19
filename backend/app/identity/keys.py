"""TW-619 Twistor Identity — RSA keypair management + JWKS publishing."""

import base64
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@dataclass
class Keypair:
    kid: str
    private_pem: str
    public_pem: str


def generate_keypair(kid: str | None = None) -> Keypair:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return Keypair(
        kid=kid or f"tw-{secrets.token_hex(4)}",
        private_pem=private_pem,
        public_pem=public_pem,
    )


def load_keypair(private_pem: str, kid: str) -> Keypair:
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return Keypair(kid=kid, private_pem=private_pem, public_pem=public_pem)


def _b64url_int(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def public_jwks(keypairs: list[Keypair]) -> dict:
    """Publish public keys only — never any private material."""
    keys = []
    for kp in keypairs:
        pub = serialization.load_pem_public_key(kp.public_pem.encode())
        numbers = pub.public_numbers()
        keys.append(
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kp.kid,
                "n": _b64url_int(numbers.n),
                "e": _b64url_int(numbers.e),
            }
        )
    return {"keys": keys}
