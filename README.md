# 发版闸（Shipgate）

骨架：发版申请入闸 → 环境晋升裁决 → 放行/回滚窗 → 发布结果对账

- 前端 http://127.0.0.1:6800
- API http://127.0.0.1:11800
- 数据库环境变量 `SHIPGATE_DB`（容器内默认 `/data/shipgate.db`）

```bash
docker compose up -d --build
```

本地开发：

```bash
# 后端（在 backend/ 下）
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 11800

# 前端
npm --prefix frontend install
npm --prefix frontend run dev      # http://127.0.0.1:6800 ，/api 代理到 11800
npm --prefix frontend run build    # 生产构建验证
```

## 入闸架构臂（全局唯一口径）

本仓库在两种可行架构中敲定并贯彻 **臂 B：红单可告警入闸**。不存在双模开关，
设置项 `入闸架构臂 = 红单可告警入闸` 仅作事实登记。

同一条判定贯穿六个体面，不允许各说各话：

1. **流水线回执**（`pipeline_status`：未回执 / 绿 / 红）
2. **入闸写口与回执**（`POST /api/发版单/{id}/入闸`）
3. **发版单状态与冻结裁决**（`status` + `gate_verdict` + `gate_pipeline_status`）
4. **制品挂接行**（`artifact_links`，结论/告警与裁决逐字段一致）
5. **总览在途与对账口径**（红/绿入闸分开计数）
6. **审计**（成功入闸、入闸驳回、异常整笔收回、入闸后翻红均留痕）

### 判定规则

- 待入闸单必须先收到流水线绿/红回执；未回执驳回。
- 应用停用、制品摘要为空，一律硬拒（**空制品摘要是独立规则**，与颜色无关）。
- 同应用已有在途单（已入闸/晋升中/已放行/回滚中）时，不得再入闸。
- 绿回执 → `绿入闸`；红回执 → 允许入闸，但形成永久 `红入闸` 裁决与告警标，
  挂接行按红如实登记。
- **红入闸单晋升硬阻断**：任何目标环境都返回 400，对账也不计入「按绿通过口径」。
- **入闸后翻红另立**：入闸结论与「入闸时流水线」被冻结，事后流水线翻红只加
  `入闸后翻红` 标记并冻结晋升，绝不改写、洗白原裁决（红转绿同样不能洗白）。
- 读侧判定是纯函数、可重放：`GET /api/发版单/{id}/入闸判定`
  （后端 `app/services/gate.py::evaluate_gate`），入闸按钮的可用性、拟定裁决、
  驳回原因全部来自它。

### 并发与事务

- 入闸事务首句 `BEGIN IMMEDIATE`，锁内重读并重放 `evaluate_gate()`，
  再用条件 `UPDATE ... WHERE status='待入闸'` + `rowcount` 兜底双击/竞态。
- 发版单裁决、旧挂接行收回、新挂接行插入、成功审计在**同一事务**提交；
  中途任何异常整笔回滚，不得留下「红灯已入闸却无成功挂接行」或半截脏挂接。
- 驳回/异常另开独立事务写 `入闸驳回` / `入闸异常整笔收回` 审计，
  保证闸内事务回滚后仍可追责。
- 历史脏状态（`已入闸` 但只有 `挂接中` 行 / 无成功挂接行）可整笔收回：
  `POST /api/发版单/{id}/收回挂接`，body `{}` 收回全部脏行并在必要时把发版单
  重置回 `待入闸`；也可 `{"挂接编号": 123}` 只收指定行。

### 入闸证据同命运账本

成功单号不是独立的成功提示。一笔可重放的入闸成功必须同时摸到：

1. `cross_line_stamps`：过线戳及其颜色/状态/成立/致命/半截字段；
2. `admit_events`：独立入闸事件册，事件必须引用入闸当下那枚过线戳；
3. `admit_water_entries` + `admit_water_level`：本单水位令牌与流水线入闸总水位；
4. `artifact_links`：同裁决挂接行；
5. `audit_events`：`action='入闸'` 的成功审计，备注记录戳/事件/令牌编号。

`POST /api/发版单/{id}/入闸` 在同一个 SQLite 事务中执行
`BEGIN IMMEDIATE` → 读戳 → 读判定 → 条件翻转 release → 写/收回挂接行 →
写过线戳 → 写入闸事件 → 写本单水位令牌 → 重算账面总水位 → 写成功审计。
`after_stamp`、`after_event`、`after_water`、`after_link` 任一故障都会使整个
事务回滚；驳回/故障审计由独立安全事务保留。

过线戳刷新口 `POST /api/发版单/{id}/过线戳刷新` 同样使用
`BEGIN IMMEDIATE`，并在同事务更新 release 与新戳。入闸引用的是入闸当下戳，
旁读口永远返回最新戳；普通入闸后的流水线翻色仍保留旧语义和审计。只有致命红戳、
未成立戳、刷新半截、无审计解释的冲突、事件缺失/错链、水位假加/孤儿令牌、
成功审计或挂接行缺失才会被钉成证据缺口。

普通红色流水线配合 `红入闸` 裁决仍是架构臂 B 允许的合法自洽证据：该戳
`red=1` 但 `fatal=0`。致命红戳（`红戳`/`未成立`/`失败`/`已回滚`/`半截` 等）
才会阻止新入闸或在历史脏数据上钉缺口。

#### 水位口径与对账差额

- **真实水位**：有效 `admit_events` join 有效 `admit_water_entries` 的计数。
- **账面水位**：`admit_water_level.level`，可被人工破坏成假水位。
- **差额**：账面水位 − 真实水位；非 0 或存在孤儿令牌时 `可信=false`、亮红灯。
- **差额总数**：按可定位的缺口/差额记录数计数。水位数值差已经对应一条
  `流水线入闸水位差额` gap，因此不再把 `abs(差额)` 与 gap 条数重复相加；
  数值另由 `水位数值差额` 暴露。

`GET /api/事件重放/{id}`（别名 `/api/replay/{id}`、`/api/evidence/{id}`）
返回 `可重放成功/replay_ok`、`假成功`、`成功单号`、`可疑成功单号`、
`被揭穿假入闸成功单号`、戳、事件册、水位令牌、审计和缺口。缺口未清时，
release enrichment 给出 `同命运=false`、`可重放成功单号=null`、
`假成功单号=单号`，且 `在途入闸格.水位亮灯=false`。

显式纠偏：`POST /api/对账/纠偏` 或 `POST /api/发版单/{id}/纠偏`。
纠偏先运行 `backfill_evidence()` 诚实补录可由 release/审计重建的事件、戳和水位；
仍有致命缺口的单再 `reclaim_dirty_links(..., force=true)` 整笔退回，最后删除孤儿
令牌、按有效令牌重算水位并同步 `admit_gap_pins`。

### 新增/增强接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/api/发版单`、`/api/releases` | 列表/创建；创建兼容 `appId/app_id`、`version/targetVersion`、`digest/artifactDigest` |
| GET | `/api/发版单/{id}`、`/api/releases/{id}`、`/api/release/{id}` | enriched 详情，含戳旁读、事件册、水位、缺口、重放、digest/环境旁读 |
| GET | `/api/发版单/{id}/入闸判定` | 读侧可重放判定 |
| POST | `/api/发版单/{id}/入闸` | 臂 B 同命运写口；别名 `/api/releases/{id}/admit`、`/api/admit/{id}` |
| POST | `/api/发版单/{id}/流水线` | 流水线回执；别名 `/api/releases/{id}/pipeline`、`/api/pipeline/{id}` |
| POST | `/api/发版单/{id}/过线戳刷新` | 戳刷新写口；另有集合口 `POST /api/过线戳刷新`、`/api/stamps/{id}/refresh` |
| GET | `/api/过线戳旁读?release_id={id}` | 当前戳；别名 `/api/stamp-side-read/{id}`、`/api/cross-line-stamp?release_id={id}`、`/api/发版单/{id}/stamp-side-read`；英文字段含 bool `established/fatal/red/half` |
| GET | `/api/入闸事件册` | 独立事件册；别名 `/api/events`、`/api/admit-event-book`、`/api/admit-event`；事件含 `valid/active` |
| GET | `/api/入闸事件?release_id={id}` | 事件列表；路径别名 `/api/releases/{id}/events`；详情内也有 `入闸事件册` |
| GET | `/api/流水线入闸水位` | 真实/账面/差额/孤儿令牌；别名 `/api/水位`、`/api/water-level`、`/api/water`、`/api/releases/{id}/water-level`；英文字段含 `true_level/stored_level/trusted` |
| GET | `/api/缺口` | 动态缺口 + 持久 pins；别名 `/api/gap-pins`、`/api/reconcile/gaps`、`/api/releases/{id}/gaps`。带 `release_id` 时 `pins` 严格限本单，全局 NULL 水位 pin 另在 `globalPins` |
| GET | `/api/对账差额` | 分类差额与明细；别名 `/api/reconcile/difference` |
| GET | `/api/事件重放/{id}` | 戳∪事件∪水位∪审计重放；别名 `/api/replay/{id}`、`/api/evidence/{id}`；含 `release_id/id`、`replay_ok`、`fake_success/fakeSuccess`、`success_no` |
| GET | `/api/发版单/{id}/digest旁读` | digest 旁读；别名 `/api/digest-side-read/{id}`、`/api/releases/{id}/digest` |
| GET | `/api/发版单/{id}/环境旁读` | 环境 code/name 旁读；别名 `/api/environment-side-read/{id}` |
| GET/POST | `/api/发版单/{id}/挂接行`、`/api/挂接行` | 挂接行；英文别名 `/api/releases/{id}/links`、`/api/artifact-links` |
| POST | `/api/发版单/{id}/收回挂接` | 收回脏行 / force 整笔重置；别名 `/api/releases/{id}/reclaim` |
| POST | `/api/对账/纠偏` | 补账、重算水位并整笔退回仍致命撕裂的单；别名 `/api/reconcile/correct` |
| GET | `/api/总览` | 水位、缺口总数、`在途入闸格`；别名 `/api/overview`、`/api/dashboard` |
| GET | `/api/对账` | 红绿口径、挂接行、缺口、水位、差额、重放；别名 `/api/reconcile`、`/api/reconciliation` |
| GET | `/api/审计` | 审计查询，兼容 `kind/action/ref_id/target_id/release_id/q/keyword`；别名 `/api/audit`、`/api/audit-events`、`/api/audit/filter` |

## 前端证据面

主导航新增 **事件册**（路由 `/事件册` 与 `/events`）。发版单页可同屏对读：

- `cross-line-stamp-side-read` / `stamp-side-read` / `stamp-refresh-button`
- `event-side-read` / `admit-event` / `admit-event-missing`
- `admit-receipt` / `admit-success-no`
- `half-refresh-fixture-button` / `half-refresh-button`
- `row-water-level` / `water-level` / `water-level-light` / `pipeline-admit-water-level` / `global-water-level`
- `gap-list` / `row-gap-list` / `detail-gap-list` / `gap-pin` / `row-gap-badge`
- `release-row` / `overview-transit-row`（行带 `data-release-id`，可先按发版单定位行，再在行内使用重复 testid）
- `co-destiny` / `overview-co-destiny`（`data-ok` 标明证据是否同命运）
- `create-release-button` / `release-app-select` / `release-version-input` / `release-digest-input`
- `pipeline-green-button` / `pipeline-red-button` / `release-reclaim-button`
- `release-serial` / `open-detail-side-read` 打开 `release-detail-side-read`
- `detail-stamp-side-read`、`detail-event-book`、`detail-event-table`
- `replay-panel`、`fake-success-no`、`detail-gap-list`
- `digest-side-read`、`environment-side-read`、`env-side-read`
- `artifact-link-row`、`release-audit-drawer`

总览提供 `overview-gap-banner`、`overview-water-difference`、
`overview-water-level`、`overview-gap-count`、`transit-admit-cell`、
`overview-stamp-side-read`、`overview-water-light`、`transit-gap-count`。
对账页提供 `reconcile-difference`、`difference-total`、`difference-water`、
`reconcile-gap-list`、`reconcile-gap-pin`、`reconcile-correct-button`。
事件册页提供 `event-book-table`、`event-book-row`、`event-state`、
`event-stamp-link`、缺口 pins 和 release 过滤 testid。

## 测试夹具（46 个用例）

依赖：`pip install -r backend/requirements-dev.txt`

```bash
# 仓库根（根 pytest.ini 已配置 pythonpath=backend）
/tmp/shipgate-venv/bin/python -m pytest -q

# 或 backend/ 目录内
cd backend && /tmp/shipgate-venv/bin/python -m pytest -q

# 前端构建
npm --prefix frontend ci
npm --prefix frontend run build
```

夹具覆盖：

- `test_evidence_fixtures.py`：四路事故分测——
  1. 致命红戳/人工拧红陪绿单号，钉缺口、重放假成功、总览格和水位灯不亮、对账差额与 pins；
  2. 入闸事件册被抹，重放揭穿且水位错链；
  3. `admit_water_level` 账面假加与孤儿水位令牌，暴露真实/账面差额；
  4. 入闸 `after_stamp/after_event/after_water/after_link` 与刷新
     `after_stamp/half_refresh` 半截回滚，以及半截刷新和入闸栅栏并发。
  同文件另测普通 `红入闸` 是合法证据，不被误判为撕裂。
- `test_evidence_surfaces.py`：干净入闸后逐一摸 API 别名、戳旁读、事件册、
  水位、重放、digest/环境旁读、总览在途格、对账差额、审计过滤、英文写口别名、
  camelCase 字段/查询别名、release 级 pin 收窄与全局 pin 分离、脱离在途态的残留证据、
  HTTP 故障别名回滚，以及纠偏修复假水位/缺失事件和 force 退回致命戳。
- `test_green_admit.py`：绿入闸全套对齐（发版单/回执/挂接/总览/晋升/审计）。
- `test_red_admit.py`：红入闸核心夹具，一个用例内核对徽章、回执、红挂接行、
  总览红格、三个目标环境晋升全 400、对账红绿分口径、审计关键字；
  另含红后转绿不洗白、红绿两套数据分计数。
- `test_post_admit_red.py`：入闸后再打红与入闸当下判定分离、独立审计、
  晋升冻结、翻回绿后恢复；红入闸永久不可晋升。
- `test_readside_rules.py`：可重放判定、未回执、空摘要、停用应用、
  并行在途、重复入闸、非法颜色、设置臂、审计筛选。
- `test_dirty_link_rollback.py`：故障注入证明闸内事务整笔回滚且有异常审计、
  HTTP 500 后无裁决无挂接、历史脏行整笔重置、好挂接旁的半截行只收脏行、
  指定行收回与重复收回 404。
- `test_concurrency.py`：多轮双线程翻色/入闸竞态、多线程双击唯一成功、
  `BEGIN IMMEDIATE` 锁语义确定性证明、真实 uvicorn + httpx 的 HTTP 并发夹具。

前端定位面还包括发版单流水线区（`pipeline-badge`）、入闸按钮（`admit-button`，
禁用原因即读侧判定）、总览红入闸横幅/统计格/在途表、晋升按钮与阻断原因、
审计筛选表（`audit-filter-kind/ref/keyword/submit/clear`、`audit-table`、
`audit-note`），均带 `data-testid`。

