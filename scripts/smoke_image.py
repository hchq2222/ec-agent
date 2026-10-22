#!/usr/bin/env python
"""命令行冒烟：列模型 / 真实生成一张图。

用法：
    python scripts/smoke_image.py --list
    python scripts/smoke_image.py --model gpt-image-2 --prompt "一只白色陶瓷马克杯，纯白背景，电商主图" --size 1024x1024
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows 中文控制台默认 GBK，打印 ✅ / ❌ 会抛 UnicodeEncodeError（此时图片已落盘）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from apps.core.config import get_settings  # noqa: E402
from apps.core.storage import LocalStorage  # noqa: E402
from apps.providers.base import ImageRequest, ProviderError  # noqa: E402
from apps.providers.registry import Registry  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    reg = Registry.from_file(settings.providers_config)
    try:
        if args.list:
            for m in reg.catalog(kind=args.kind):
                caps = m["capabilities"]
                print(
                    f"{m['key']:<26} provider={m['provider']:<10} "
                    f"n<= {caps['max_n']:<2} sizes={','.join(caps['sizes']) or '-'} "
                    f"ref={'Y' if caps['image_input'] else 'N'}"
                )
            return 0

        res = await reg.generate(
            ImageRequest(
                prompt=args.prompt,
                model_key=args.model,
                n=args.n,
                size=args.size,
                quality=args.quality,
                output_format=args.output_format,
                images=args.image or [],
            )
        )

        storage = LocalStorage(Path(args.out))
        for idx, img in enumerate(res.images):
            ext = img.mime.split("/")[-1].replace("jpeg", "jpg")
            obj = storage.save(img.data, ext=ext, prefix=f"{args.model}_{idx}")
            print(f"✅ saved: {obj.path}  ({obj.bytes} bytes, {img.mime})")

        print(
            f"provider={res.provider_id} model={res.model_key} "
            f"latency={res.latency_ms}ms usage={res.usage}"
        )
        return 0
    except ProviderError as exc:
        print(f"❌ {exc.code.value}: {exc.message}", file=sys.stderr)
        if exc.code.value == "AUTH_FAILED":
            print("   检查 .env 里的 OMYAI_API_KEY 是否有效。", file=sys.stderr)
        return 2
    finally:
        await reg.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="生图接口冒烟脚本")
    parser.add_argument("--list", action="store_true", help="只列出可用模型")
    parser.add_argument("--kind", default="image", help="模型类型：image / video")
    parser.add_argument("--model", default="gpt-image-2", help="模型 key")
    parser.add_argument("--prompt", default="一只白色陶瓷马克杯，纯白背景，电商主图", help="提示词")
    parser.add_argument("--n", type=int, default=1, help="生成张数")
    parser.add_argument("--size", default=None, help="尺寸，如 1024x1024")
    parser.add_argument("--quality", default=None, help="auto / low / medium / high")
    parser.add_argument("--output-format", default=None, help="png / jpeg / webp")
    parser.add_argument("--image", action="append", help="参考图（URL / data URL / base64），可重复")
    parser.add_argument("--out", default="var/outputs", help="产物目录")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
