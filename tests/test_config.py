from botwanfa.config import Settings


def test_super_admin_ids_accept_single_int_from_env_decoder() -> None:
    settings = Settings(super_admin_ids=510092936)
    assert settings.super_admin_ids == (510092936,)


def test_super_admin_ids_accept_comma_separated_string() -> None:
    settings = Settings(super_admin_ids="510092936,123456789")
    assert settings.super_admin_ids == (510092936, 123456789)
