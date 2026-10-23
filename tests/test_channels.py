"""渠道密钥加密与模型类型推断的单测。"""

from __future__ import annotations

import pytest

from apps.core.crypto import CryptoError, decrypt_secret, encrypt_secret, mask_secret
from apps.providers.base import ProviderError
from apps.providers.channels import guess_kind, normalize_base_url

SECRET = "test-secret-key"


def test_encrypt_decrypt_roundtrip():
    token = encrypt_secret(SECRET, "sk-abcdef123456")
    assert token != "sk-abcdef123456"
    assert decrypt_secret(SECRET, token) == "sk-abcdef123456"


def test_wrong_secret_cannot_decrypt():
    token = encrypt_secret(SECRET, "sk-abcdef")
    with pytest.raises(CryptoError):
        decrypt_secret("another-secret", token)


def test_mask_keeps_last_four():
    masked = mask_secret("sk-5eGx0PAkCEQat9IV")
    assert masked.endswith("t9IV")
    assert masked.startswith("•")
    assert "5eGx" not in masked
    assert mask_secret("") == ""


@pytest.mark.parametrize(
    ("model_id", "kind"),
    [
        ("doubao-seedance-2-0-260128", "video"),
        ("veo-3.1-fast", "video"),
        ("sora-2", "video"),
        ("gpt-image-2", "image"),
        ("doubao-seedream-5-0-pro-260628", "image"),
        ("flux-2-pro", "image"),
        ("nano-banana-pro", "image"),
        ("some-unknown-model", "image"),
    ],
)
def test_guess_kind(model_id: str, kind: str):
    assert guess_kind(model_id) == kind


def test_normalize_base_url():
    assert normalize_base_url("https://omyai.vip/v1/") == "https://omyai.vip/v1"
    with pytest.raises(ProviderError):
        normalize_base_url("omyai.vip/v1")
