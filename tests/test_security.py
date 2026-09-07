from attendance_hub.security import new_password_record, token_digest, verify_password


def test_password_hash_and_verification():
    salt, digest = new_password_record("a long private password")
    assert salt and digest
    assert verify_password("a long private password", salt, digest)
    assert not verify_password("wrong password", salt, digest)


def test_token_digest_is_deterministic_and_not_plaintext():
    assert token_digest("secret") == token_digest("secret")
    assert token_digest("secret") != "secret"
