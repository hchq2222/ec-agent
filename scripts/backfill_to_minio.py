"""把本地 var/outputs 里的历史产物补传 MinIO，并更新 asset.object_key。

用法：
    python scripts/backfill_to_minio.py            # 预览
    python scripts/backfill_to_minio.py --apply    # 执行

幂等：已在 MinIO 中的对象会跳过。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from apps.core.config import get_settings  # noqa: E402
from apps.core.db import Database  # noqa: E402
from apps.core.models import Asset  # noqa: E402
from apps.core.storage import build_object_key, build_storage  # noqa: E402


async def run(apply: bool) -> int:
    settings = get_settings()
    storage = build_storage(settings)
    if storage.backend != "minio":
        print("❌ STORAGE_BACKEND 不是 minio，无需回填")
        return 1
    if not settings.database_url:
        print("❌ 未配置 DATABASE_URL")
        return 1

    db = Database(settings.database_url)
    moved = skipped = missing = 0
    try:
        async with db.session() as session:
            rows = (await session.execute(select(Asset))).scalars().all()
            print(f"assets: {len(rows)}")
            for a in rows:
                ext = a.mime.split("/")[-1].replace("jpeg", "jpg")
                target_key = build_object_key(asset_id=str(a.id), ext=ext)

                if storage.exists(target_key):
                    skipped += 1
                    print(f"skip   {target_key}")
                    continue

                src = Path(settings.output_dir) / a.object_key
                if not src.exists():
                    missing += 1
                    print(f"missing {a.object_key}（本地文件不存在）")
                    continue

                print(f"upload {a.object_key} -> {target_key}")
                if apply:
                    storage.put(target_key, src.read_bytes(), a.mime)
                    a.object_key = target_key
                moved += 1
            if apply:
                await session.commit()
    finally:
        await db.dispose()

    mode = "已执行" if apply else "预览（加 --apply 执行）"
    print(f"\n{mode}: 待/已上传 {moved}，跳过 {skipped}，本地缺失 {missing}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正执行上传与更新")
    raise SystemExit(asyncio.run(run(parser.parse_args().apply)))
