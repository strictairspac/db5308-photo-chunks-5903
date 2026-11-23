# 光坞影像·底片块库验收

以 SHA-256 内容寻址去重，从选中快照复原并列出损坏或缺失块。面向演示数据和本地复现，场景、操作剧本和业务边界见 [BUSINESS.md](BUSINESS.md)。

## 业务模块

- 底片快照
- 内容块目录
- 还原查验
- 留存清单

## 本地运行

```sh
cp -n .env.example .env
docker compose up -d --build --wait
```

- 工作台 `http://127.0.0.1:18297`
- FastAPI 文档 `http://127.0.0.1:19297/docs`
- MySQL 8.0 端口 `127.0.0.1:17797`，账号 `app`，演示库 `workflow`
- `docker compose exec -T backend python -m scripts.seed_demo` 手动重跑演示数据
- `docker compose logs -f backend worker` 查看运行情况
- `docker compose down` 停容器并保留演示卷

### 演示账号

统一密码 `Demo-Walkthrough-2026`。

| 账号 | 角色 | 能执行的操作 |
|---|---|---|
| `operator` | 经办员 | 读取档案、创建实验输入、提交执行、导出结果 |
| `reviewer` | 复核员 | 读取、导出和签署结果 |
| `viewer` | 观察员 | 读取、导出已有结果 |

`SEED_DEMO=true` 启动时生成账号与一个业务样例。工作台新建档案会预填样例。可编辑表单中的数值、文本和结构化 JSON 后提交。业务说明中的原始文件是上游示例，演示数据通过表单录入。角色枚举位于后端 `src/constants/workflow.py` 与前端 `src/constants/workflow.js`。执行状态枚举也位于这两个文件，使用于队列、列表和执行详情。证据校验码是报告 JSON 按 UTF-8、键排序且省略多余空格序列化后的 SHA-256。

## 块摘要反查

复核发现坏块时，可按块摘要反查它出现在哪些验收报告、哪份底片版本和第几块。同一块在同一报告内出现多次时逐处返回。

```sh
curl -s 'http://127.0.0.1:19297/blocks/occurrences?address=<64位SHA-256>&limit=50' \
  -H "Authorization: Bearer $TOKEN"
```

- `results[]` 每项含 `report_id`（报告编号，即证据编号）、`run_id`、`snapshot_id`（底片版本）、`asset`（底片文件）、`chunk_index`（从 1 开始的块序号）与 `ordinal`（报告内引用序号）。
- `total`、`report_count` 为本次翻页范围内的总处数与报告数；翻页时把 `next_cursor` 原样回传，`has_more` 为 `false` 即到末尾。
- 首批结果打开时固定当时已出具报告的边界（`boundary_evidence_id`），后续页只在该边界内翻动，不会漏项，也不会混入后来出具的报告。游标与块摘要、边界绑定并做签名校验。
- 翻页只读块引用索引表，不逐份扫描报告正文。

新报告在证据落库的同一事务内写入索引，出具后即可反查；历史证据由可中断的补录收录：

- 启动时后台自动补录一次；也可手动执行 `docker compose exec -T backend python -m scripts.backfill_index`，或用 `--max 200` 分批限时。
- 补录按证据逐份推进，每份收录后提交并记录标记；中断后重跑只处理未收录的报告，重复收录由唯一约束跳过，不会多出记录。
- `GET /blocks/index` 查看补录进度（`pending_evidence`、`done`），`POST /blocks/index/backfill`（经办员）触发一批补录。

## 工程结构

| 服务 | 实现 |
|---|---|
| frontend | Nginx 与原生 HTML、CSS、JavaScript |
| backend | Python 3.11、FastAPI、SQLAlchemy |
| worker | Python 持久队列执行与 Redis 唤醒信号 |
| db | MySQL 8.0，持久卷保存档案、执行、证据及审计 |
| redis | Redis 7，追加日志卷保存队列唤醒信号 |

编辑 `backend/src/services/domain.py` 可修改试算方法与业务表单说明。API 路由、数据定义和页面位于 `backend/src/routes/`、`backend/src/models/` 与 `frontend/src/pages/`。开发时更新源文件后重新构建对应服务。

`.env.example` 提供 `FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT`、`DB_PASSWORD`、`AUTH_KEY` 、`SEED_DEMO` 与 `NETWORK_SUBNET`。修改 `NETWORK_SUBNET` 可避开本机现有容器网段。所有发布的端口绑定本机回环地址。接入独立部署前需配置自己的凭据并按业务范围做独立验收。

[MIT License](LICENSE)
