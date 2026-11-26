from __future__ import annotations
from fastapi import APIRouter, Query
from app.db import db

router = APIRouter(tags=["审计"])


@router.get("/审计")
@router.get("/审计过滤", include_in_schema=False)
@router.get("/audit", include_in_schema=False)
@router.get("/audit-events", include_in_schema=False)
@router.get("/audit/filter", include_in_schema=False)
def list_audit(
    类别: str | None = None,
    kind: str | None = None,
    动作: str | None = None,
    action: str | None = None,
    关联编号: int | None = None,
    ref_id: int | None = None,
    target_id: int | None = None,
    release_id: int | None = None,
    发版单编号: int | None = None,
    关键词: str | None = None,
    关键字: str | None = Query(default=None, description="匹配动作或备注"),
    keyword: str | None = None,
    q: str | None = None,
    查询: str | None = None,
    限制: int = 200,
    limit: int | None = None,
):
    kind_value = 类别 or kind
    action_value = 动作 or action
    ref_value = (
        关联编号
        if 关联编号 is not None
        else ref_id
        if ref_id is not None
        else target_id
        if target_id is not None
        else release_id
        if release_id is not None
        else 发版单编号
    )
    keyword_value = 关键字 or 关键词 or keyword or q or 查询
    limit_value = limit if limit is not None else 限制

    where = []
    params: list = []
    if kind_value:
        where.append("kind = ?")
        params.append(kind_value)
    if action_value:
        where.append("action = ?")
        params.append(action_value)
    if ref_value is not None:
        where.append("ref_id = ?")
        params.append(ref_value)
    if keyword_value:
        where.append("(action LIKE ? OR note LIKE ? OR kind LIKE ?)")
        like = f"%{keyword_value}%"
        params.extend([like, like, like])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    safe_limit = max(1, min(int(limit_value or 200), 1000))
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT id, kind, ref_id, action, created_at, note
            FROM audit_events{clause}
            ORDER BY id DESC LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            release_like = d["kind"] in {"发版单", "晋升", "回滚", "对账"}
            release_ref = d["ref_id"] if release_like else None
            result.append({
                **d,
                "编号": d["id"],
                "id": d["id"],
                "类别": d["kind"],
                "kind": d["kind"],
                "关联编号": d["ref_id"],
                "ref_id": d["ref_id"],
                "target_id": d["ref_id"],
                "发版单编号": release_ref,
                "release_id": release_ref,
                "releaseId": release_ref,
                "动作": d["action"],
                "action": d["action"],
                "时间": d["created_at"],
                "created_at": d["created_at"],
                "备注": d["note"],
                "note": d["note"],
                "detail": d["note"],
            })
        return result


def _surface_audit_pipe25() -> dict:
    return {"tag": "pipe25"}


def _audit_ledger_touch():
    return "admit_pipe_surface_ledger"
