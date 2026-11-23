"""块引用反查索引。

验收报告正文只列出异常位置与汇总，无法单靠报告体回答「某个块摘要出现在哪些
报告、哪份底片版本、第几块」。本模块把每份报告对应的块引用落到独立索引表：

* 每处引用一行（报告、执行、块摘要、底片版本、块序号、报告内序号），
  同一块在同一报告内出现多次即多行；
* 新报告落库时与证据在同一事务内写入索引，无需等待历史补录即可查；
* 历史证据由可中断的补录逐份推进：每份收录后写收录标记并在同一事务提交，
  重跑只会重做未标记的报告，引用按唯一约束幂等去重。

反查与翻页只读索引表，不读取 ``business_evidence.report`` 正文。

翻页边界
--------
新证据在写入事务内先锁住出具锁行（``evidence_report_lease``）再插入，锁持有到
提交，因此证据自增编号的顺序就是提交顺序：不会出现「编号较小、提交较晚」的报告。

首批结果（不带游标）先固定当时的已提交最大证据编号为上界，再把上界内尚未收录的
报告补齐；此后各页只在上界内翻动，所以既不会漏掉打开首批时已出具的报告，也不会
混入后来出具的报告。游标带上上界并做 HMAC 签名，客户端无法把边界改大。
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
from threading import Lock

from fastapi import HTTPException
from sqlalchemy import and_, func, insert, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.config.settings import AUTH_KEY, engine
from src.models.tables import (block_refs, cases, evidence, indexed_evidence,
                               report_lease, runs)
from src.services import domain
from src.utils.encoding import canonical
from src.utils.time import utc_time

MAX_PAGE = 200
DEFAULT_PAGE = 50
BATCH = 100
CURSOR_SIG_LEN = 32
_CURSOR_VERSION = 1
_PROCESS_BACKFILL = Lock()


def _insert_ignore(conn, table, rows):
    """按方言做「冲突即跳过」的插入，便于补录中断后原样重试。

    MySQL 没有 ``ON CONFLICT DO NOTHING``，用 ``ON DUPLICATE KEY UPDATE <主键>=<主键>``；
    不用 ``INSERT IGNORE``，以免顺带吞掉其他数据错误。
    """
    if not rows:
        return
    dialect = conn.dialect.name
    if dialect == 'mysql':
        key = table.primary_key.columns.values()[0]
        statement = mysql_insert(table).on_duplicate_key_update({key.name: key})
    elif dialect == 'postgresql':
        statement = postgres_insert(table).on_conflict_do_nothing()
    elif dialect == 'sqlite':
        statement = sqlite_insert(table).on_conflict_do_nothing()
    else:
        statement = insert(table)
    conn.execute(statement, rows)


def touch_lease(conn):
    """在证据写入事务内先锁住出具锁行，使证据编号顺序与提交顺序一致。

    先按固定主键做幂等插入，再 ``UPDATE`` 该行取得排他锁；锁持有到事务提交，因此
    后一个写入者必须等前一个提交后才能插入证据、拿到更大的自增编号。
    """
    _insert_ignore(conn, report_lease, [{'id': 1, 'touched_at': utc_time()}])
    conn.execute(report_lease.update().where(report_lease.c.id == 1)
                 .values(touched_at=utc_time()))


def index_evidence(conn, evidence_id, run_id, payload):
    """收录单份报告的块引用并写收录标记；重复收录同一处引用会被忽略。"""
    rows = [dict(row, evidence_id=evidence_id, run_id=run_id)
            for row in domain.block_references(payload)]
    _insert_ignore(conn, block_refs, rows)
    _insert_ignore(conn, indexed_evidence, [{'evidence_id': evidence_id, 'run_id': run_id,
                                             'indexed_at': utc_time()}])
    return len(rows)


def _pending(conn, boundary, limit):
    query = select(evidence.c.id, evidence.c.run_id).where(
        ~select(indexed_evidence.c.evidence_id)
        .where(indexed_evidence.c.evidence_id == evidence.c.id).exists())
    if boundary is not None:
        query = query.where(evidence.c.id <= boundary)
    return conn.execute(query.order_by(evidence.c.id).limit(limit)).all()


def _payload(conn, run_id):
    return conn.execute(select(cases.c.payload)
                        .select_from(runs.join(cases, runs.c.case_id == cases.c.id))
                        .where(runs.c.id == run_id)).scalar_one_or_none()


def _index_one(conn, evidence_id, run_id):
    """收录一份报告；无法复原或意外出错时也写收录标记，避免补录反复卡在同一份上。"""
    payload = _payload(conn, run_id)
    try:
        if payload is not None:
            index_evidence(conn, evidence_id, run_id, payload)
    except (ValueError, KeyError, TypeError):
        logging.exception('Block reference index skipped evidence %s', evidence_id)
    _insert_ignore(conn, indexed_evidence, [{'evidence_id': evidence_id, 'run_id': run_id,
                                             'indexed_at': utc_time()}])


def _index_batches(boundary, batch, max_evidence):
    """分批收录编号不超过 ``boundary`` 的未标记报告；每批独立提交，可随时中断。"""
    processed = 0
    while max_evidence is None or processed < max_evidence:
        size = batch if max_evidence is None else min(batch, max_evidence - processed)
        with engine.begin() as conn:
            rows = _pending(conn, boundary, size)
            if not rows:
                break
            for evidence_id, run_id in rows:
                _index_one(conn, evidence_id, run_id)
        processed += len(rows)
    return processed


def latest_evidence(conn):
    return conn.execute(select(func.max(evidence.c.id))).scalar_one_or_none() or 0


def ensure_indexed(boundary, batch=BATCH):
    """补齐本页边界内尚未收录的报告，保证打开首批后翻到末尾不漏项。"""
    with _PROCESS_BACKFILL:
        return _index_batches(boundary, batch, None)


def backfill_step(batch=BATCH, max_evidence=None):
    """补录一批历史证据；``max_evidence`` 限制本次份数，便于分批与限时。

    同一进程内的启动补录、手动补录与反查首屏共用一把进程锁，避免重复做同一批
    工作；跨进程并发由收录标记与唯一约束兜底。
    """
    with _PROCESS_BACKFILL:
        with engine.connect() as conn:
            boundary = latest_evidence(conn)
        processed = _index_batches(boundary, batch, max_evidence)
    report = status()
    report['processed'] = processed
    report['boundary_evidence_id'] = boundary
    return report


def status():
    with engine.connect() as conn:
        pending = conn.execute(select(func.count()).select_from(evidence).where(
            ~select(indexed_evidence.c.evidence_id)
            .where(indexed_evidence.c.evidence_id == evidence.c.id).exists())).scalar_one()
        indexed = conn.execute(select(func.count()).select_from(indexed_evidence)).scalar_one()
        occurrences = conn.execute(select(func.count()).select_from(block_refs)).scalar_one()
        reports = conn.execute(select(func.count(func.distinct(block_refs.c.evidence_id)))
                               .select_from(block_refs)).scalar_one()
        return {'latest_evidence': latest_evidence(conn), 'pending_evidence': pending,
                'done': pending == 0, 'indexed_reports': indexed,
                'reports_with_refs': reports, 'indexed_occurrences': occurrences}


def _sign(body):
    return hmac.new(AUTH_KEY.encode('utf-8'), body.encode('ascii'),
                    hashlib.sha256).hexdigest()[:CURSOR_SIG_LEN]


def _encode_cursor(address, boundary, evidence_id, ordinal):
    body = base64.urlsafe_b64encode(canonical({
        'v': _CURSOR_VERSION, 'address': address, 'boundary': boundary,
        'evidence_id': evidence_id, 'ordinal': ordinal,
    })).decode('ascii').rstrip('=')
    return f'{body}.{_sign(body)}'


def _decode_cursor(token, address):
    try:
        body, signature = token.rsplit('.', 1)
        if not hmac.compare_digest(_sign(body), signature):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode((body + '=' * (-len(body) % 4)).encode('ascii')))
        if data['v'] != _CURSOR_VERSION or data['address'] != address:
            raise ValueError
        return int(data['boundary']), int(data['evidence_id']), int(data['ordinal'])
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise HTTPException(422, '翻页游标无效或与块摘要不匹配') from None


def normalize_address(raw):
    value = (raw or '').strip().lower()
    if value.startswith('sha256:'):
        value = value[7:]
    if len(value) != 64 or any(ch not in '0123456789abcdef' for ch in value):
        raise HTTPException(422, '块摘要须为 64 位十六进制 SHA-256')
    return value


def _page_size(limit):
    try:
        return max(1, min(int(limit), MAX_PAGE))
    except (TypeError, ValueError):
        raise HTTPException(422, '每页条数须为整数') from None


def _occurrence(row):
    return {'report_id': row.evidence_id, 'evidence_id': row.evidence_id, 'run_id': row.run_id,
            'snapshot_id': row.snapshot, 'snapshot': row.snapshot, 'asset': row.asset,
            'chunk_index': row.chunk_index, 'ordinal': row.ordinal}


def lookup(raw_address, limit=DEFAULT_PAGE, cursor=None):
    """按块摘要分页反查逐处引用。

    首批结果（不带游标）先固定当时的已提交最大证据编号为上界，再补齐上界内尚未
    收录的报告；后续各页沿用游标里的上界，只读索引表。
    """
    address = normalize_address(raw_address)
    size = _page_size(limit)
    if cursor:
        # 后续页只读索引表：首批已把上界内的报告全部收录，边界由签名游标固定。
        boundary, last_id, last_ordinal = _decode_cursor(cursor, address)
    else:
        with engine.connect() as conn:
            boundary = latest_evidence(conn)
        # 上界确定后再补录，避免把上界之后新出具的报告卷进来。
        ensure_indexed(boundary)
        last_id = last_ordinal = None

    scoped = [block_refs.c.address == address, block_refs.c.evidence_id <= boundary]
    conditions = list(scoped)
    if last_id is not None:
        conditions.append(or_(
            block_refs.c.evidence_id > last_id,
            and_(block_refs.c.evidence_id == last_id, block_refs.c.ordinal > last_ordinal)))
    with engine.connect() as conn:
        rows = conn.execute(select(block_refs.c.evidence_id, block_refs.c.run_id, block_refs.c.snapshot,
                                   block_refs.c.asset, block_refs.c.chunk_index, block_refs.c.ordinal)
                            .where(*conditions)
                            .order_by(block_refs.c.evidence_id, block_refs.c.ordinal)
                            .limit(size + 1)).all()
        total = conn.execute(select(func.count()).select_from(block_refs)
                             .where(*scoped)).scalar_one()
        reports = conn.execute(select(func.count(func.distinct(block_refs.c.evidence_id)))
                               .select_from(block_refs).where(*scoped)).scalar_one()

    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = _encode_cursor(address, boundary, rows[-1].evidence_id, rows[-1].ordinal) if has_more else None
    return {'address': address, 'boundary_evidence_id': boundary, 'total': total,
            'report_count': reports, 'count': len(rows), 'has_more': has_more,
            'next_cursor': next_cursor, 'results': [_occurrence(row) for row in rows]}
