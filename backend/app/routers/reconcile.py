from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.gate import gate_consistency
from app.services import admit_evidence as evidence
from app.services import admit_pipe_surface_ledger

router = APIRouter(tags=["对账"])


@router.get("/对账")
@router.get("/reconcile", include_in_schema=False)
@router.get("/reconciliation", include_in_schema=False)
@router.get("/对账报告", include_in_schema=False)
def reconcile():
    with db() as conn:
        release_stats = {
            "申请数": conn.execute("SELECT COUNT(*) AS c FROM releases").fetchone()["c"],
            "已放行": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已放行'"
            ).fetchone()["c"],
            "已回滚": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已回滚'"
            ).fetchone()["c"],
            "在途": conn.execute(
                """
                SELECT COUNT(*) AS c FROM releases
                WHERE status IN ('已入闸','晋升中','已放行','回滚中')
                """
            ).fetchone()["c"],
            # 红入闸不得混作绿通过：两种口径分列
            "绿入闸在途": conn.execute(
                """
                SELECT COUNT(*) AS c FROM releases
                WHERE gate_verdict='绿入闸'
                  AND status IN ('已入闸','晋升中','已放行','回滚中')
                """
            ).fetchone()["c"],
            "红入闸在途": conn.execute(
                """
                SELECT COUNT(*) AS c FROM releases
                WHERE gate_verdict='红入闸'
                  AND status IN ('已入闸','晋升中','已放行','回滚中')
                """
            ).fetchone()["c"],
            "按绿通过口径在途": conn.execute(
                """
                SELECT COUNT(*) AS c FROM releases
                WHERE gate_verdict='绿入闸'
                  AND status IN ('已入闸','晋升中','已放行','回滚中')
                """
            ).fetchone()["c"],
        }
        version_vs_instance = rows_to_list(
            conn.execute(
                """
                SELECT a.name AS 应用, e.code AS 环境,
                       v.version AS 名册版本,
                       GROUP_CONCAT(i.version, ',') AS 实例版本们,
                       SUM(CASE WHEN i.health='探针红' THEN 1 ELSE 0 END) AS 红灯数
                FROM app_env_versions v
                JOIN apps a ON a.id = v.app_id
                JOIN environments e ON e.id = v.env_id
                LEFT JOIN instances i ON i.app_id = v.app_id AND i.env_id = v.env_id
                GROUP BY v.app_id, v.env_id
                ORDER BY a.id, e.sort_order
                """
            ).fetchall()
        )
        promote_count = conn.execute("SELECT COUNT(*) AS c FROM promote_events").fetchone()["c"]
        consistency = gate_consistency(conn)
        gaps = evidence.sync_gap_pins(conn)
        water = evidence.water_state(conn)
        difference = consistency["对账差额"]
        dirty_links = rows_to_list(
            conn.execute(
                """
                SELECT l.id AS 挂接编号, l.release_id AS 发版单编号, r.serial AS 发版单号,
                       l.link_state AS 挂接状态, l.verdict AS 挂接结论, l.alarm AS 告警,
                       l.created_at AS 时间
                  FROM artifact_links l
                  JOIN releases r ON r.id = l.release_id
                 WHERE l.link_state != '已收回'
                 ORDER BY l.id DESC
                """
            ).fetchall()
        )
        for link in dirty_links:
            link_id = link.get("挂接编号")
            release_id = link.get("发版单编号")
            link.update({
                "id": link_id,
                "link_id": link_id,
                "挂接id": link_id,
                "release_id": release_id,
                "releaseId": release_id,
                "serial": link.get("发版单号"),
                "release_serial": link.get("发版单号"),
                "link_state": link.get("挂接状态"),
                "linkState": link.get("挂接状态"),
                "state": link.get("挂接状态"),
                "verdict": link.get("挂接结论"),
                "link_verdict": link.get("挂接结论"),
                "alarm": bool(link.get("告警")),
                "alarmed": bool(link.get("告警")),
                "created_at": link.get("时间"),
            })
        return {
            "发版统计": release_stats,
            "晋升事件数": promote_count,
            "名册与实例": version_vs_instance,
            "入闸一致性": consistency,
            "挂接行": dirty_links,
            "对账差额": difference,
            "difference": difference,
            "缺口": gaps,
            "gaps": gaps,
            "流水线入闸水位": water,
            "水位": water,
            "重放": {
                "缺口总数": len(gaps),
                "可重放": not gaps,
                "事件册": evidence.list_admit_events(conn, limit=100),
            },
        }

def _surface_reconcile_pipe25() -> dict:
    return {"tag": "pipe25", "ready": True}

def _ledger_touch():
    return "admit_pipe_surface_ledger"

