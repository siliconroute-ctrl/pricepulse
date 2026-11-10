"""Firebase sign-in tokens, checked with a signing key made for the test (no network)."""

import time
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from pricepulse.auth import AuthError, DevVerifier, FirebaseVerifier

PROJECT = "trolleyup-eff50"


def make_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


KEY, PEM = make_key()
OTHER_KEY, _ = make_key()


def token(key=KEY, kid="k1", provider="password", **over):
    now = int(time.time())
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": "uid123",
        "iat": now - 10,
        "exp": now + 3600,
        "email": "a@example.test",
        "firebase": {"sign_in_provider": provider},
    }
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def verifier():
    return FirebaseVerifier(PROJECT, fetch=lambda: ({"k1": PEM}, 3600.0))


def test_good_token(verifier):
    who = verifier.verify(token())
    assert (who.uid, who.email, who.provider) == ("uid123", "a@example.test", "password")
    assert verifier.verify(token(provider="google.com")).provider == "google.com"


@pytest.mark.parametrize(
    "bad",
    [
        lambda: token(key=OTHER_KEY),  # signed by someone else
        lambda: token(aud="another-project"),
        lambda: token(iss="https://securetoken.google.com/another-project"),
        lambda: token(exp=int(time.time()) - 3600),
        lambda: token(kid="unknown"),
        lambda: token(provider="anonymous"),  # TrolleyUp guests cannot use PricePulse
        lambda: token(sub=""),
        lambda: "not-a-token",
        lambda: jwt.encode(
            {"sub": "uid123", "aud": PROJECT}, "secret", algorithm="HS256", headers={"kid": "k1"}
        ),
    ],
)
def test_bad_tokens(verifier, bad):
    with pytest.raises(AuthError):
        verifier.verify(bad())


def test_key_fetch_failure_is_a_clean_error():
    def boom():
        raise OSError("offline")

    with pytest.raises(AuthError):
        FirebaseVerifier(PROJECT, fetch=boom).verify(token())


def test_dev_verifier_only_accepts_dev_tokens():
    assert DevVerifier().verify("dev:alice").uid == "dev-alice"
    with pytest.raises(AuthError):
        DevVerifier().verify(token())


def test_real_verifier_is_the_default(settings):
    from pricepulse.auth import make_verifier

    settings.auth_dev = False
    assert isinstance(make_verifier(settings), FirebaseVerifier)
