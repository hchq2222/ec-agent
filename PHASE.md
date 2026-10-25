# PHASE.md · 项目进度

> 进度日志。**有实质进展就追加一条**，只记"做了什么 + 产物/证据 + 下一步"，不复述设计（设计看 `docs/技术设计/`）。
> 最后更新：2026-10-22

---

## 一、当前阶段

| 项 | 状态 |
|---|---|
| 阶段 | **P0 技术验证中**（架构与中间件已完成，核心验证未做） |
| 代码 | 生图服务（多渠道/加密/持久化/MinIO）、生图工作台、商品档案（手工+链接导入）已落地；**29 项单测通过** |
| 中间件 | 已就绪并通过外网实测，见 `infrastructure/测试环境中间件访问说明.md` |
| 当前卡点 | 地基未落：商品表、素材「槽位+版本」、`job_item`、登录/审计、`price_book` 均未实施 |
| 下一关 | 按 TD-004 顺序实施 `0003`–`0007`，再做 8 图模板定义与 10 SKU 实测 |

---

## 二、里程碑（周期对齐 `docs/MVP需求说明` §九）

| 阶段 | 周期 | 目标 | 状态 |
|---|---|---|---|
| **P0 · 技术验证** | 2 周 | 不写产品代码；手工跑 10 个真实 SKU，测一次通过率与单 SKU 成本 | 🟡 架构/中间件完成，验证未做 |
| **P1 · 地基** | 2 周 | M1 商品档案 + M3 抠图白底 + 素材存储 | ⬜ 未开始 |
| **P2 · 核心链路** | 3 周 | M4 场景图 + M5 主图 + M6 画布与文字替换 | ⬜ 未开始 |
| **P3 · 交付闭环** | 2 周 | M7 批量任务 + M8 审核交付 | ⬜ 未开始 |
| **P4 · 视频** | 2 周 | M9 15s 展示视频 | ⬜ 未开始 |
| **P5 · 真实跑批** | 1 周 | 3 个商家、20 个 SKU 实测，出验收报告 | ⬜ 未开始 |

状态图例：⬜ 未开始 · 🟡 进行中 · ✅ 完成 · 🔴 阻塞

---

## 三、Go / No-Go 指标（不达标就停，不许加功能）

| 指标 | 阈值 | 当前实测 |
|---|---|---|
| 一次通过率（不返修即可用） | ≥ 60% | 未测 |
| 单 SKU 全套成本（8 图 + 15s 视频） | ≤ ¥15 | 未测 |
| 单 SKU 交付时长（含人工确认） | ≤ 30 分钟 | 未测 |
| 商家愿意上架比例（20 SKU） | ≥ 80% | 未测 |

---

## 四、P0 待办清单

### 4.1 渠道与模型实测

- [ ] 模型完整度：是否同时具备 Seedream 5.0（pro/flash）与 Seedance 2.0
- [ ] 生图响应模式：同步还是提交+轮询；首次响应耗时
- [ ] 图层拆分：传 `layer_decomposition` 是否返回 `z_index` / `bounding_box`
- [ ] bbox 交互编辑：能否只改指定区域
- [ ] 视频 draft 档：时延、画质差、实际计费差
- [ ] 尾帧连续性：`return_last_frame` 接续下一段是否跳变
- [ ] 输入限制：base64 上限、参考图张数上限、单图大小上限
- [ ] 限流与并发：IPM / RPM / 同时在跑任务数
- [ ] 失败退款：失败是否扣费、账单能否导出
- [ ] 产物有效期：URL 实际有效期与下载次数限制
- [ ] 错误码映射：400 / 401 / 429 / 5xx / 内容拦截
- [ ] 数据条款：是否用于训练、留存时长

> 产出：填写报价表（单价按张 / 秒 / token），并确定渠道优先级与能力矩阵。

### 4.2 一致性路径验证

- [ ] 10 张真实主图测图层拆分：**拆分无损 diff ≈ 0**、文字落层命中率
- [ ] 拆分不达标时，回落 OCR + inpainting 的效果与成本
- [ ] 抠图 + 合成路径（L1）在白底/场景图上的通过率
- [ ] 一致性校验两项硬指标（CLIP 相似度 + OCR 比对）阈值标定

### 4.3 商业验证

- [ ] 3 个真实商家、10 个真实 SKU，手工跑通"图 + 视频"
- [ ] 素材实际上架，确认是否被判违规
- [ ] 单 SKU 成本实测（含重试）
- [ ] 三个产品决策拍板（见 §六）

---

## 五、进度日志

### 2026-10-21

- ✅ **需求**：完整需求地图（32 功能 / 14 品类）裁剪为 MVP 的 M1–M9，明确 10 条不做。
- ✅ **技术架构**：分层架构、中间件职责边界、数据模型（原 9 表 + 补 5 表）、任务状态机与对账器、部署拓扑、失败处理矩阵、架构验收标准 → `docs/技术设计/TD-001-总体技术架构.md`。
- ✅ **中间件**：测试服务器（`203.0.113.10`）PostgreSQL 17 / Redis 7.4 / MinIO 就绪；新增 PG 角色 `dianshang` + schema `app`、Redis ACL 用户、四个业务桶（`raw`/`work`/`assets`/`export`）及生命周期；MinIO 强制 TLS（PG / Redis 同日后改为明文）；从外网完成 PG 读写、Redis roundtrip、MinIO put/get、TLS 握手验证 → `infrastructure/测试环境中间件访问说明.md`。
- ✅ **文档结构**：`README.md` 作为项目说明与文档索引，`AGENTS.md` 只做导航，本文件记录进度。
- ✅ **凭据脱敏**：真实密码移入本地 `infrastructure/credentials.local.md`（已被 `*.local.md` 忽略），仓库文档改为 `${XXX_PASSWORD}` 占位符；重写提交历史并强推，远端 `main` 已无任何明文密码。
- 🟡 **渠道接入方案**：曾完成一版渠道抽象 / 能力矩阵 / 产物转存 / 计费对账设计，文档已移除；**结论暂时保留**（聚合渠道为主 + 官方按能力兜底，产物必须同一步转存），具体方案待后续重新确定。
- ✅ **PostgreSQL 放开明文**：按需求把 `pg_hba.conf` 的 `hostnossl` 从 `reject` 改为 `scram-sha-256`（改用原地覆盖 + `chmod 644` + reload；TLS 保持可用），外网已验证 `sslmode=disable` 可连、错误密码仍被拒。
- ✅ **Redis 改为明文**：`redis.conf` 的 `port 0` → `6379`、`tls-port` 关闭，compose 健康检查改为 `redis-cli ping` 并重建容器；AOF 数据完好，外网明文读写、错误密码拒绝、匿名拒绝均验证通过。
- ✅ **文档结构整理**：`README.md` 移至根目录并改为项目索引；`AGENTS.md` 精简为导航页（只留"去哪看"）；新增 `PHASE.md`；恢复 `infrastructure/测试环境中间件访问说明.md`；清理 `TD-001` 中对已移除文档的引用。
- ⬜ **未开始**：图层拆分验证。

### 2026-10-25

- ✅ **商品档案原型重做（按 TD-004）**：列表改表格（搜索 / 品类 / 状态 / 来源筛选、待补聚合条、价格区间、素材与受影响）；详情拆 6 区（基础 / 品类必填 / SKU / 图片与证据 / 变更影响 / 审计）；SKU 矩阵生成 + 批量改价；字段来源四态与「拦生成 / 仅拦交付」分级。
- ✅ **阻断逻辑改造**：`canGenerate` 只看 `block_scope=generate` 的字段，`delivery` 字段不再阻塞生成；制作台阻断提示同步。
- ✅ **深链**：`#products/{id}/{tab}` 可直接打开详情与页签，便于评审与自动化截图。
- ⚠️ 原型阶段：商品仍存 localStorage；后端 `product/sku` 表与 `0003` 迁移待实施。

### 2026-10-23

- ✅ **补上持久化**：新增 `apps/core/db.py`、`apps/core/models.py`（`job` / `asset` / `provider_call` 三表）与 Alembic 迁移 `migrations/versions/0001_init.py`。
- ✅ **迁移已应用**：测试库 `ec_agent_pro` 的 `app` schema 下现有 `job`、`asset`、`provider_call`、`alembic_version` 四张表（`version_num=0001_init`）。
- ✅ **真实出图落库验证**：`gpt-image-2`、quality=low、1024×1024，耗时 38.6s；`job` / `asset` / `provider_call` 各 1 行，`GET /api/jobs/{id}` 可回查，产物 748 KB。
- ✅ **失败也记录**：渠道异常时写 `job.failed` + `provider_call(failed, units=0)`；写库失败不影响已生成产物（响应 `persisted=false`）。
- ✅ **MinIO 存储**：`STORAGE_BACKEND=minio`，新图直接写 `assets` 桶，key 为 `{tenant}/{yyyy}/{mm}/{asset_id}/generated.png`，返回 900s 预签名 URL；本地盘只在无 MinIO 时兜底。
- ✅ **历史产物回填**：`scripts/backfill_to_minio.py --apply` 把本地 1 张历史图补传 MinIO 并更新 `asset.object_key`；回填图与新生成的图均可通过预签名 URL 下载（200，PNG 头正确）。
- ✅ **原型：渠道与模型管理**：设置页新增渠道（名称 / 协议 / Base URL / API Key / 测试拉取模型），支持启用停用、删除、同步；制作台的场景图 / 主图 / 短视频各自可选「渠道 + 模型」，模型按 `kind` 过滤。数据存 `localStorage`，同步为模拟。
- ✅ **后端：多渠道管理**：新增 `app.channel` 表（迁移 `0002_channel`）；API Key 用 Fernet 加密（`CHANNEL_SECRET_KEY`），接口只回显后 4 位；提供 `GET/POST/PATCH/DELETE /api/channels` 与 `/sync`；`POST /api/images/generate` 支持 `channel_id`，按渠道动态构造 provider；`GET /api/models?channel_id=` 合并渠道模型。
- ✅ **渠道实测**：真实 omyai 渠道同步到 3 个模型；用 `channel_id` 生成成功，`job.provider_id` 与 `provider_call.provider_id` 均为渠道 UUID；DB 里 `api_key_enc` 为 Fernet 密文，非明文。
- ✅ **生图工作台（原型）**：新增三栏页面（生成记录 / 参数面板 / 结果区），对标参考工作台；直接调真实后端 `/api/channels`、`/api/models`、`/api/images/generate`，支持参考图、渠道+模型、质量、W/H、分辨率、宽高比、张数；深链 `#genstudio`。
- ✅ **辅助接口**：`GET /api/jobs?limit=`（生成记录，含 prompt）、`GET /api/assets/{id}/content`（图片中转）。
- ✅ **开发模式图片代理**：`STORAGE_URL_MODE=proxy`，图片走 API 中转，绕开自签 CA 导致浏览器白图的问题；生产切回 `presign`。
- ✅ **图生图链路打通**：参考图 data URL → `POST /api/images/generate` → 上游 `/images/edits`（`image[]`、`output_format=png` 均可用）→ 结果落 MinIO（1024×1024 / 812 KB）。⚠️ 实测商品被重绘（杯型/手柄与原图不同），确认图生图不能替代 L1 合成，需 L3 校验兜底。
- ✅ **生成记录删除**：`DELETE /api/jobs/{id}` 同时清理 DB 行与 MinIO 对象；前端历史项加 ×（二次确认）。实测删除后列表 5→4、原图 URL 200→404、job 详情 404。
- ✅ **结果互通**：制作台与画布编辑器增加「最近生成」横条（`GET /api/jobs?limit=8`），点击弹窗预览；导航调整为 商品档案 → 生图工作台 → 制作台。
- ✅ **商品链接导入**：后端 `POST /api/products/parse-link`（JSON-LD → OpenGraph → 标题+价格兜底，含 SSRF 防护）；前端商品档案新增「链接导入」弹窗，解析后可编辑、选品类、一键录入，字段标 AI 待确认；新建/导入的商品写 localStorage 并记 audit。离线单测覆盖三种提取路径，SSRF 已验证。
- ⚠️ **计费口径**：omyai 的 `usage` 是 token 口径（本次 output image_tokens=272），`price` 未回填，`amount` 暂为空，待对账后填 `price_book`。
- ⬜ **未做**：token→金额换算、前端接真实接口、图层拆分验证。
- ✅ **架构决策定稿（四场专题讨论）**：生图流程 / 商品录入 / 素材界面 / 系统功能四份讨论合并，六条决策全部取 B：
  1. 素材 = **槽位 + 版本**（`asset` / `asset_version`）
  2. 删除 = **软删 30 天回收站**，原图永不物理删
  3. MVP 登录 = **单 admin + Cookie 会话**
  4. 字段阻断 = **`block_scope` 分级**（generate / delivery）
  5. 成本口径 = **含返工/废弃/视频 + 汇率快照**
  6. 制作台与生图工作台 = **上下游**，工作台产物进「未归档」
  → `docs/技术设计/TD-004-商品素材与生产流程.md`、`docs/需求变更/2026-10-23-系统与数据模型定稿.md`
- ⬜ **下一步（地基顺序不可颠倒）**：`0003` 商品 → `0004` 系统（登录/审计）→ `0005` 计费 → `0006` 素材重构 → `0007` 任务上下文。

### 2026-10-22

- ✅ **生图接入层**：落地 `apps/`（FastAPI + Provider/Model 注册表 + OpenAI 兼容 adapter），模型与渠道由 `config/providers.yaml` 驱动，新增模型只改 YAML → `docs/技术设计/TD-002-生图能力接入.md`。
- ✅ **已注册模型**：`gpt-image-2`、`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`（来自 omyai 公开 `/api/pricing`）。
- ✅ **接口**：`GET /api/models`、`POST /api/images/generate`、`/files` 静态产物；统一错误码 9 个；产物落本地 `var/outputs`。
- ✅ **测试**：11 个单测通过（成功/URL 下载/401/429/内容拦截/多图/能力校验），均为 MockTransport，不产生费用。
- ✅ **渠道鉴权与真实出图已验证（2026-10-22）**：轮换密钥后 `GET /v1/models` 返回 200，3 个已注册模型均在渠道列表；`gpt-image-2`（quality=low，1024x1024，n=1）出图成功。耗时与 usage 未记录，计费待对账。
- ✅ **状态复核**：使用 `.venv\Scripts\python.exe -m pytest -q` 复跑 11 项单测，全部通过（MockTransport，无渠道请求）；顶部项目状态已与现有代码记录对齐。
- ⬜ **未做**：前端原型接真实接口、图生图实测、计费口径回填。

---

## 六、待决事项

> 2026-10-23 已定稿：素材模型、删除语义、MVP 登录、字段阻断、成本口径、制作台与工作台关系（见 TD-004）。下表只列**仍未拍板**的事项。

| # | 事项 | 影响 |
|---|---|---|
| 1 | MVP 是否只做标品（美妆/家居/3C），不做服装 | 做服装需加模特与试穿，工期 +3 周且通过率下降 |
| 2 | 视频是否进 MVP | 进，成本结构完整；不进，M9 后移，工期 -2 周 |
| 3 | 是否接受 MVP 单租户、无权限体系 | 接受可省约 2 周 |
| 4 | 聚合渠道选哪家 | 决定价格、限流、能力可用性、数据条款 |
| 5 | ~~渠道鉴权待重新验证~~ 已于 2026-10-22 解决 | 剩余：各 quality 档耗时与单价实测，回填 `providers.yaml` 的 `pricing.price` |
| 6 | 生图渠道的计费口径 | 实测 `usage` 为 token 口径（output image_tokens=272），非按张；需拿渠道账单换算成单张成本，回填 `price_book` |

---

## 七、写进度日志的格式

```markdown
### YYYY-MM-DD

- ✅ / 🟡 / ⬜ **主题**：做了什么；产物或证据（文件路径 / 实测结果 / 数字）。
- ⬜ **下一步**：谁在什么时候做、卡在哪。
```

> 原则：**能指向文件就不复述内容**；数据要带来源（实测 / 估算 / 待测）。
