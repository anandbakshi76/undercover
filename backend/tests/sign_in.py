"""Sign-ins for tests: tokens signed with a key of the tests' own, not Supabase's."""

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
HANDLERS = {
    "dana": "dana.whitfield@undercover.example",
    "marcus": "marcus.oyelaran@undercover.example",
    "priya": "priya.raman@undercover.example",
}


class SigningKey:
    """The public key and algorithm that PyJWKClient would find for a token."""

    def __init__(self) -> None:
        self.key = PRIVATE_KEY.public_key()
        self.algorithm_name = "RS256"


class StubKeys:
    """A stand-in for the project's public keys, with the test key only."""

    def get_signing_key_from_jwt(self, token: str) -> SigningKey:
        return SigningKey()


def token(
    who: str,
    audience: str = "authenticated",
    expires_in: int = 3600,
    key: Any = PRIVATE_KEY,
) -> str:
    """Sign a token for a handler (by first name or email address)."""
    claims = {
        "sub": "user",
        "email": HANDLERS.get(who, who),
        "aud": audience,
        "exp": int(time.time()) + expires_in,
    }
    return jwt.encode(claims, key, algorithm="RS256")


def bearer(who: str = "dana", **options: Any) -> dict[str, str]:
    """The Authorization header of a signed-in handler."""
    return {"Authorization": f"Bearer {token(who, **options)}"}
