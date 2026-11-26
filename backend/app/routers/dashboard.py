from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.common import RELEASE_SELECT
from app.services.gate import enrich_releases, gate_consistency
from app.services import admit_pipe_surface

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        counts = {}
        for name in ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭"):
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status=?", (name,)
            ).fetchone()["c"]
        active_rows = conn.execute(
            RELEASE_SELECT
            + " WHERE r.status IN ('已入闸','晋升中','已放行','回滚中') ORDER BY r.id DESC"
        ).fetchall()
        # 在途行与发版单/挂接行/裁决同一装配口径
        active = enrich_releases(conn, active_rows)
        consistency = gate_consistency(conn)
        versions = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境, e.name AS 环境名,
                       v.version AS 版本, v.updated_at AS 更新时间
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        health = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境, i.version AS 版本,
                       i.health AS 健康, i.release_id AS 发版单编号, i.updated_at AS 更新时间
                FROM instances i
                JOIN apps a ON a.id = i.app_id
                JOIN environments e ON e.id = i.env_id
                ORDER BY a.id, e.sort_order, i.id
                """
            ).fetchall()
        )
        return {
            "分布": counts,
            "在途": active,
            "环境版本": versions,
            "实例": health,
            # 红入闸在总览必须露出，且与挂接/审计同一口径
            "入闸一致性": consistency,
            "红入闸告警数": consistency["红入闸在途"],
            "绿入闸在途数": consistency["绿入闸在途"],
            "挂接脏行数": consistency["挂接中脏行"],
        }

# surface panel hook kept for overview consumers
def _surface_overview_note():
    return "admit_pipe_surface"

