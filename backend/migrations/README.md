# 结构说明

`src/models/tables.py` 定义初始关系结构。API 启动时显式创建演示库的表；`schema.create_all` 只创建缺失的表，不会改动已有表，已有部署升级表结构时仍应单独引入版本化迁移。

## 块摘要反查索引

`v2` 新增三张表用于块摘要反查，均为新增表，不改动既有表：

- `evidence_block_refs`：每份报告中每处块引用一行（`address`、`snapshot`、`asset`、`chunk_index`、`ordinal`），`(evidence_id, snapshot, chunk_index)` 唯一。
- `evidence_index_state`：每份已收录报告的标记，补录据此续跑。
- `evidence_report_lease`：单行出具锁，新证据写入时先更新该行以固定编号与提交顺序。

这三张表由 `schema.create_all` 在启动时自动补建；已有证据由 `python -m scripts.backfill_index` 或 `POST /blocks/index/backfill` 补录，可分批中断续跑。若使用独立的迁移工具，对应 DDL 为建表加索引 `ix_block_refs_address (address, evidence_id, ordinal)`。

索引说明：引用记录数等于各报告块引用数之和，单份报告按业务上限最多 2,048 行。补录按批提交，事务大小与内存只与批大小相关，历史报告很多时用 `--max` 分批限时即可。
