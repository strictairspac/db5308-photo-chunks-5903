from contextlib import asynccontextmanager
import logging
import os
from threading import Thread

from fastapi import FastAPI
import uvicorn

from scripts.seed_demo import seed
from src.config.settings import engine
from src.middlewares.request import limit_body, request_headers
from src.models.tables import schema
from src.routes.workflow import router
from src.services import block_index


def _backfill():
    try:
        block_index.backfill_step()
    except Exception:
        logging.exception('Block reference index backfill failed')


@asynccontextmanager
async def lifespan(app):
    schema.create_all(engine)
    if os.getenv('SEED_DEMO', 'true').lower() == 'true':
        try:
            seed()
        except Exception:
            logging.exception('Demo seeding failed')
    # 历史补录放到后台，不阻塞启动；反查首批也会补齐边界内未收录的报告。
    Thread(target=_backfill, name='block-index-backfill', daemon=True).start()
    yield


app = FastAPI(title='Database business workflow', lifespan=lifespan)
app.middleware('http')(limit_body)
app.middleware('http')(request_headers)
app.include_router(router)

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)
