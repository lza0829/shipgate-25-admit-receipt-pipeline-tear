"""过线戳 / 入闸事件册 / 流水线水位 / 缺口重放 HTTP 面。

路径同时给中文主口径和常见英文别名；所有读模型都来自 SQLite 持久事实，
不调用 pipe25 surface 粉刷模块。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.db import db
from app.services.common import audit
from app.services import admit_evidence as ev
from app.services.gate import reclaim_dirty_links, record_pipeline

router = APIRouter(tags=["入闸证据"])


def _rid(中文编号: int | None, release_id: int | None, rid: int | None) -> int | None:
    return release_id if release_id is not None else (rid if rid is not None else 中文编号)


def _stamp_list(release_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
    with db() as conn:
        sql = "SELECT * FROM cross_line_stamps"
        params: list[Any] = []
        if release_id is not None:
            sql += " WHERE release_id=?"
            params.append(release_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        return [ev.row_to_stamp_side(r) for r in conn.execute(sql, params).fetchall()]


# ---------------------------------------------------------------------------
# 过线戳旁读
# ---------------------------------------------------------------------------

@router.get("/过线戳")
def list_stamps(
    发版单编号: int | None = None,
    release_id: int | None = None,
    rid: int | None = None,
    限制: int = 500,
    limit: int = 500,
):
    target = _rid(发版单编号, release_id, rid)
    return _stamp_list(target, limit or 限制)


@router.get("/cross-line-stamps")
def list_stamps_en(release_id: int | None = None, limit: int = 500):
    return _stamp_list(release_id, limit)


@router.get("/stamps")
def list_stamps_alias(release_id: int | None = None, limit: int = 500):
    return _stamp_list(release_id, limit)


@router.get("/过线戳旁读")
def stamp_side_by_query(发版单编号: int | None = None, release_id: int | None = None):
    rid = 发版单编号 if 发版单编号 is not None else release_id
    if rid is None:
        return {"过线戳旁读": _stamp_list(), "items": _stamp_list()}
    with db() as conn:
        side = ev.current_stamp(conn, rid)
        return {"发版单编号": rid, "过线戳旁读": side, "stamp_side_read": side}


@router.get("/stamp-side-read", include_in_schema=False)
@router.get("/cross-line-stamp", include_in_schema=False)
def stamp_side_by_query_en(release_id: int | None = None, rid: int | None = None):
    target = release_id if release_id is not None else rid
    if target is None:
        return {"过线戳旁读": _stamp_list(), "items": _stamp_list()}
    return release_stamp_side(target)


@router.get("/发版单/{release_id}/过线戳旁读")
def release_stamp_side(release_id: int):
    with db() as conn:
        side = ev.current_stamp(conn, release_id)
        return {"发版单编号": release_id, "过线戳旁读": side, "stamp_side_read": side}


@router.get("/发版单/{release_id}/stamp-side-read", include_in_schema=False)
@router.get("/发版单/{release_id}/cross-line-stamp", include_in_schema=False)
@router.get("/releases/{release_id}/stamp-side-read", include_in_schema=False)
@router.get("/releases/{release_id}/cross-line-stamp", include_in_schema=False)
@router.get("/releases/{release_id}/stamp", include_in_schema=False)
@router.get("/cross-line-stamp/{release_id}", include_in_schema=False)
@router.get("/cross-line-stamp-side-read/{release_id}", include_in_schema=False)
def release_stamp_side_en(release_id: int):
    return release_stamp_side(release_id)


@router.get("/stamp-side-read/{release_id}", include_in_schema=False)
@router.get("/stamps/{release_id}", include_in_schema=False)
@router.get("/stamps/{release_id}/side-read", include_in_schema=False)
def stamp_side_short_en(release_id: int):
    return release_stamp_side(release_id)


async def _refresh_body(request: Request) -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


def _pick_status(body: dict[str, Any]) -> str:
    raw = (
        body.get("流水线")
        or body.get("流水线状态")
        or body.get("pipeline_status")
        or body.get("pipelineStatus")
        or body.get("status")
        or body.get("pipeline")
        or body.get("color")
        or body.get("颜色")
        or body.get("过线戳颜色")
        or body.get("过线戳")
        or "未回执"
    )
    text = str(raw).strip()
    aliases = {
        "green": "绿", "passed": "绿", "pass": "绿", "ok": "绿", "成功": "绿",
        "red": "红", "failed": "红", "fail": "红", "失败": "红",
        "pending": "未回执", "none": "未回执", "": "未回执",
    }
    return aliases.get(text.lower(), text)


def _refresh_stamp_payload(release_id: int, body: dict[str, Any]):
    status = _pick_status(body)
    if status not in ("未回执", "绿", "红"):
        raise HTTPException(400, "过线戳/流水线状态非法")
    try:
        return record_pipeline(
            release_id,
            status,
            fault=(
                body.get("故障")
                or body.get("fault")
                or body.get("injectFault")
                or body.get("inject_fault")
                or body.get("faultType")
                or body.get("fault_type")
            ),
            half=bool(body.get("半截") or body.get("half")),
            fatal=body.get("致命", body.get("fatal")),
            stamp_state=(
                body.get("戳状态")
                or body.get("stamp_state")
                or body.get("stampState")
                or body.get("state")
            ),
        )
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@router.post("/发版单/{release_id}/过线戳刷新")
async def refresh_stamp(release_id: int, request: Request):
    body = await _refresh_body(request)
    return _refresh_stamp_payload(release_id, body)


@router.post("/过线戳刷新")
async def refresh_stamp_collection(request: Request):
    body = await _refresh_body(request)
    rid = (
        body.get("发版单编号")
        or body.get("release_id")
        or body.get("releaseId")
        or body.get("target_id")
        or body.get("targetId")
        or body.get("rid")
        or body.get("id")
    )
    if rid is None:
        raise HTTPException(422, "缺少发版单编号")
    return _refresh_stamp_payload(int(rid), body)


@router.post("/过线戳刷新/{release_id}")
async def refresh_stamp_alias(release_id: int, request: Request):
    return await refresh_stamp(release_id, request)


@router.post("/stamps/{release_id}/refresh")
async def refresh_stamp_en(release_id: int, request: Request):
    return await refresh_stamp(release_id, request)


# ---------------------------------------------------------------------------
# 入闸事件册
# ---------------------------------------------------------------------------

@router.get("/入闸事件")
@router.get("/admit-event", include_in_schema=False)
def list_admit_events(
    发版单编号: int | None = None,
    release_id: int | None = None,
    rid: int | None = None,
    限制: int = 500,
    limit: int = 500,
):
    target = _rid(发版单编号, release_id, rid)
    with db() as conn:
        return ev.list_admit_events(conn, target, limit or 限制)


@router.get("/admit-events")
def list_admit_events_en(release_id: int | None = None, limit: int = 500):
    with db() as conn:
        return ev.list_admit_events(conn, release_id, limit)


@router.get("/events")
def list_events_en(release_id: int | None = None, limit: int = 500):
    with db() as conn:
        return ev.list_admit_events(conn, release_id, limit)


@router.get("/入闸事件册")
@router.get("/admit-event-book", include_in_schema=False)
@router.get("/event-book", include_in_schema=False)
def admit_event_book(
    发版单编号: int | None = None,
    release_id: int | None = None,
    限制: int = 500,
):
    rid = 发版单编号 if 发版单编号 is not None else release_id
    with db() as conn:
        items = ev.list_admit_events(conn, rid, 限制)
        return {
            "入闸事件册": items,
            "events": items,
            "items": items,
            "总数": len(items),
            "可重放": True,
        }


@router.get("/发版单/{release_id}/入闸事件")
def release_admit_events(release_id: int):
    with db() as conn:
        items = ev.list_admit_events(conn, release_id)
        return {"发版单编号": release_id, "入闸事件": ev.active_admit_event(conn, release_id), "入闸事件册": items, "events": items}


@router.get("/发版单/{release_id}/events")
@router.get("/发版单/{release_id}/admit-event", include_in_schema=False)
@router.get("/releases/{release_id}/events", include_in_schema=False)
@router.get("/releases/{release_id}/admit-event", include_in_schema=False)
@router.get("/admit-events/{release_id}", include_in_schema=False)
def release_events_en(release_id: int):
    return release_admit_events(release_id)


# ---------------------------------------------------------------------------
# 水位 / 缺口 / 重放
# ---------------------------------------------------------------------------

@router.get("/流水线入闸水位")
@router.get("/admit-water-level", include_in_schema=False)
@router.get("/pipeline-admit-water-level", include_in_schema=False)
def pipeline_water():
    with db() as conn:
        return ev.water_state(conn)


@router.get("/水位")
def water_alias():
    return pipeline_water()


@router.get("/water-level")
@router.get("/water", include_in_schema=False)
def water_en():
    return pipeline_water()


@router.get("/releases/{release_id}/water-level", include_in_schema=False)
@router.get("/releases/{release_id}/water", include_in_schema=False)
def release_water_en(release_id: int):
    # 水位是独立全局账；路径别名同时回传本单令牌，避免把全局水位误读成本单私有值。
    with db() as conn:
        water = ev.water_state(conn)
        token = ev.water_token(conn, release_id)
        return {**water, "release_id": release_id, "本单水位令牌": token, "water_token": token}


@router.get("/缺口")
@router.get("/gaps", include_in_schema=False)
@router.get("/reconcile/gaps", include_in_schema=False)
@router.get("/reconciliation/gaps", include_in_schema=False)
@router.get("/gap-pins", include_in_schema=False)
@router.get("/admit-gap-pins", include_in_schema=False)
def list_gaps(
    发版单编号: int | None = None,
    release_id: int | None = None,
    rid: int | None = None,
    仅未清: bool = True,
    active_only: bool | None = None,
):
    target = _rid(发版单编号, release_id, rid)
    only_uncleared = 仅未清 if active_only is None else active_only
    with db() as conn:
        all_gaps = ev.sync_gap_pins(conn)
        global_gaps = [g for g in all_gaps if g.get("发版单编号") is None]
        if target is not None:
            gaps = [g for g in all_gaps if g.get("发版单编号") == target]
        else:
            gaps = all_gaps

        pin_sql = "SELECT * FROM admit_gap_pins"
        pin_where: list[str] = []
        pin_params: list[Any] = []
        if only_uncleared:
            pin_where.append("cleared=0")
        if target is not None:
            pin_where.append("release_id=?")
            pin_params.append(target)
        if pin_where:
            pin_sql += " WHERE " + " AND ".join(pin_where)
        pin_sql += " ORDER BY id DESC"
        pins = [dict(r) for r in conn.execute(pin_sql, pin_params).fetchall()]

        global_pin_sql = "SELECT * FROM admit_gap_pins WHERE release_id IS NULL"
        global_pin_params: list[Any] = []
        if only_uncleared:
            global_pin_sql += " AND cleared=0"
        global_pin_sql += " ORDER BY id DESC"
        global_pins = [dict(r) for r in conn.execute(global_pin_sql, global_pin_params).fetchall()]
        return {
            "缺口": gaps,
            "gaps": gaps,
            "缺口总数": len(gaps),
            "gap_count": len(gaps),
            "gapCount": len(gaps),
            "钉住": pins,
            "pins": pins,
            "全局缺口": global_gaps,
            "globalGaps": global_gaps,
            "全局钉住": global_pins,
            "globalPins": global_pins,
        }


@router.get("/releases/{release_id}/gaps", include_in_schema=False)
def release_gaps_en(release_id: int):
    return list_gaps(release_id=release_id)


@router.get("/对账差额")
def reconcile_difference():
    with db() as conn:
        gaps = ev.sync_gap_pins(conn)
        water = ev.water_state(conn)
        diff = ev.difference_summary(gaps, water)
        return {"对账差额": diff, "difference": diff, **diff}


@router.get("/reconcile/difference")
@router.get("/difference", include_in_schema=False)
@router.get("/reconciliation/difference", include_in_schema=False)
def difference_en():
    return reconcile_difference()


@router.get("/事件重放")
@router.get("/对账/重放", include_in_schema=False)
@router.get("/reconcile/replay", include_in_schema=False)
def replay_by_query(
    发版单编号: int | None = None,
    release_id: int | None = None,
    rid: int | None = None,
):
    target = _rid(发版单编号, release_id, rid)
    if target is None:
        raise HTTPException(422, "缺少发版单编号")
    return replay(target)


@router.get("/replay")
def replay_by_query_en(release_id: int | None = None, rid: int | None = None):
    target = release_id if release_id is not None else rid
    if target is None:
        raise HTTPException(422, "缺少 release_id")
    return replay(target)


@router.get("/事件重放/{release_id}")
@router.get("/releases/{release_id}/replay", include_in_schema=False)
@router.get("/releases/{release_id}/evidence", include_in_schema=False)
@router.get("/对账/重放/{release_id}", include_in_schema=False)
@router.get("/reconcile/replay/{release_id}", include_in_schema=False)
def replay(release_id: int):
    with db() as conn:
        if conn.execute("SELECT id FROM releases WHERE id=?", (release_id,)).fetchone() is None:
            raise HTTPException(404, "发版单不存在")
        return ev.replay_release(conn, release_id)


@router.get("/发版单/{release_id}/事件重放")
def replay_release_alias(release_id: int):
    return replay(release_id)


@router.get("/重放/{release_id}")
def replay_short(release_id: int):
    return replay(release_id)


@router.get("/replay/{release_id}")
def replay_en(release_id: int):
    return replay(release_id)


@router.get("/发版单/{release_id}/证据")
def release_evidence(release_id: int):
    return replay(release_id)


@router.get("/evidence/{release_id}")
def evidence_en(release_id: int):
    return replay(release_id)


@router.get("/发版单/{release_id}/digest旁读")
@router.get("/digest旁读/{release_id}", include_in_schema=False)
@router.get("/digest-side-read/{release_id}", include_in_schema=False)
@router.get("/发版单/{release_id}/digest-side-read", include_in_schema=False)
@router.get("/releases/{release_id}/digest", include_in_schema=False)
def digest_side(release_id: int):
    with db() as conn:
        r = conn.execute("SELECT artifact_digest FROM releases WHERE id=?", (release_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "发版单不存在")
        digest = r["artifact_digest"]
        return {"制品摘要": digest, "digest": digest, "齐全": bool(digest), "可打开": True}


@router.get("/发版单/{release_id}/环境旁读")
@router.get("/环境旁读/{release_id}", include_in_schema=False)
@router.get("/environment-side-read/{release_id}", include_in_schema=False)
@router.get("/发版单/{release_id}/environment-side-read", include_in_schema=False)
@router.get("/releases/{release_id}/environment", include_in_schema=False)
def env_side(release_id: int):
    with db() as conn:
        row = conn.execute(
            """
            SELECT r.current_env_id AS 环境编号, e.code AS 代号, e.name AS 名称,
                   e.code AS code, e.name AS name
              FROM releases r LEFT JOIN environments e ON e.id=r.current_env_id
             WHERE r.id=?
            """,
            (release_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "发版单不存在")
        return dict(row)


# ---------------------------------------------------------------------------
# 显式纠偏：先诚实补账；致命撕裂按「过线戳红则整笔退回」收回。
# ---------------------------------------------------------------------------

def _correct(release_id: int | None = None, rollback_torn: bool = True) -> dict[str, Any]:
    with db() as conn:
        before = ev.detect_gaps(conn, release_id)
        target_ids = sorted({
            int(g["发版单编号"])
            for g in before
            if g.get("发版单编号") is not None
        })
        if release_id is not None:
            target_ids = [release_id]

    reset: list[int] = []
    if rollback_torn:
        for rid in target_ids:
            # 先补账，能由发版单/审计诚实重建的缺失事件/水位就不必退回；
            # 补账后仍有缺口的单，说明证据命运已撕裂，整笔退回。
            with db() as conn:
                ev.backfill_evidence(conn)
                remaining = ev.detect_gaps(conn, rid)
            if remaining:
                try:
                    reclaim_dirty_links(rid, force=True)
                    reset.append(rid)
                except Exception:
                    # 状态机不允许收回的历史阶段仍保留缺口，由对账继续钉住。
                    pass

    with db() as conn:
        ev.backfill_evidence(conn)
        conn.execute(
            """
            DELETE FROM admit_water_entries
             WHERE event_id IS NULL OR event_id NOT IN (
                SELECT id FROM admit_events WHERE state='有效'
             )
            """
        )
        count = conn.execute("SELECT COUNT(*) AS c FROM admit_water_entries").fetchone()["c"]
        from app.services.common import now_str

        conn.execute(
            """
            INSERT INTO admit_water_level(scope, level, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET level=excluded.level, updated_at=excluded.updated_at
            """,
            (ev.WATER_SCOPE, count, now_str()),
        )
        gaps = ev.sync_gap_pins(conn)
        if release_id is not None:
            gaps = [g for g in gaps if g.get("发版单编号") == release_id]
        audit(
            conn,
            "对账",
            release_id,
            "证据纠偏",
            f"补账并重算水位；整笔退回={reset}；剩余缺口={len(gaps)}",
        )
        water = ev.water_state(conn)
        difference = ev.difference_summary(gaps, water)
        return {
            "纠偏": True,
            "corrected": True,
            "整笔退回": reset,
            "reset": reset,
            "剩余缺口": gaps,
            "gaps": gaps,
            "缺口总数": len(gaps),
            "水位": water,
            "对账差额": difference,
            "difference": difference,
        }


@router.post("/对账/纠偏")
async def correct_all(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    target = (
        body.get("发版单编号")
        or body.get("release_id")
        or body.get("releaseId")
        or body.get("rid")
    )
    rollback_torn = bool(body.get("整笔退回致命缺口", body.get("rollback_torn", True)))
    if target is not None:
        return _correct(int(target), rollback_torn)
    return _correct(None, rollback_torn)


@router.post("/reconcile/correct")
async def correct_all_en(request: Request):
    return await correct_all(request)


@router.post("/发版单/{release_id}/纠偏")
async def correct_one(release_id: int, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return _correct(release_id, bool(body.get("整笔退回致命缺口", body.get("rollback_torn", True))))


@router.post("/correct/{release_id}")
@router.post("/reconcile/correct/{release_id}", include_in_schema=False)
@router.post("/releases/{release_id}/correct", include_in_schema=False)
@router.post("/发版单/{release_id}/correct", include_in_schema=False)
async def correct_one_en(release_id: int, request: Request):
    return await correct_one(release_id, request)
