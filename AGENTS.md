# AGENTS.md

> 智能体**导航页**。这里只放"去哪看"，不复制正文；细节全部在下列文件里。
> 最后更新：2026-10-21

## 项目一句话

电商 AI 视觉工作台：输入商品 / SKU，输出可直接上架的一整套合规素材（图片 + 视频）。
核心立场：**商品一致性优先于美观，能"合成"就绝不"重绘"**。

## 去哪看

| 想了解 | 看这里 |
|---|---|
| 项目概览、文档索引、命名约定、护栏、关键指标 | [`README.md`](README.md) |
| 这一版做什么（M1–M9）、验收标准 | [`docs/MVP需求说明-电商视觉工作台.md`](docs/MVP需求说明-电商视觉工作台.md) |
| 完整需求（权威、只读） | [`docs/电商工作台需求说明.html`](docs/电商工作台需求说明.html) |
| 技术架构、数据模型、任务状态机、部署与失败处理 | [`docs/技术设计/TD-001-总体技术架构.md`](docs/技术设计/TD-001-总体技术架构.md) |
| 生图能力怎么接、怎么加模型/加渠道 | [`docs/技术设计/TD-002-生图能力接入.md`](docs/技术设计/TD-002-生图能力接入.md) |
| **中间件地址 / 账号名 / 证书 / 运维 / 已知坑** | [`infrastructure/测试环境中间件访问说明.md`](infrastructure/测试环境中间件访问说明.md)（**密码不在仓库**，见本地 `credentials.local.md`） |
| 当前进度、下一步、待决事项 | [`PHASE.md`](PHASE.md) |
| 可视化原型（非权威） | `prototype/index.html` |

## 连接信息在哪（速查）

| 需要什么 | 位置 |
|---|---|
| PostgreSQL 地址 / 账号 | 中间件访问说明 **§二 连接总表**；参数细节 **§四**；密码见本地 `infrastructure/credentials.local.md` |
| Redis 地址 / 账号 | 同上 **§二**；连接写法 **§五**；密码见本地凭据文件 |
| MinIO AccessKey / 控制台 | 同上 **§二**；桶规划 **§六**；SecretKey 见本地凭据文件 |
| 数据库超管 / Redis 管理账号 / SSH | 同上 **§二 管理账号**（密码同样在本地凭据文件） |
| CA 证书文件 | [`infrastructure/certs/ca.crt`](infrastructure/certs/ca.crt) |
| 运维命令（重启/备份/日志） | 同上 **§九** |
| 踩过的坑（ACL inode、CRLF、TLS） | 同上 **§十一** |

连接约定：**PG 与 Redis 都是明文**（PG 仍可走 TLS）；**MinIO 必须 TLS + path-style + 私有 CA**。库名用 `ec_agent_pro`（不是 `postgres`）。

## 本地怎么跑（生图服务）

```bash
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt   # Windows
.venv\Scripts\python -m alembic upgrade head                            # 建表（需要 DATABASE_URL）
.venv\Scripts\python -m uvicorn apps.api.main:app --reload --port 8000
.venv\Scripts\python scripts\smoke_image.py --list
```

密钥放 `.env`（见 `.env.example`），接口契约与扩展方式见 TD-002。

## 五条红线（细节见 README）

1. 一致性优先于美观，能合成就别重绘。
2. 商品事实只来自商家资料，AI 只整理、不补全。
3. 合规是产品能力：标识 + 留痕 + 审计 + 可回滚。
4. PostgreSQL 是唯一事实源，MinIO 是唯一二进制真源，Redis 可丢可重建。
5. 每次模型调用都必须落计费流水（否则单 SKU 成本无法验证）。

## 硬性禁止

- 不改 `docs/电商工作台需求说明.html`（上游权威输入）。
- 不擅自扩大 MVP 范围（清单见 `docs/MVP需求说明` §八）。
- 不动中间件数据：不 `FLUSHDB`、不删桶、不删卷、不改 `postgres` / `default` 账号。
- 不要把明文密码复制到仓库、issue、外部文档或聊天工具；真实值只在本地 `infrastructure/credentials.local.md`。
- 未经明确指令不写产品代码。

## 工作方式

- **有实质进展 → 追加到 [`PHASE.md`](PHASE.md)**（日期 + 做了什么 + 产物/证据 + 下一步），不要只留在对话里。
- 需求变更 → `docs/需求变更/YYYY-MM-DD-主题.md`（五段式）。
- 技术设计 → `docs/技术设计/TD-NNN-主题.md`（六段式）。
- 新增文档后同步更新 `README.md` 的索引。
