import re

from agendaai.receipt_service import hash_code, make_code


def test_code_has_six_digits():
    for _ in range(100):
        code = make_code()
        assert re.fullmatch(r"[0-9]{6}", code)


def test_same_information_produces_same_hash():
    first = hash_code(
        "test-secret",
        123,
        "usuario@example.com",
        "123456",
    )

    second = hash_code(
        "test-secret",
        123,
        "usuario@example.com",
        "123456",
    )

    assert first == second


def test_different_users_have_different_hashes():
    first = hash_code(
        "test-secret",
        123,
        "usuario@example.com",
        "123456",
    )

    second = hash_code(
        "test-secret",
        456,
        "usuario@example.com",
        "123456",
    )

    assert first != second


def test_different_codes_have_different_hashes():
    first = hash_code(
        "test-secret",
        123,
        "usuario@example.com",
        "123456",
    )

    second = hash_code(
        "test-secret",
        123,
        "usuario@example.com",
        "654321",
    )

    assert first != second
