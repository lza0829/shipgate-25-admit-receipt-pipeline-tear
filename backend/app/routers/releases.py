from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
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

router = APIRouter(tags=["发版单"])


class ReleaseIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    应用编号: int = Field(
        validation_alias=AliasChoices("应用编号", "app_id", "appId", "应用id")
    )
    目标版本: str = Field(
        min_length=1,
        validation_alias=AliasChoices("目标版本", "version", "targetVersion", "target_version"),
    )
    制品摘要: str = Field(
        default="",
        validation_alias=AliasChoices("制品摘要", "digest", "artifactDigest", "artifact_digest"),
    )
    金丝雀比例: int | None = Field(
        default=None,
        validation_alias=AliasChoices("金丝雀比例", "canaryRatio", "canary_ratio", "ratio"),
    )
    备注: str = Field(
        default="",
        validation_alias=AliasChoices("备注", "note", "comment", "remark"),
    )


class PipelineIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    流水线: str | None = None
    流水线状态: str | None = None
    状态: str | None = None
    color: str | None = None
    status: str | None = None
    pipeline: str | None = None
    故障: str | None = None
    fault: str | None = None
    半截: bool | None = None
    half: bool | None = None
    致命: bool | None = None
    fatal: bool | None = None
    戳状态: str | None = None
    stamp_state: str | None = None

    def resolved_status(self) -> str:
        extra = self.model_extra or {}
        raw = (
            self.流水线
            or self.流水线状态
            or self.status
            or self.pipeline
            or self.color
            or self.状态
            or extra.get("pipeline_status")
            or extra.get("pipelineStatus")
            or extra.get("pipelineState")
            or extra.get("state")
            or ""
        )
        text = str(raw).strip()
        aliases = {
            "green": "绿", "passed": "绿", "pass": "绿", "ok": "绿", "成功": "绿",
            "red": "红", "failed": "红", "fail": "红", "失败": "红",
            "pending": "未回执", "none": "未回执", "": "未回执",
        }
        return aliases.get(text.lower(), text)


class AdmitIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    故障: str | None = None
    fault: str | None = None


class ReclaimIn(BaseModel):
    挂接编号: int | None = None
    link_id: int | None = None
    强制整笔退回: bool | None = None
    force: bool | None = None


@router.get("/发版单")
@router.get("/releases", include_in_schema=False)
def list_releases():
    with db() as conn:
        rows = conn.execute(RELEASE_SELECT + " ORDER BY r.id DESC").fetchall()
        return enrich_releases(conn, rows)


@router.get("/发版单/{release_id}")
@router.get("/releases/{release_id}", include_in_schema=False)
@router.get("/release/{release_id}", include_in_schema=False)
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
@router.get("/挂接行", include_in_schema=False)
@router.get("/artifact-links", include_in_schema=False)
def all_links(仅有效: bool = False, active_only: bool = False):
    only_active = 仅有效 or active_only
    sql = """
        SELECT l.*, r.serial AS 发版单号
          FROM artifact_links l
          JOIN releases r ON r.id = l.release_id
    """
    if only_active:
        sql += " WHERE l.link_state!='已收回'"
    sql += " ORDER BY l.id DESC LIMIT 500"
    with db() as conn:
        return rows_to_list(conn.execute(sql).fetchall())


@router.get("/发版单/{release_id}/挂接行")
@router.get("/releases/{release_id}/links", include_in_schema=False)
def release_links(release_id: int, 全部: bool = False, all: bool = False):
    include_all = 全部 or all
    with db() as conn:
        r = conn.execute("SELECT id FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        if include_all:
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
@router.post("/releases", include_in_schema=False)
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
@router.post("/releases/{release_id}/admit", include_in_schema=False)
@router.post("/admit/{release_id}", include_in_schema=False)
def admit(release_id: int, body: AdmitIn | None = None):
    extra = body.model_extra if body is not None else {}
    fault = None
    if body is not None:
        fault = (
            body.故障
            or body.fault
            or (extra or {}).get("injectFault")
            or (extra or {}).get("inject_fault")
            or (extra or {}).get("faultType")
            or (extra or {}).get("fault_type")
        )
    try:
        return admit_release(release_id, fault=fault)
    except GateRejected as gr:
        raise HTTPException(status_code=gr.status_code, detail=gr.message)
    except RuntimeError as exc:
        # 夹具故障注入：事务已整体回滚，对外给 500，不能把半截当成功。
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/发版单/{release_id}/收回挂接")
@router.post("/releases/{release_id}/reclaim", include_in_schema=False)
@router.post("/reclaim/{release_id}", include_in_schema=False)
def reclaim_link(release_id: int, body: ReclaimIn | None = None):
    link_id = (body.挂接编号 or body.link_id) if body else None
    force = bool((body.强制整笔退回 or body.force) if body else False)
    return reclaim_dirty_links(release_id, link_id, force=force)


@router.post("/发版单/{release_id}/流水线")
@router.post("/releases/{release_id}/pipeline", include_in_schema=False)
@router.post("/pipeline/{release_id}", include_in_schema=False)
def set_pipeline(release_id: int, body: PipelineIn | None = None):
    body = body or PipelineIn()
    status = body.resolved_status()
    extra = body.model_extra or {}
    fault = (
        body.故障
        or body.fault
        or extra.get("injectFault")
        or extra.get("inject_fault")
        or extra.get("faultType")
        or extra.get("fault_type")
    )
    if status not in ("未回执", "绿", "红"):
        raise HTTPException(400, "流水线状态非法")
    return record_pipeline(
        release_id,
        status,
        fault=fault,
        half=bool(body.半截 if body.半截 is not None else body.half),
        fatal=body.致命 if body.致命 is not None else body.fatal,
        stamp_state=body.戳状态 or body.stamp_state or extra.get("stampState") or extra.get("state"),
    )
