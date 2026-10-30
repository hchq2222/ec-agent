# 基础设施（公开版）

本目录只保留可公开的本地依赖与配置说明。**真实测试环境地址、账号、密码、证书和运维命令不在仓库里**：请放在本地 `infrastructure/credentials.local.md`（已被 `.gitignore` 的 `*.local.md` 忽略），或使用你自己的密钥管理系统。

## 依赖

- PostgreSQL 16+（业务表在 `app` schema；连接串示例见 `.env.example`）
- Redis 7+（可选 TLS）
- MinIO（S3 协议；建议 TLS + path-style）
- Python 3.12+
- Node 18+（仅前端构建需要）

## 配置

复制 `.env.example` 为 `.env`，填入自己的值。常用项：

```ini
DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:5432/ec_agent_pro
REDIS_URL=redis://<host>:6379/0
MINIO_ENDPOINT=https://<host>:9000
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_CA_BUNDLE=
DEEPSEEK_API_KEY=
```

如果 MinIO 使用自建 CA，把 CA 文件放在本地路径，并让 `MINIO_CA_BUNDLE` 指向它；仓库不附带任何测试环境证书。

## 本地启动

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn apps.api.main:app --reload --port 8000
```

浏览器打开 `prototype/index.html`，或按 `.env` 里的 `PUBLIC_BASE_URL` 部署。

## 数据库迁移

```bash
python -m alembic upgrade head
```

迁移脚本在 `migrations/versions/`。新增表结构请一并补迁移和测试。
