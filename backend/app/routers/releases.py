from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list, row_to_dict
from app.services.common import (
    RELEASE_SELECT,
    assert_app_enabled,
    audit,
    get_release,
    get_setting,
    next_serial,
)
from app.services.gate import (
    GateRejected,
    admit_release,
    enrich_release,
    enrich_releases,
    evaluate_gate,
    reclaim_dirty_links,
    record_pipeline,
)
from app.services import admit_pipe_surface

router = APIRouter(tags=["发版单"])


class ReleaseIn(BaseModel):
    应用编号: int
    目标版本: str = Field(min_length=1)
    制品摘要: str = ""
    金丝雀比例: int | None = None
    备注: str = ""


class PipelineIn(BaseModel):
    流水线: str


class ReclaimIn(BaseModel):
    挂接编号: int | None = None


@router.get("/发版单")
def list_releases():
    with db() as conn:
        rows = conn.execute(RELEASE_SELECT + " ORDER BY r.id DESC").fetchall()
        return enrich_releases(conn, rows)


@router.get("/发版单/{release_id}")
def one_release(release_id: int):
    with db() as conn:
        return enrich_release(conn, get_release(conn, release_id))


@router.get("/发版单/{release_id}/入闸判定")
def gate_decision(release_id: int):
    """读侧可重放：只根据库内持久事实推导能不能入闸、将形成什么裁决。"""
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        return evaluate_gate(conn, r)


@router.get("/制品挂接")
def all_links(仅有效: bool = False):
    sql = """
        SELECT l.*, r.serial AS 发版单号
          FROM artifact_links l
          JOIN releases r ON r.id = l.release_id
    """
    if 仅有效:
        sql += " WHERE l.link_state!='已收回'"
    sql += " ORDER BY l.id DESC LIMIT 500"
    with db() as conn:
        return rows_to_list(conn.execute(sql).fetchall())


@router.get("/发版单/{release_id}/挂接行")
def release_links(release_id: int, 全部: bool = False):
    with db() as conn:
        r = conn.execute("SELECT id FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        if 全部:
            rows = conn.execute(
                "SELECT * FROM artifact_links WHERE release_id=? ORDER BY id DESC",
                (release_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM artifact_links
                WHERE release_id=? AND link_state!='已收回'
                ORDER BY id DESC
                """,
                (release_id,),
            ).fetchall()
        return rows_to_list(rows)


@router.post("/发版单")
def create_release(body: ReleaseIn):
    with db() as conn:
        assert_app_enabled(conn, body.应用编号)
        ratio = body.金丝雀比例
        if ratio is None:
            ratio = int(get_setting(conn, "金丝雀默认比例", "10") or "10")
        if ratio < 0 or ratio > 100:
            raise HTTPException(400, "金丝雀比例须在 0 到 100")
        serial = next_serial(conn)
        from app.services.common import now_str

        now = now_str()
        cur = conn.execute(
            """
            INSERT INTO releases(
              serial, app_id, target_version, artifact_digest, status, pipeline_status,
              canary_ratio, note, created_at
            ) VALUES (?, ?, ?, ?, '待入闸', '未回执', ?, ?, ?)
            """,
            (serial, body.应用编号, body.目标版本, body.制品摘要, ratio, body.备注, now),
        )
        rid = cur.lastrowid
        audit(conn, "发版单", rid, "申请发版", serial)
        return enrich_release(conn, get_release(conn, rid))


@router.post("/发版单/{release_id}/入闸")
def admit(release_id: int):
    try:
        return admit_release(release_id)
    except GateRejected as gr:
        raise HTTPException(status_code=gr.status_code, detail=gr.message)


@router.post("/发版单/{release_id}/收回挂接")
def reclaim_link(release_id: int, body: ReclaimIn | None = None):
    link_id = body.挂接编号 if body else None
    return reclaim_dirty_links(release_id, link_id)


@router.post("/发版单/{release_id}/流水线")
def set_pipeline(release_id: int, body: PipelineIn):
    if body.流水线 not in ("未回执", "绿", "红"):
        raise HTTPException(400, "流水线状态非法")
    return record_pipeline(release_id, body.流水线)
