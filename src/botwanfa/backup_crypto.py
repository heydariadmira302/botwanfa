from __future__ import annotations

import argparse
import os
import struct
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"BWF1"
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
ARGON_TIME_COST = 3
ARGON_MEMORY_COST = 64 * 1024
ARGON_PARALLELISM = 4


class BackupIntegrityError(ValueError):
    pass


def derive_key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < 12:
        raise ValueError("备份密钥至少需要12个字符")
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=ARGON_TIME_COST,
        memory_cost=ARGON_MEMORY_COST,
        parallelism=ARGON_PARALLELISM,
        hash_len=32,
        type=Type.ID,
    )


def encrypt_file(source: Path, target: Path, passphrase: str) -> None:
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = derive_key(passphrase, salt)
    header = MAGIC + salt + nonce + struct.pack(">Q", source.stat().st_size)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("wb") as dst:
        dst.write(header)
        while chunk := src.read(CHUNK_SIZE):
            dst.write(encryptor.update(chunk))
        dst.write(encryptor.finalize())
        dst.write(encryptor.tag)


def decrypt_file(source: Path, target: Path, passphrase: str) -> None:
    header_size = len(MAGIC) + SALT_SIZE + NONCE_SIZE + 8
    file_size = source.stat().st_size
    if file_size < header_size + TAG_SIZE:
        raise BackupIntegrityError("备份文件格式不完整")
    with source.open("rb") as src:
        header = src.read(header_size)
        if header[: len(MAGIC)] != MAGIC:
            raise BackupIntegrityError("备份文件标识错误")
        cursor = len(MAGIC)
        salt = header[cursor : cursor + SALT_SIZE]
        cursor += SALT_SIZE
        nonce = header[cursor : cursor + NONCE_SIZE]
        expected_size = struct.unpack(">Q", header[-8:])[0]
        src.seek(-TAG_SIZE, os.SEEK_END)
        tag = src.read(TAG_SIZE)
        src.seek(header_size)
        remaining = file_size - header_size - TAG_SIZE
        decryptor = Cipher(
            algorithms.AES(derive_key(passphrase, salt)), modes.GCM(nonce, tag)
        ).decryptor()
        decryptor.authenticate_additional_data(header)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with target.open("wb") as dst:
                while remaining:
                    chunk = src.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise BackupIntegrityError("备份文件提前结束")
                    remaining -= len(chunk)
                    plain = decryptor.update(chunk)
                    dst.write(plain)
                    written += len(plain)
                dst.write(decryptor.finalize())
        except Exception as exc:
            target.unlink(missing_ok=True)
            if isinstance(exc, BackupIntegrityError):
                raise
            raise BackupIntegrityError("备份密钥错误或文件完整性校验失败") from exc
    if written != expected_size:
        target.unlink(missing_ok=True)
        raise BackupIntegrityError("备份文件长度校验失败")


def _passphrase() -> str:
    value = os.environ.get("BACKUP_PASSPHRASE", "")
    if not value:
        raise ValueError("环境变量 BACKUP_PASSPHRASE 为空")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("encrypt", "decrypt"))
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    action = encrypt_file if args.action == "encrypt" else decrypt_file
    action(args.source, args.target, _passphrase())


if __name__ == "__main__":
    main()
