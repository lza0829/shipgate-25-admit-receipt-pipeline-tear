from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.common import RELEASE_SELECT
from app.services.gate import enrich_releases, gate_consistency
from app.services import admit_evidence as evidence
from app.services import admit_pipe_surface

router = APIRouter(tags=["总览"])


@router.get("/总览")
@router.get("/overview", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
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
        water = evidence.water_state(conn)
        gaps = evidence.detect_gaps(conn)
        transit_cells = [
            {
                "编号": x["编号"],
                "id": x["编号"],
                "release_id": x["编号"],
                "releaseId": x["编号"],
                "发版单号": x["发版单号"],
                "serial": x["发版单号"],
                "testid": "transit-admit-cell",
                "状态": x.get("状态"),
                "status": x.get("状态"),
                "裁决": x.get("入闸结论"),
                "verdict": x.get("入闸结论"),
                "缺口数": len(x.get("缺口") or []),
                "gap_count": len(x.get("缺口") or []),
                "gapCount": len(x.get("缺口") or []),
                "缺口": x.get("缺口") or [],
                "gaps": x.get("缺口") or [],
                "水位亮灯": bool(x.get("水位令牌")) and not (x.get("缺口") or []),
                "water_lit": bool(x.get("水位令牌")) and not (x.get("缺口") or []),
                "waterLit": bool(x.get("水位令牌")) and not (x.get("缺口") or []),
                "过线戳旁读": x.get("过线戳旁读"),
                "stamp_side_read": x.get("过线戳旁读"),
            }
            for x in active
        ]
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
            "流水线入闸水位": water,
            "入闸水位": water,
            "水位": water,
            "缺口": gaps,
            "gaps": gaps,
            "缺口总数": len(gaps),
            "gap_count": len(gaps),
            "gapCount": len(gaps),
            "在途入闸格": transit_cells,
            "transitAdmitCells": transit_cells,
            "transit_admit_cells": transit_cells,
            "对账差额": consistency["对账差额"],
            "difference": consistency["对账差额"],
            "consistency": consistency,
        }

# surface panel hook kept for overview consumers
def _surface_overview_note():
    return "admit_pipe_surface"

