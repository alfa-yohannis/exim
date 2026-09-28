"""Tests for sign-in, session tokens and the admin/guest role split."""

from __future__ import annotations

import time

import pytest

from app.authentication import (
    AuthenticationService,
    LoginThrottle,
    PasswordHasher,
    Role,
    SessionTokenSigner,
)


@pytest.fixture
def authentication(isolated_settings, monkeypatch) -> AuthenticationService:
    """A service using the default admin credentials and a throwaway secret."""
    for variable_name in (
        "ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "GUEST_USERNAME", "GUEST_PASSWORD_HASH",
        "SESSION_SECRET", "SESSION_HOURS",
    ):
        monkeypatch.delenv(variable_name, raising=False)
    return AuthenticationService(isolated_settings)


class TestPasswordHasher:
    def test_a_hash_verifies_its_own_password(self):
        hasher = PasswordHasher()
        encoded = hasher.hash("rahasia")
        assert hasher.verify("rahasia", encoded)
        assert not hasher.verify("Rahasia", encoded)

    def test_the_same_password_hashes_differently_each_time(self):
        hasher = PasswordHasher()
        assert hasher.hash("rahasia") != hasher.hash("rahasia")

    def test_the_plain_password_never_appears_in_the_hash(self):
        assert "rahasia" not in PasswordHasher().hash("rahasia")

    @pytest.mark.parametrize("malformed", ["", "plain", "md5$1$aa$bb", "pbkdf2_sha256$x$aa$bb"])
    def test_malformed_hashes_are_rejected_not_raised(self, malformed):
        assert not PasswordHasher().verify("rahasia", malformed)


class TestSessionTokenSigner:
    def test_a_token_round_trips_to_its_principal(self):
        signer = SessionTokenSigner(b"kunci")
        principal = signer.verify(signer.issue("admin", Role.ADMIN, 60))
        assert principal.username == "admin"
        assert principal.is_admin

    def test_a_tampered_token_is_rejected(self):
        signer = SessionTokenSigner(b"kunci")
        guest_token = signer.issue("guest", Role.GUEST, 60)
        forged_body = SessionTokenSigner(b"kunci").issue("guest", Role.ADMIN, 60).split(".")[0]
        # A guest's signature attached to an admin body must not verify.
        assert signer.verify(f"{forged_body}.{guest_token.split('.')[1]}") is None

    def test_a_token_signed_with_another_key_is_rejected(self):
        token = SessionTokenSigner(b"kunci-lain").issue("admin", Role.ADMIN, 60)
        assert SessionTokenSigner(b"kunci").verify(token) is None

    def test_an_expired_token_is_rejected(self):
        signer = SessionTokenSigner(b"kunci")
        assert signer.verify(signer.issue("admin", Role.ADMIN, -1)) is None

    @pytest.mark.parametrize("garbage", [None, "", "tanpa-titik", "a.b.c", "!!!.???"])
    def test_garbage_is_rejected(self, garbage):
        assert SessionTokenSigner(b"kunci").verify(garbage) is None


class TestAuthenticationService:
    def test_the_default_admin_credentials_sign_in_as_admin(self, authentication):
        token = authentication.authenticate("admin", "1EkorAyam!")
        assert authentication.identify(token).role == Role.ADMIN

    def test_the_default_guest_credentials_sign_in_as_guest(self, authentication):
        principal = authentication.identify(authentication.authenticate("guest", "i am guest"))
        assert principal.username == "guest"
        assert principal.role == Role.GUEST
        assert not principal.is_admin

    def test_the_username_is_case_insensitive(self, authentication):
        assert authentication.authenticate("  Admin ", "1EkorAyam!") is not None
        assert authentication.authenticate("GUEST", "i am guest") is not None

    @pytest.mark.parametrize(
        ("username", "password"),
        [
            ("admin", "1ekorayam!"),
            ("admin", ""),
            ("guest", ""),
            ("guest", "I am guest"),
            # Each password only opens its own account.
            ("guest", "1EkorAyam!"),
            ("admin", "i am guest"),
            ("tamu", "i am guest"),
        ],
    )
    def test_wrong_credentials_are_refused(self, authentication, username, password):
        assert authentication.authenticate(username, password) is None

    def test_the_signing_secret_survives_a_restart(self, isolated_settings, authentication):
        token = authentication.authenticate("guest", "i am guest")
        assert AuthenticationService(isolated_settings).identify(token) is not None

    def test_the_secret_is_written_to_the_data_directory_only(self, isolated_settings, authentication):
        assert (isolated_settings.data_directory / "session-secret.key").exists()
        assert not list(isolated_settings.source_directory.rglob("*.key"))


class TestLoginThrottle:
    def test_locks_after_the_maximum_failures(self):
        throttle = LoginThrottle(maximum_failures=3, lockout_seconds=60)
        for _ in range(2):
            throttle.record_failure("10.0.0.1")
        assert throttle.seconds_locked("10.0.0.1") == 0
        throttle.record_failure("10.0.0.1")
        assert throttle.seconds_locked("10.0.0.1") > 0

    def test_other_addresses_are_unaffected(self):
        throttle = LoginThrottle(maximum_failures=1, lockout_seconds=60)
        throttle.record_failure("10.0.0.1")
        assert throttle.seconds_locked("10.0.0.2") == 0

    def test_success_clears_earlier_failures(self):
        throttle = LoginThrottle(maximum_failures=2, lockout_seconds=60)
        throttle.record_failure("10.0.0.1")
        throttle.record_success("10.0.0.1")
        throttle.record_failure("10.0.0.1")
        assert throttle.seconds_locked("10.0.0.1") == 0

    def test_failures_expire_after_the_window(self, monkeypatch):
        throttle = LoginThrottle(maximum_failures=1, lockout_seconds=60)
        throttle.record_failure("10.0.0.1")
        later = time.time() + 61
        monkeypatch.setattr("app.authentication.time.time", lambda: later)
        assert throttle.seconds_locked("10.0.0.1") == 0
