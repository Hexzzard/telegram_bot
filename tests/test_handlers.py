from agendaai.handlers import is_authorized


def test_allowlist_restricts_users():
    assert is_authorized(42, {42})
    assert not is_authorized(43, {42})


def test_empty_allowlist_is_open_for_initial_bootstrap():
    assert is_authorized(43, set())
