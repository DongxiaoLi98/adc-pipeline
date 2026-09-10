# ADC Pipeline — Step 1–6 数据库与储存设计
## Review 说明（中文对照版）

> 本文是 `FOR_REVIEW.md` 的中文对照版，章节一一对应。表名、字段、命令、URL 保持英文。

本包是 ADC pipeline Step 1–6 输出的数据库/储存设计，外加一份参考实现骨架。它是**供 review 的设计 + 骨架**，不是已完工的生产服务。

---

### 1. 这是什么（30 秒版）

按之前的讨论，AWS 托管服务放到**未来云迁移路线图**。当前阶段基于 **Supabase Local / PostgreSQL** + 一个 S3 兼容对象存储来做，并设计成：以后迁到 **AWS（RDS + S3 + Athena/Glue + 可选 DynamoDB）**只是改配置，不是重新设计。

一句话核心：每个 pipeline step 的完整输出原样存成 JSONB"真相文档"；稳定字段投影进类型化关系表供查询；大文件放对象存储、库里只存指针；所有写入幂等（同一步重跑结果一致，绝不产生重复）。

---

### 2. 读什么、按什么顺序（约 15 分钟）

所有设计文档在 `docs/`，建议按顺序读：

1. **`docs/00_overview_and_decisions.md`** — 三层模型 + 六条架构决策（ADR）。**只读一份就读这份。**
2. `docs/01_data_model.md` — 每个 step 的数据怎么存；标识符策略。
3. `docs/02_read_write_actions.md` — 数据怎么写/读；幂等保证；文件上传怎么处理。
4. `docs/03_aws_migration_roadmap.md` — AWS 映射与切换步骤。
5. `docs/04_ops_and_open_questions.md` — 治理、安全，以及需要你意见的未决问题。

数据库 schema 本身（唯一真相源）是 `supabase/migrations/0001_adc_step_1_6_schema.sql`（25 张表）。参考代码在 `app/db/`，测试在 `tests/`。

---

### 3. 希望你签字确认的决策

以下是承重的关键选择（完整理由见 `docs/00`）：

| # | 决策 | 为什么 |
|---|---|---|
| 1 | 当前阶段用 **Supabase Local / PostgreSQL** | Postgres + S3 兼容存储 + SQL migration，本地免费，与 RDS + S3 一一映射 |
| 2 | **不使用 MinIO** | 其开源版已不再维护、镜像不再发布；受支持的产品是企业定价。本地用 Supabase Storage，严格 S3 测试用 LocalStack |
| 3 | **JSONB 真相文档 + 关系投影** | 容忍仍在漂移的上游 schema 而不丢数据 |
| 4 | **枚举现阶段存为 TEXT** | 枚举值集合仍在变；Postgres 原生 enum 改起来很痛苦 |
| 5 | **run-scoped 标识符**（复合键） | `candidate_id` 等只在 run 内唯一 |
| 6 | **幂等写入** | 同一步重跑不能产生重复行 |

---

### 4. 怎么跑（可选，约 5 分钟）

review 不必跑，但如果你想试：

```bash
supabase init && supabase start          # 本地 Postgres + Storage
supabase db reset                        # 应用 schema
pip install -r requirements.txt
pytest tests/test_ids.py                 # 单元测试，无需数据库
# 完整套件（需要 DB 起着）：
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres
pytest
```

---

### 5. 需要你拍板的未决问题

（完整清单在 `docs/04`。）主要几条：

1. **保留期** — 完整 step 输出和 artifacts 存多久后归档到更便宜的存储？
2. **访问控制 / PII** — intake 记录带有提交者身份；本阶段 user/project 级访问要多严？
3. **何时迁 AWS** — 是否有触发条件（数据量、用户数、资金）来搭建 AWS account？

---

### 6. 范围与诚实状态

**在范围内 / 已完成：** Step 1、2、3、5、6 —— schema、读写逻辑、幂等、artifact 处理、AWS 迁移映射，以及上述设计文档。单元测试通过；集成测试针对本地 Postgres 运行。

**暂未包含（有意为之）：** Step 4/7/13/14 及其 `scores` / `rankings` / `ip_record` 表（留待以后）；REST/API 层（本里程碑是数据库层）；生产部署 / CI。少量上游 payload 字段名需要在对齐时对照当前代码确认。

我很乐意就其中任何部分做讲解，或根据你的反馈调整方向。
