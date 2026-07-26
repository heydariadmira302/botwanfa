from pathlib import Path

import pytest

from botwanfa.backup_crypto import BackupIntegrityError, decrypt_file, encrypt_file


def test_encrypted_backup_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "dump.sql"
    encrypted = tmp_path / "dump.bwf"
    restored = tmp_path / "restored.sql"
    source.write_bytes((b"postgres-data\x00" * 100_000) + b"end")

    encrypt_file(source, encrypted, "correct horse battery staple")
    assert encrypted.read_bytes()[:4] == b"BWF1"
    assert b"postgres-data" not in encrypted.read_bytes()

    decrypt_file(encrypted, restored, "correct horse battery staple")
    assert restored.read_bytes() == source.read_bytes()


def test_wrong_backup_key_does_not_leave_plaintext(tmp_path: Path) -> None:
    source = tmp_path / "dump.sql"
    encrypted = tmp_path / "dump.bwf"
    restored = tmp_path / "restored.sql"
    source.write_text("secret database", encoding="utf-8")
    encrypt_file(source, encrypted, "correct horse battery staple")

    with pytest.raises(BackupIntegrityError):
        decrypt_file(encrypted, restored, "incorrect horse battery")
    assert not restored.exists()
