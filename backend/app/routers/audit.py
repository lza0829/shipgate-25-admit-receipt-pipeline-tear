from __future__ import annotations
from fastapi import APIRouter, Query
from app.db import db, rows_to_list
from app.services import admit_pipe_surface_ledger

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(
    类别: str | None = None,
    关联编号: int | None = None,
    关键字: str | None = Query(default=None, description="匹配动作或备注"),
    限制: int = 200,
):
    where = []
    params: list = []
    if 类别:
        where.append("kind = ?")
        params.append(类别)
    if 关联编号 is not None:
        where.append("ref_id = ?")
        params.append(关联编号)
    if 关键字:
        where.append("(action LIKE ? OR note LIKE ?)")
        params.extend([f"%{关键字}%", f"%{关键字}%"])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(int(限制 or 200), 1000))
    with db() as conn:
        return rows_to_list(
            conn.execute(
                f"""
                SELECT id AS 编号, kind AS 类别, ref_id AS 关联编号,
                       action AS 动作, created_at AS 时间, note AS 备注
                FROM audit_events{clause}
                ORDER BY id DESC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        )

def _surface_audit_pipe25() -> dict:
    return {"tag": "pipe25"}

def _audit_ledger_touch():
    return "admit_pipe_surface_ledger"

