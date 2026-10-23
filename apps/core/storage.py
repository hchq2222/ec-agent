"""产物存储。

两种后端，按 STORAGE_BACKEND 选择：
    local  本地磁盘（开发兜底，URL 走 /files）
    minio  S3 协议（TD-001 的二进制真源，URL 走预签名）

DB 只存 object_key，不存 URL；URL 在读取时现签。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import boto3
from botocore.config import Config


def build_object_key(
    *,
    asset_id: str,
    ext: str,
    tenant_id: str = "default",
    variant: str = "generated",
    now: datetime | None = None,
) -> str:
    """TD-001 §3.2 约定：{tenant}/{yyyy}/{mm}/{asset_id}/{variant}.{ext}"""
    now = now or datetime.now(timezone.utc)
    return f"{tenant_id}/{now:%Y/%m}/{asset_id}/{variant}.{ext.lstrip('.')}"


@dataclass
class StoredObject:
    key: str
    bytes: int


class Storage(Protocol):
    backend: str

    def put(self, key: str, data: bytes, mime: str) -> StoredObject: ...

    def url_for(self, key: str) -> str: ...

    def exists(self, key: str) -> bool: ...


class LocalStorage:
    """本地磁盘，开发兜底。key 直接当相对路径用。"""

    backend = "local"

    def __init__(self, root: Path, public_base_url: str) -> None:
        self.root = Path(root)
        self.public_base_url = public_base_url.rstrip("/")

    def put(self, key: str, data: bytes, mime: str) -> StoredObject:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return StoredObject(key=key, bytes=len(data))

    def url_for(self, key: str) -> str:
        return f"{self.public_base_url}/files/{key}"

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    # 兼容旧调用（第一版 API 用过）
    def save(self, data: bytes, ext: str, prefix: str = "img") -> StoredObject:
        now = datetime.now(timezone.utc)
        key = f"{now:%Y/%m/%d}/{prefix}_{secrets.token_hex(8)}.{ext.lstrip('.')}"
        return self.put(key, data, "application/octet-stream")

    def public_url(self, base_url: str, key: str) -> str:
        return f"{base_url.rstrip('/')}/files/{key}"


class MinioStorage:
    """MinIO / S3 兼容存储。强制 TLS + 私有 CA 也能用（verify 传 ca 路径）。"""

    backend = "minio"

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        ca_bundle: str | None = None,
        presign_ttl: int = 900,
    ) -> None:
        self.bucket = bucket
        self.presign_ttl = presign_ttl
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            verify=ca_bundle or True,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put(self, key: str, data: bytes, mime: str) -> StoredObject:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=mime)
        return StoredObject(key=key, bytes=len(data))

    def url_for(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.presign_ttl,
        )

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


def build_storage(settings) -> Storage:
    """按配置选择存储后端。minio 缺少关键参数时直接失败，不静默降级。"""
    backend = (settings.storage_backend or "local").lower()
    if backend == "minio":
        missing = [
            name
            for name, value in {
                "MINIO_ENDPOINT": settings.minio_endpoint,
                "MINIO_ACCESS_KEY": settings.minio_access_key,
                "MINIO_SECRET_KEY": settings.minio_secret_key,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"storage_backend=minio 但缺少配置: {', '.join(missing)}")
        return MinioStorage(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket_assets,
            region=settings.minio_region,
            ca_bundle=settings.minio_ca_bundle or None,
            presign_ttl=settings.minio_presign_ttl,
        )
    return LocalStorage(Path(settings.output_dir), settings.public_base_url)