# Alembic 迁移（schema 双轨统一后的决策记录，问题8）

## 轨道选择（二选一，入口都在 `app.db.init_db`）

| 轨道 | 触发 | 适用 |
|---|---|---|
| **alembic** | `SC_USE_ALEMBIC=1`（env） | 生产/多实例：schema 变更可追踪、可回滚 |
| **create_all** | 默认（未设 SC_USE_ALEMBIC） | 测试 fixture、本地新库 |

两条轨共用同一份 SQLAlchemy 模型；**改 schema 时两条轨都要照顾**：

1. 新增可空列/新表 → 模型加字段 + 新建一个 alembic 迁移文件
   （`alembic revision --autogenerate -m "..."`，生成后人工核对 diff）。
   create_all 轨自然从模型拿到新列，无需其它。
2. 既有表加 NOT NULL 列 → 同上，但**迁移文件的 `op.add_column` 必须带 server_default**，
   且 create_all 轨需要幂等补列（复制 `scripts/migrate_*` 的
   `_legacy_alter_bootstrap` 模式——create_all 不给已有表加列）。
3. 从不回退：迁移一经合入不可修改（新库直接走到 head；生产在旧库上继续加迁移）。

## 初始迁移说明

`2548b632b722_initial_schema` 是历次 create_all 时代的全量 schema 快照，已含

- `knowledge_point.archived`（kb-edit §3.1）；
- `parse_batch_item.started_at`（G6 看门狗计时列）。

它们在 create_all 轨的存量库上由 `app.db._legacy_alter_bootstrap` 幂等补齐
（曾位于 main.py lifespan，问题8 收进 init_db 后单一入口）。

## 存量库切 alembic

已有 create_all 时代数据的库想启用 alembic：

```bash
cd backend
SC_USE_ALEMBIC=1 python -m alembic stamp 2548b632b722   # 以初始迁移为基线
SC_USE_ALEMBIC=1 python -m alembic upgrade head
```

`env.py` 的 SQLAlchemy url 取自 `app.config.settings.database_url`，与本项目 runtime 一致。