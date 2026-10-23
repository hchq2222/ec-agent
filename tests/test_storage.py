"""存储层单测：key 规范与预签名（不访问网络）。"""

from __future__ import annotations

from datetime import datetime, timezone

from apps.core.storage import MinioStorage, build_object_key


def test_object_key_format():
    key = build_object_key(
        asset_id="0cd7f955-8c11-4f7c-91f1-3e5f3b4195af",
        ext="png",
        now=datetime(2026, 10, 23, tzinfo=timezone.utc),
    )
    assert key == "default/2026/10/0cd7f955-8c11-4f7c-91f1-3e5f3b4195af/generated.png"


def test_object_key_strips_dot():
    key = build_object_key(asset_id="a", ext=".jpg", now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert key == "default/2026/01/a/generated.jpg"


def test_presigned_url_offline():
    storage = MinioStorage(
        endpoint="https://127.0.0.1:9000",
        access_key="ak",
        secret_key="sk",
        bucket="assets",
        presign_ttl=900,
    )
    url = storage.url_for("default/2026/10/x/generated.png")
    assert url.startswith("https://127.0.0.1:9000/assets/default/2026/10/x/generated.png")
    assert "X-Amz-Signature=" in url
