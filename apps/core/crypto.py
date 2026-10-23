"""渠道密钥的对称加密（Fernet）。

密钥来自环境变量 CHANNEL_SECRET_KEY，经 SHA-256 派生为 Fernet key。
数据库里只存密文；接口只回显后 4 位。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(Exception):
    pass


def _fernet(secret: str) -> Fernet:
    if not secret:
        raise CryptoError("未配置 CHANNEL_SECRET_KEY")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(secret: str, plaintext: str) -> str:
    return _fernet(secret).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(secret: str, token: str) -> str:
    try:
        return _fernet(secret).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("渠道密钥无法解密（CHANNEL_SECRET_KEY 可能已更换）") from exc


def mask_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    tail = plaintext[-4:] if len(plaintext) >= 4 else plaintext
    return f"••••••••{tail}"
