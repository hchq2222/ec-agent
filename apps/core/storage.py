"""产物存储。

MVP：本地磁盘。接口保持窄：只依赖 save()，后续接 MinIO / TOS 时替换实现即可。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class StoredObject:
    key: str          # 相对 key，例如 2026/10/22/img_xxx_0.png
    path: Path
    bytes: int


class LocalStorage:
    """本地文件存储，目录结构按 年/月/日 分层，便于清理与迁移。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def save(self, data: bytes, ext: str, prefix: str = "img") -> StoredObject:
        now = datetime.now(timezone.utc)
        rel_dir = Path(f"{now:%Y/%m/%d}")
        token = secrets.token_hex(8)
        name = f"{prefix}_{now:%H%M%S}_{token}.{ext.lstrip('.')}"
        rel_key = rel_dir / name

        target = self.root / rel_key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return StoredObject(key=str(rel_key).replace("\\", "/"), path=target, bytes=len(data))

    def public_url(self, base_url: str, key: str) -> str:
        return f"{base_url.rstrip('/')}/files/{key}"
