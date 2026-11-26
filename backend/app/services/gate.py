"""入闸裁决核心。

架构臂（实现方自行敲定，全局唯一贯彻）：**红单可告警入闸**。

- 流水线绿  -> 裁决「绿入闸」，挂接行按绿登记，无告警；
- 流水线红  -> 裁决「红入闸」，允许入闸，但落下不可抹除的红入闸告警事实，
               挂接行按红裁决登记（绝不写成绿/假成功），总览露出告警，
               晋升入口对红入闸硬不可用，对账按红入闸单列；
- 未回执    -> 无法形成裁决，驳回入闸；
- 空制品摘要、状态不符、应用停用、并行在途 -> 驳回（空制品摘要为另立闸规，
               此处仅沿用既有硬拒）。

流水线当下颜色与入闸写口在 ``BEGIN IMMEDIATE`` 写锁内读取并裁决，
发版单态、制品挂接行、审计在同一事务提交；任何异常整笔回滚到入闸前。
``evaluate_gate`` 是纯读侧推导，可对同一库状态重复重放。
"""

from __future__ import annotations

from app.db import db
from app.services.common import audit as persist_audit
from app.services.common import get_release, now_str

ARM = "红单可告警入闸"
VERDICT_GREEN = "绿入闸"
VERDICT_RED = "红入闸"
PIPELINE_GREEN = "绿"
PIPELINE_RED = "红"

ACTIVE_STATUSES = ("已入闸", "晋升中", "已放行", "回滚中")
# 允许把半截入闸整笔收回为「待入闸」的状态：尚未发生后续晋升
RECLAIMABLE_STATUSES = ("已入闸",)


class GateRejected(Exception):
    """入闸判定为驳回。闸内写事务已回滚，发版单保持入闸前状态。"""

    def __init__(self, message: str, *, reasons=None, status_code: int = 400, decision=None):
        super().__init__(message)
        self.message = message
        self.reasons = list(reasons or [message])
        self.status_code = status_code
        self.decision = decision


# ---------------------------------------------------------------------------
# 读侧：可重放的入闸判定
# ---------------------------------------------------------------------------

def evaluate_gate(conn, r) -> dict:
    """根据库内持久事实推导入闸判定。只读、确定、可重放。

    ``r`` 为 releases 原始行（SELECT *）。
    """
    reasons: list[str] = []
    checks: dict[str, bool] = {}

    status_ok = r["status"] == "待入闸"
    checks["状态待入闸"] = status_ok
    if not status_ok:
        reasons.append(f"仅待入闸可入闸（当前状态：{r['status']}）")

    app = conn.execute("SELECT id, status FROM apps WHERE id=?", (r["app_id"],)).fetchone()
    app_ok = app is not None and app["status"] == "启用"
    checks["应用启用"] = app_ok
    if app is None:
        reasons.append("应用不存在")
    elif not app_ok:
        reasons.append("应用已停用，禁止入闸")

    digest_ok = bool(r["artifact_digest"])
    checks["制品摘要非空"] = digest_ok
    if not digest_ok:
        reasons.append("缺少制品摘要，禁止入闸")

    pipeline = r["pipeline_status"]
    receipt_ok = pipeline in (PIPELINE_GREEN, PIPELINE_RED)
    checks["流水线已有红绿回执"] = receipt_ok
    if not receipt_ok:
        reasons.append("未收到流水线回执（未回执），无法形成入闸裁决")

    parallel = conn.execute(
        """
        SELECT id, serial FROM releases
        WHERE app_id=? AND id!=? AND status IN ('已入闸','晋升中','已放行','回滚中')
        """,
        (r["app_id"], r["id"]),
    ).fetchone()
    checks["无并行在途单"] = parallel is None
    if parallel is not None:
        reasons.append(f"同应用已有在途发版单 {parallel['serial']}，禁止并行入闸")

    # 流水线颜色 -> 拟定裁决（即便被其他闸规驳回，也如实给出颜色对应物）
    if pipeline == PIPELINE_GREEN:
        planned = VERDICT_GREEN
        alarm = False
    elif pipeline == PIPELINE_RED:
        planned = VERDICT_RED
        alarm = True
    else:
        planned = None
        alarm = False

    admissible = not reasons
    if admissible:
        judge_text = f"准予{planned}" + ("（红单告警入闸）" if alarm else "")
    else:
        judge_text = "驳回入闸"

    return {
        "架构臂": ARM,
        "可入闸": admissible,
        "admittable": admissible,
        "判定": judge_text,
        "裁决": planned if admissible else None,
        "verdict": planned if admissible else None,
        "拟定入闸结论": planned,
        "告警": alarm if admissible else False,
        "alarm": alarm if admissible else False,
        "流水线结论": pipeline,
        "原因": reasons,
        "reasons": reasons,
        "检查": checks,
    }


def promotion_block_reason(r) -> str | None:
    """晋升入口可用性的唯一裁决口径：红入闸不得按绿通过；当下红灯冻结晋升。"""
    if r["status"] not in ("已入闸", "晋升中"):
        return "当前状态不可晋升"
    if not r["gate_verdict"]:
        return "发版单缺少入闸裁决（疑似半截入闸/脏挂接），请先整笔收回后重新入闸"
    if r["gate_verdict"] == VERDICT_RED:
        return "红入闸告警单不得按绿通过晋升；请先处置告警并重新裁决"
    if r["pipeline_status"] == PIPELINE_RED:
        if r["gate_verdict"] == VERDICT_GREEN:
            return "入闸后流水线翻红，晋升冻结（入闸结论仍为绿入闸，不与当下判定混写）"
        return "当前流水线为红，冻结晋升"
    return None


# ---------------------------------------------------------------------------
# 行装配：把读侧判定、告警标、挂接行嵌进发版单 JSON
# ---------------------------------------------------------------------------

def enrich_release(conn, row) -> dict:
    d = dict(row)
    rid = d["编号"]
    raw = conn.execute("SELECT * FROM releases WHERE id=?", (rid,)).fetchone()

    d["流水线结论"] = d.get("流水线")
    verdict = d.get("入闸结论")
    pipeline = d.get("流水线")

    red_gate = verdict == VERDICT_RED or bool(d.get("入闸告警标记"))
    flipped_red = verdict == VERDICT_GREEN and pipeline == PIPELINE_RED
    d["红入闸告警"] = bool(red_gate)
    d["入闸后翻红"] = bool(flipped_red)
    if red_gate:
        d["告警标"] = "红入闸告警"
    elif flipped_red:
        d["告警标"] = "入闸后翻红"
    else:
        d["告警标"] = ""

    link_id = d.get("挂接编号")
    if link_id is not None:
        d["挂接行"] = {
            "编号": link_id,
            "状态": d.get("挂接状态"),
            "结论": d.get("挂接结论"),
            "告警": bool(d.get("挂接告警标记")),
            "制品": d.get("挂接制品"),
            "备注": d.get("挂接备注"),
            "时间": d.get("挂接时间"),
        }
        # 挂接行不得与入闸裁决打架
        d["挂接裁决一致"] = (
            d.get("挂接结论") == verdict
            and bool(d.get("挂接告警标记")) == bool(d.get("入闸告警标记"))
        )
    else:
        d["挂接行"] = None
        d["挂接裁决一致"] = verdict is None

    if raw is not None:
        decision = evaluate_gate(conn, raw)
        d["可入闸"] = decision["可入闸"]
        d["入闸判定"] = decision["判定"]
        d["入闸判定明细"] = decision
        block = promotion_block_reason(raw)
        d["晋升可用"] = block is None
        d["晋升不可用原因"] = block or ""
    else:  # pragma: no cover - 行来自查询，理论上不会丢
        d["可入闸"] = False
        d["入闸判定"] = "发版单不存在"
        d["入闸判定明细"] = None
        d["晋升可用"] = False
        d["晋升不可用原因"] = "发版单不存在"

    return d


def enrich_releases(conn, rows) -> list[dict]:
    return [enrich_release(conn, r) for r in rows]


# ---------------------------------------------------------------------------
# 入闸写口：同一判定、同一事务
# ---------------------------------------------------------------------------

def _safe_audit(kind: str, ref_id, action: str, note: str) -> None:
    """驳回/收回审计走独立事务，保证闸内事务回滚后仍可核；自身失败不得掩盖原结局。"""
    try:
        with db() as conn:
            persist_audit(conn, kind, ref_id, action, note[:800])
    except Exception:
        pass


def admit_release(release_id: int, *, fault: str | None = None) -> dict:
    """把发版单入闸。

    ``fault`` 仅供夹具注入半截故障（``"after_link"``：挂接行写入后抛错），
    不对 HTTP 暴露。
    """
    try:
        with db() as conn:
            # 抢写锁后再读再判，杜绝「读绿 -> 对方翻红提交 -> 按绿入闸」的竞态
            conn.execute("BEGIN IMMEDIATE")
            r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
            if r is None:
                raise GateRejected("发版单不存在", status_code=404)

            decision = evaluate_gate(conn, r)
            if not decision["可入闸"]:
                raise GateRejected(
                    decision["原因"][0],
                    reasons=decision["原因"],
                    decision=decision,
                )

            verdict = decision["拟定入闸结论"]
            alarm = 1 if verdict == VERDICT_RED else 0
            now = now_str()
            test_env = conn.execute("SELECT id FROM environments WHERE code='测'").fetchone()
            test_env_id = test_env["id"] if test_env is not None else r["current_env_id"]

            # 条件状态翻转，rowcount 兜底并发重复入闸
            cur = conn.execute(
                """
                UPDATE releases
                   SET status='已入闸', admitted_at=?, current_env_id=?,
                       gate_verdict=?, gate_pipeline_status=?, gate_alarm=?
                 WHERE id=? AND status='待入闸'
                """,
                (now, test_env_id, verdict, r["pipeline_status"], alarm, release_id),
            )
            if cur.rowcount != 1:
                fresh = conn.execute("SELECT status FROM releases WHERE id=?", (release_id,)).fetchone()
                raise GateRejected(
                    f"发版单状态已被并发改变（当前：{fresh['status'] if fresh else '未知'}），请刷新后重试",
                    status_code=409,
                )

            # 旧的非收回挂接行（含半截「挂接中」/历史假成功）先整笔收回，
            # 保证一笔入闸至多有一行有效挂接，绝不残留假成功行。
            conn.execute(
                """
                UPDATE artifact_links
                   SET link_state='已收回', reclaimed_at=?,
                       note='重新入闸裁决，旧挂接行整笔收回'
                 WHERE release_id=? AND link_state!='已收回'
                """,
                (now, release_id),
            )

            link_note = (
                "红入闸告警：流水线红，挂接行按红裁决如实登记，不得视作绿通过"
                if alarm
                else "绿入闸：流水线绿，挂接成功"
            )
            lc = conn.execute(
                """
                INSERT INTO artifact_links(
                  release_id, artifact_digest, pipeline_status, verdict,
                  link_state, alarm, note, created_at
                ) VALUES (?, ?, ?, ?, '挂接成功', ?, ?, ?)
                """,
                (
                    release_id,
                    r["artifact_digest"],
                    r["pipeline_status"],
                    verdict,
                    alarm,
                    link_note,
                    now,
                ),
            )
            link_id = lc.lastrowid

            if fault == "after_link":
                # 夹具注入：此刻发版单已改、挂接行已插，但审计/提交尚未完成
                raise RuntimeError("注入故障：挂接行写入后、事务提交前中断")

            audit_note = (
                f"{verdict}｜流水线结论={r['pipeline_status']}"
                f"｜挂接行#{link_id}=挂接成功"
                f"｜红入闸告警={'是' if alarm else '否'}"
                f"｜制品={r['artifact_digest']}"
            )
            persist_audit(conn, "发版单", release_id, "入闸", audit_note)

            out = enrich_release(conn, get_release(conn, release_id))
            out["入闸回执"] = {
                "判定": decision["判定"],
                "裁决": verdict,
                "告警": bool(alarm),
                "挂接编号": link_id,
                "挂接状态": "挂接成功",
                "时间": now,
            }
            return out
    except GateRejected as gr:
        _safe_audit(
            "发版单",
            release_id,
            "入闸驳回",
            "；".join(gr.reasons),
        )
        raise
    except Exception as exc:  # 含注入故障 / 数据库异常：闸内已整体回滚
        _safe_audit(
            "发版单",
            release_id,
            "入闸异常整笔收回",
            f"{type(exc).__name__}: {exc}｜发版单与挂接行已回到入闸前",
        )
        raise


# ---------------------------------------------------------------------------
# 流水线回执（含入闸后翻红的独立标记，不回写入闸裁决）
# ---------------------------------------------------------------------------

def record_pipeline(release_id: int, status: str) -> dict:
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if r is None:
            from fastapi import HTTPException

            raise HTTPException(404, "发版单不存在")

        prev = r["pipeline_status"]
        now = now_str()
        conn.execute("UPDATE releases SET pipeline_status=? WHERE id=?", (status, release_id))

        if r["status"] != "待入闸" and status == PIPELINE_RED and prev != PIPELINE_RED:
            verdict = r["gate_verdict"] or "未形成入闸结论"
            note = (
                f"入闸后翻红告警：{prev}→红；入闸结论「{verdict}」为入闸当下事实，不被改写；"
                f"当下红灯另立标记，晋升按当下规则冻结"
            )
        elif r["status"] != "待入闸" and status == PIPELINE_GREEN and prev != PIPELINE_GREEN:
            note = f"入闸后流水线回执：{prev}→绿；入闸结论「{r['gate_verdict'] or '未形成'}」不变"
        else:
            note = status
        persist_audit(conn, "发版单", release_id, "流水线回执", note)
        return enrich_release(conn, get_release(conn, release_id))


# ---------------------------------------------------------------------------
# 半截脏挂接：整笔收回
# ---------------------------------------------------------------------------

def reclaim_dirty_links(release_id: int, link_id: int | None = None) -> dict:
    """收回半截脏挂接行；若入闸已无有效挂接行支撑，则把整笔入闸收回为待入闸。"""
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if r is None:
            from fastapi import HTTPException

            raise HTTPException(404, "发版单不存在")

        now = now_str()
        if link_id is not None:
            targets = conn.execute(
                "SELECT * FROM artifact_links WHERE id=? AND release_id=? AND link_state!='已收回'",
                (link_id, release_id),
            ).fetchall()
            if not targets:
                from fastapi import HTTPException

                raise HTTPException(404, "未找到可收回的指定挂接行")
        else:
            # 半截脏挂接的典型形态：停在「挂接中」
            targets = conn.execute(
                """
                SELECT * FROM artifact_links
                WHERE release_id=? AND link_state='挂接中'
                ORDER BY id
                """,
                (release_id,),
            ).fetchall()

        reclaimed_ids = [t["id"] for t in targets]
        for tid in reclaimed_ids:
            conn.execute(
                "UPDATE artifact_links SET link_state='已收回', reclaimed_at=? WHERE id=?",
                (now, tid),
            )

        success_left = conn.execute(
            """
            SELECT COUNT(*) AS c FROM artifact_links
            WHERE release_id=? AND link_state='挂接成功'
            """,
            (release_id,),
        ).fetchone()["c"]

        whole_reset = False
        if success_left == 0 and r["status"] in RECLAIMABLE_STATUSES:
            # 没有任何有效挂接行支撑这次入闸：整笔回到入闸前
            whole_reset = True
            conn.execute(
                """
                UPDATE releases
                   SET status='待入闸', admitted_at=NULL, current_env_id=NULL,
                       gate_verdict=NULL, gate_pipeline_status='未回执', gate_alarm=0
                 WHERE id=?
                """,
                (release_id,),
            )

        if reclaimed_ids or whole_reset:
            if whole_reset:
                note = (
                    f"半截脏挂接整笔收回：收回挂接行 {reclaimed_ids or '无成功行支撑'}；"
                    f"发版单 {r['serial']} 已回到待入闸，入闸裁决/在途计数一并撤销"
                )
            else:
                note = f"半截脏挂接行收回：挂接行 {reclaimed_ids}；有效挂接仍在，发版单状态不变"
            persist_audit(conn, "发版单", release_id, "半截挂接整笔收回", note)

        out = enrich_release(conn, get_release(conn, release_id))
        return {
            "发版单": out,
            "收回挂接行": reclaimed_ids,
            "整笔重置": whole_reset,
            "时间": now,
        }


# ---------------------------------------------------------------------------
# 一致性核对：总览 / 对账共用同一口径
# ---------------------------------------------------------------------------

def gate_consistency(conn) -> dict:
    red_active = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM releases
        WHERE gate_verdict='红入闸' AND status IN ({','.join('?' for _ in ACTIVE_STATUSES)})
        """,
        ACTIVE_STATUSES,
    ).fetchone()["c"]
    green_active = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM releases
        WHERE gate_verdict='绿入闸' AND status IN ({','.join('?' for _ in ACTIVE_STATUSES)})
        """,
        ACTIVE_STATUSES,
    ).fetchone()["c"]

    dirty_linking = conn.execute(
        "SELECT COUNT(*) AS c FROM artifact_links WHERE link_state='挂接中'"
    ).fetchone()["c"]

    # 在途/已入闸类发版单缺少有效成功挂接行
    rows_without_link = conn.execute(
        f"""
        SELECT r.id, r.serial, r.status, r.gate_verdict
          FROM releases r
         WHERE r.status IN ({','.join('?' for _ in ACTIVE_STATUSES)})
           AND NOT EXISTS (
                 SELECT 1 FROM artifact_links l
                  WHERE l.release_id=r.id AND l.link_state='挂接成功'
               )
         ORDER BY r.id
        """,
        ACTIVE_STATUSES,
    ).fetchall()

    # 挂接行结论/告警与发版单裁决不一致
    mismatched = conn.execute(
        """
        SELECT l.id AS link_id, l.release_id AS release_id,
               l.verdict AS link_verdict, r.gate_verdict AS release_verdict
          FROM artifact_links l
          JOIN releases r ON r.id = l.release_id
         WHERE l.link_state='挂接成功'
           AND (l.verdict IS NOT r.gate_verdict
                OR l.alarm != r.gate_alarm)
        """
    ).fetchall()

    return {
        "架构臂": ARM,
        "红入闸在途": red_active,
        "绿入闸在途": green_active,
        "挂接中脏行": dirty_linking,
        "无挂接行的在途单": [dict(x) for x in rows_without_link],
        "裁决不一致挂接行": [dict(x) for x in mismatched],
        "一致": dirty_linking == 0 and not rows_without_link and not mismatched,
    }
