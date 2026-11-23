"""块引用反查索引补录。

历史证据在引入索引前已落库，需补录才能反查。补录按证据自增编号推进并逐批提交：
进程随时中断后重跑会从水位继续，已收录的引用由唯一约束跳过，不会重复。

用法（backend 容器内）::

    python -m scripts.backfill_index            # 补录到当前最新证据
    python -m scripts.backfill_index --batch 50 # 指定每批读取份数
    python -m scripts.backfill_index --max 10   # 本次最多处理 10 份，便于分批限时
"""

import argparse
import json

from src.config.settings import engine
from src.models.tables import schema
from src.services.block_index import backfill_step, status


def main():
    parser = argparse.ArgumentParser(description='补录块引用反查索引')
    parser.add_argument('--batch', type=int, default=100)
    parser.add_argument('--max', dest='max_evidence', type=int, default=None)
    args = parser.parse_args()
    schema.create_all(engine)
    result = backfill_step(batch=args.batch, max_evidence=args.max_evidence)
    print(json.dumps({'step': result, 'status': status()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
