from sqlalchemy import (JSON, Column, ForeignKey, Index, Integer, MetaData,
                        String, Table, Text, UniqueConstraint)

schema = MetaData()
users = Table('accounts', schema,
    Column('id', Integer, primary_key=True),
    Column('username', String(40), nullable=False, unique=True),
    Column('password_hash', String(200), nullable=False),
    Column('role', String(20), nullable=False))
cases = Table('business_cases', schema,
    Column('id', Integer, primary_key=True),
    Column('slug', String(64), nullable=False, unique=True),
    Column('title', String(120), nullable=False),
    Column('payload', JSON, nullable=False),
    Column('author', String(40), nullable=False),
    Column('created_at', String(32), nullable=False))
runs = Table('business_runs', schema,
    Column('id', Integer, primary_key=True),
    Column('case_id', ForeignKey('business_cases.id'), nullable=False, index=True),
    Column('status', String(20), nullable=False, index=True),
    Column('owner', String(40), nullable=False),
    Column('attempt_token', String(36)),
    Column('started_at', String(32)),
    Column('finished_at', String(32)),
    Column('created_at', String(32), nullable=False),
    Column('error', Text))
evidence = Table('business_evidence', schema,
    Column('id', Integer, primary_key=True),
    Column('run_id', ForeignKey('business_runs.id'), nullable=False, unique=True),
    Column('report', JSON, nullable=False),
    Column('digest', String(64), nullable=False),
    Column('reviewer', String(40)),
    Column('review_note', String(500)),
    Column('reviewed_at', String(32)))
audit = Table('audit_events', schema,
    Column('id', Integer, primary_key=True),
    Column('actor', String(40), nullable=False),
    Column('action', String(32), nullable=False),
    Column('reference', String(50), nullable=False),
    Column('created_at', String(32), nullable=False))
# 块引用反查索引：每行对应某份报告中某处块引用。
# address 为块 SHA-256；snapshot/asset 为底片版本（快照编号与底片文件名）；chunk_index
# 为从 1 开始的块序号；ordinal 为报告内引用顺序，用于同一报告内稳定翻页。
# 同一块在同一报告中出现多次即多行；(evidence_id, snapshot, chunk_index) 唯一，
# 补录中断后重跑不会重复收录。
block_refs = Table('evidence_block_refs', schema,
    Column('id', Integer, primary_key=True),
    Column('evidence_id', ForeignKey('business_evidence.id', ondelete='CASCADE'), nullable=False),
    Column('run_id', Integer, nullable=False),
    Column('address', String(64), nullable=False),
    Column('snapshot', String(64), nullable=False),
    Column('asset', String(120), nullable=False),
    Column('chunk_index', Integer, nullable=False),
    Column('ordinal', Integer, nullable=False),
    UniqueConstraint('evidence_id', 'snapshot', 'chunk_index', name='uq_block_ref_occurrence'),
    Index('ix_block_refs_address', 'address', 'evidence_id', 'ordinal'))
# 收录标记：每份已入索引的证据一行。补录按「缺标记」逐份推进，因此中断后只重做未完成
# 的报告；用标记而非最大编号判断完成，可容忍报告提交乱序，也能标记零块引用的报告。
indexed_evidence = Table('evidence_index_state', schema,
    Column('evidence_id', ForeignKey('business_evidence.id', ondelete='CASCADE'), primary_key=True),
    Column('run_id', Integer, nullable=False),
    Column('indexed_at', String(32), nullable=False))
# 报告出具锁（单行）。新证据在写入事务内先取得该行锁再插入，锁持有到提交，因此证据
# 自增编号的顺序与提交顺序一致。反查以「首批翻页时已提交的最大证据编号」为上界：上界
# 内的报告必定已出具，尚未提交的报告编号在上界之外，不会混入后续页。
report_lease = Table('evidence_report_lease', schema,
    Column('id', Integer, primary_key=True),
    Column('touched_at', String(32), nullable=False))
