"""入闸证据同命运账本：过线戳、入闸事件、流水线入闸水位与缺口重放。

这里只呈现库内持久事实，绝不做 surface 式粉刷：

* 新入闸在同一个 ``BEGIN IMMEDIATE`` 事务里写 releases / links / stamp /
  event / water / audit；
* 读侧把四方证据重放成一张缺口清单，假成功单号会被揭穿；
* 历史拧花状态不自动洗白，只钉缺口，直到整笔收回或显式纠偏。
"""

from __future__ import annotations

from typing import Any

from app.services.common import now_str

WATER_SCOPE = "流水线入闸总水位"
ACTIVE_STATUSES = ("已入闸", "晋升中", "已放行", "回滚中")

FATAL_STAMP_STATES = {
    "红",
    "红戳",
    "未过",
    "未成立",
    "失败",
    "已回滚",
    "回滚",
    "半截",
    "刷新半截",
    "half",
    "failed",
    "error",
    "red",
}

STAMP_STATE_ALIASES = {
    "绿": ("绿", "已成立", 1, 1, 1, 0, 0),
    "绿色": ("绿", "已成立", 1, 1, 1, 0, 0),
    "green": ("绿", "已成立", 1, 1, 1, 0, 0),
    "pass": ("绿", "已成立", 1, 1, 1, 0, 0),
    "passed": ("绿", "已成立", 1, 1, 1, 0, 0),
    "红": ("红", "红入闸已成立", 1, 0, 0, 1, 0),
    "红色": ("红", "红入闸已成立", 1, 0, 0, 1, 0),
    "red": ("红", "红入闸已成立", 1, 0, 0, 1, 0),
    "未回执": ("未过", "未成立", 0, 0, 0, 0, 0),
    "未过": ("未过", "未成立", 0, 0, 0, 0, 0),
    "未成立": ("未过", "未成立", 0, 0, 0, 0, 0),
    "pending": ("未过", "未成立", 0, 0, 0, 0, 0),
}


def _bool(v: Any) -> bool:
    return v is True or v == 1 or v == "1" or str(v).lower() == "true"


def normalize_stamp_color(value: Any) -> str:
    if isinstance(value, bool):
        return "绿" if value else "红"
    text = str(value or "").strip()
    if text in ("1", "true", "True"):
        return "绿"
    if text in ("0", "false", "False"):
        return "红"
    return text if text in STAMP_STATE_ALIASES else "未回执"


def stamp_values(color: Any) -> dict[str, Any]:
    key = normalize_stamp_color(color)
    row = STAMP_STATE_ALIASES.get(key, STAMP_STATE_ALIASES["未回执"])
    c, state, established, passed, green, red, fatal = row
    return {
        "color": c,
        "state": state,
        "established": established,
        "passed": passed,
        "green": green,
        "red": red,
        "fatal": fatal,
    }


def insert_stamp(
    conn,
    release_id: int,
    color: Any,
    *,
    artifact_digest: str = "",
    source: str = "pipeline",
    note: str = "",
    fatal: bool | int | None = None,
    half_state: str | None = None,
    state: str | None = None,
) -> int:
    """插入一枚新的过线戳修订。调用方负责把它放在同命运事务内。"""
    r = conn.execute("SELECT serial FROM releases WHERE id=?", (release_id,)).fetchone()
    serial = r["serial"] if r is not None else str(release_id)
    rev_row = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) + 1 AS rev FROM cross_line_stamps WHERE release_id=?",
        (release_id,),
    ).fetchone()
    revision = int(rev_row["rev"])
    vals = stamp_values(color)

    # 显式致命态/半截态优先于普通颜色投影，供故障夹具和历史拧花使用。
    if state:
        state_text = str(state)
        if state_text in FATAL_STAMP_STATES or _bool(fatal):
            vals["fatal"] = 1
        if state_text in ("半截", "刷新半截", "half"):
            half_state = "半截"
            vals["fatal"] = 1
        if state_text in ("已回滚", "回滚", "rolled_back"):
            vals["fatal"] = 1
        if state_text in ("红戳", "红", "red"):
            vals["fatal"] = 1
            vals["red"] = 1
        vals["state"] = state_text
    if fatal is not None:
        vals["fatal"] = 1 if _bool(fatal) else 0
    if half_state:
        vals["half_state"] = half_state
        if half_state == "半截":
            vals["fatal"] = 1

    now = now_str()
    cur = conn.execute(
        """
        INSERT INTO cross_line_stamps(
          release_id, stamp_no, revision, color, state, established, passed,
          green, red, fatal, half_state, pipeline_status, artifact_digest,
          source, note, created_at, refreshed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            release_id,
            f"GX-{serial}-{revision:02d}",
            revision,
            vals["color"],
            vals["state"],
            vals["established"],
            vals["passed"],
            vals["green"],
            vals["red"],
            vals["fatal"],
            half_state or "完成",
            normalize_stamp_color(color),
            artifact_digest or "",
            source,
            note,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def row_to_stamp_side(row) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    side = {
        **d,
        # SQLite 原始 0/1 字段在英文探针里也必须是真正 bool，不能只靠中文别名。
        "fatal": bool(d.get("fatal")),
        "established": bool(d.get("established")),
        "passed": bool(d.get("passed")),
        "green": bool(d.get("green")),
        "red": bool(d.get("red")),
        "half": d.get("half_state") == "半截",
        "id": d.get("id"),
        "release_id": d.get("release_id"),
        "stamp_id": d.get("id"),
        "stamp_no": d.get("stamp_no"),
        "stampNo": d.get("stamp_no"),
        "stamp_state": d.get("state"),
        "stampState": d.get("state"),
        "stamp_color": d.get("color"),
        "stampColor": d.get("color"),
        "编号": d.get("id"),
        "过线戳编号": d.get("id"),
        "过线戳单号": d.get("stamp_no"),
        "版本": d.get("revision"),
        "过线戳颜色": d.get("color"),
        "过线戳状态": d.get("state"),
        "过线戳成立": bool(d.get("established")),
        "过线戳已过线": bool(d.get("passed")),
        "过线戳绿": bool(d.get("green")),
        "过线戳红": bool(d.get("red")),
        "异常红戳": bool(d.get("fatal")),
        "致命": bool(d.get("fatal")),
        "半截": d.get("half_state") == "半截",
        "刷新半截": d.get("half_state") == "半截",
        "stamp": d,
        "testid": "stamp-side-read",
        "testids": ["cross-line-stamp", "stamp-side-read", "cross-line-stamp-side-read"],
        "可打开": True,
        "ok": bool(d.get("established")) and not d.get("fatal") and d.get("half_state") != "半截",
    }
    return side


def current_stamp(conn, release_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM cross_line_stamps WHERE release_id=? ORDER BY id DESC LIMIT 1",
        (release_id,),
    ).fetchone()
    return row_to_stamp_side(row)


def stamp_by_id(conn, stamp_id: int | None) -> dict[str, Any] | None:
    if not stamp_id:
        return None
    row = conn.execute("SELECT * FROM cross_line_stamps WHERE id=?", (stamp_id,)).fetchone()
    return row_to_stamp_side(row)


def event_to_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    return {
        **d,
        "编号": d.get("id"),
        "入闸事件编号": d.get("id"),
        "发版单编号": d.get("release_id"),
        "过线戳编号": d.get("stamp_id"),
        "事件类型": d.get("event_type"),
        "事件状态": d.get("state"),
        "裁决": d.get("verdict"),
        "水位增量": d.get("water_delta"),
        "时间": d.get("created_at"),
        "valid": d.get("state") == "有效",
        "active": d.get("state") == "有效",
        "有效": d.get("state") == "有效",
        "event_type": d.get("event_type"),
        "eventType": d.get("event_type"),
        "stamp_id": d.get("stamp_id"),
        "water_delta": d.get("water_delta"),
        "waterDelta": d.get("water_delta"),
        "testid": "admit-event",
        "testids": ["admit-event", "event-side-read", "event-book-row"],
        "event": d,
    }


def active_admit_event(conn, release_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM admit_events
         WHERE release_id=? AND event_type='入闸成立' AND state='有效'
         ORDER BY id DESC LIMIT 1
        """,
        (release_id,),
    ).fetchone()
    return event_to_dict(row)


def list_admit_events(conn, release_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
    sql = "SELECT * FROM admit_events"
    params: list[Any] = []
    if release_id is not None:
        sql += " WHERE release_id=?"
        params.append(release_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit or 500), 2000)))
    return [event_to_dict(r) for r in conn.execute(sql, params).fetchall()]


def water_state(conn) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM admit_water_level WHERE scope=?", (WATER_SCOPE,)
    ).fetchone()
    stored = int(row["level"]) if row is not None else 0
    true_level = int(
        conn.execute(
            """
            SELECT COUNT(*) AS c
              FROM admit_water_entries w
              JOIN admit_events e ON e.id=w.event_id
             WHERE e.state='有效'
            """
        ).fetchone()["c"]
    )
    orphan_entries = [
        dict(r)
        for r in conn.execute(
            """
            SELECT w.* FROM admit_water_entries w
            LEFT JOIN admit_events e ON e.id=w.event_id
            WHERE e.id IS NULL OR e.state!='有效'
            """
        ).fetchall()
    ]
    diff = stored - true_level
    return {
        "口径": WATER_SCOPE,
        "scope": WATER_SCOPE,
        "水位": true_level,
        "真实水位": true_level,
        "流水线入闸水位": true_level,
        "level": true_level,
        "true_level": true_level,
        "trueLevel": true_level,
        "真实水位数": true_level,
        "账面水位": stored,
        "stored_level": stored,
        "storedLevel": stored,
        "账面流水线入闸水位": stored,
        "water_level": stored,
        "waterLevel": stored,
        "stored_water_level": stored,
        "差额": diff,
        "水位差额": diff,
        "difference": diff,
        "water_difference": diff,
        "waterDifference": diff,
        "孤儿水位令牌": orphan_entries,
        "orphan_entries": orphan_entries,
        "orphanEntries": orphan_entries,
        "可信": diff == 0 and not orphan_entries,
        "trusted": diff == 0 and not orphan_entries,
        "trustworthy": diff == 0 and not orphan_entries,
        "aligned": diff == 0 and not orphan_entries,
        "亮灯": "绿" if diff == 0 and not orphan_entries else "缺口红",
        "lit": diff == 0 and not orphan_entries,
        "testid": "water-level",
        "testids": ["water-level", "pipeline-admit-water-level", "global-water-level", "overview-water-level"],
    }


def water_token(conn, release_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM admit_water_entries WHERE release_id=?", (release_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def _gap(
    gap_type: str,
    *,
    release_id: int | None = None,
    expected: str = "",
    actual: str = "",
    detail: str = "",
    severity: str = "高",
) -> dict[str, Any]:
    severity_en = {"高": "high", "中": "medium", "低": "low"}.get(severity, severity)
    return {
        "缺口类型": gap_type,
        "type": gap_type,
        "gap_type": gap_type,
        "gapType": gap_type,
        "发版单编号": release_id,
        "release_id": release_id,
        "releaseId": release_id,
        "期望": expected,
        "实际": actual,
        "expected": expected,
        "actual": actual,
        "说明": detail,
        "detail": detail,
        "严重度": severity,
        "severity": severity,
        "severity_en": severity_en,
        "severityEn": severity_en,
        "pinned": True,
        "钉住": True,
        "testid": "gap-pin",
        "testids": ["gap-pin", "reconcile-gap-pin", "event-gap-pin"],
    }


def detect_gaps(conn, release_id: int | None = None) -> list[dict[str, Any]]:
    where = ""
    params: tuple[Any, ...] = ()
    if release_id is not None:
        where = "WHERE id=?"
        params = (release_id,)
    releases = conn.execute(f"SELECT * FROM releases {where} ORDER BY id", params).fetchall()

    gaps: list[dict[str, Any]] = []
    for r in releases:
        rid = int(r["id"])
        event = active_admit_event(conn, rid)
        latest_stamp = current_stamp(conn, rid)
        gate_stamp = stamp_by_id(conn, event["stamp_id"] if event else None) if event else None
        stamp = gate_stamp or latest_stamp
        token = water_token(conn, rid)
        audits = conn.execute(
            "SELECT id FROM audit_events WHERE ref_id=? AND action='入闸'", (rid,)
        ).fetchall()
        link = conn.execute(
            """
            SELECT * FROM artifact_links
             WHERE release_id=? AND link_state='挂接成功'
             ORDER BY id DESC LIMIT 1
            """,
            (rid,),
        ).fetchone()

        # 已回到待入闸/终态且没有裁决痕迹时，残留的有效事件或令牌本身就是缺口。
        # 不能把 event/token 同时放进 requires 后再在 not requires 分支里判断，
        # 否则这个缺口类型会永远不可达。
        if (
            r["status"] not in ACTIVE_STATUSES
            and not r["gate_verdict"]
            and (event is not None or token is not None)
        ):
            gaps.append(_gap(
                "入闸证据无在途单态",
                release_id=rid,
                expected="待入闸/已收回时无有效入闸事件或水位令牌",
                actual=f"状态={r['status']}，事件={event is not None}，水位={token is not None}",
                detail="存在脱离发版单状态的入闸事件/水位，疑似假加或收回未净",
            ))
            continue

        requires = (
            r["status"] in ACTIVE_STATUSES
            or bool(r["gate_verdict"])
            or event is not None
            or token is not None
        )
        if not requires:
            continue

        serial = r["serial"]
        if event is not None and event.get("stamp_id") and gate_stamp is None:
            gaps.append(_gap(
                "入闸事件与过线戳对不上", release_id=rid,
                expected=f"事件引用过线戳#{event['stamp_id']} 必须存在",
                actual="被引用过线戳不存在",
                detail="入闸事件册仍在，但过线戳证据缺失",
            ))
        if stamp is None:
            gaps.append(_gap(
                "过线戳缺失", release_id=rid,
                expected="入闸同事务写过线戳", actual="无过线戳",
                detail=f"单号 {serial} 缺少过线戳旁读证据",
            ))
        else:
            fatal_state = (
                not stamp["过线戳成立"]
                or stamp["异常红戳"]
                or stamp["刷新半截"]
                or stamp["过线戳状态"] in FATAL_STAMP_STATES
            )
            # 绿入闸的成功灯不能由致命红/未成立戳陪跑。普通红戳配红入闸裁决
            # 是旧臂 B 允许并告警的自洽结局，不算证据撕裂。
            green_success = r["gate_verdict"] == "绿入闸" or (
                r["status"] in ACTIVE_STATUSES and not r["gate_verdict"]
            )
            if fatal_state:
                gaps.append(_gap(
                    "过线戳红或未成立", release_id=rid,
                    expected="过线戳成立且无致命红/半截/回滚态",
                    actual=f"状态={stamp['过线戳状态']}，成立={stamp['过线戳成立']}",
                    detail=f"单号 {serial} 的成功灯缺少成立过线戳",
                ))
            elif green_success and stamp["过线戳红"] and r["gate_verdict"] != "红入闸":
                gaps.append(_gap(
                    "过线戳红陪绿单号", release_id=rid,
                    expected="绿入闸成功单由绿戳成立",
                    actual="过线戳为红",
                    detail=f"单号 {serial} 亮绿但过线戳为红，必须钉缺口",
                ))

        # 最新旁读戳若处于半截/未成立，即使入闸时戳完好，也必须钉住刷新口缺口。
        if latest_stamp is not None and (gate_stamp is None or latest_stamp["编号"] != gate_stamp["编号"]):
            latest_fatal = (
                not latest_stamp["过线戳成立"]
                or latest_stamp["异常红戳"]
                or latest_stamp["刷新半截"]
                or latest_stamp["过线戳状态"] in FATAL_STAMP_STATES
            )
            if latest_fatal:
                gaps.append(_gap(
                    "过线戳刷新半截或红戳", release_id=rid,
                    expected="过线戳刷新完整且状态成立",
                    actual=f"最新戳#{latest_stamp['编号']} 状态={latest_stamp['过线戳状态']}",
                    detail="过线戳刷新口留下半截/回滚/致命红状态，入闸与旁读已撕裂",
                ))
            elif latest_stamp["过线戳红"] and r["pipeline_status"] != "红" and r["gate_verdict"] == "绿入闸":
                refresh_audit = conn.execute(
                    """
                    SELECT id FROM audit_events
                     WHERE ref_id=? AND action='流水线回执' AND note LIKE ?
                     ORDER BY id DESC LIMIT 1
                    """,
                    (rid, f"%过线戳#{latest_stamp['编号']}%"),
                ).fetchone()
                if refresh_audit is None:
                    gaps.append(_gap(
                        "过线戳旁读与流水线不一致", release_id=rid,
                        expected="红戳须有同口径流水线刷新/审计",
                        actual=f"最新戳#{latest_stamp['编号']}为红，但流水线={r['pipeline_status']}",
                        detail="疑似直接改红过线戳旁读，必须钉缺口",
                    ))

        if event is None:
            gaps.append(_gap(
                "入闸事件缺失", release_id=rid,
                expected="恰好一条有效入闸成立事件", actual="无有效入闸事件",
                detail=f"单号 {serial} 过线戳/水位可能仍在，但事件册缺证",
            ))
        elif r["gate_verdict"] and event.get("裁决") != r["gate_verdict"]:
            gaps.append(_gap(
                "入闸事件裁决不一致", release_id=rid,
                expected=r["gate_verdict"], actual=str(event.get("裁决")),
                detail="事件册裁决与发版单裁决不同",
            ))

        if token is None:
            gaps.append(_gap(
                "流水线入闸水位令牌缺失", release_id=rid,
                expected="本单占用一个水位令牌", actual="无水位令牌",
                detail=f"单号 {serial} 未进入同命运水位",
            ))
        elif event is None or token.get("event_id") != event["编号"]:
            gaps.append(_gap(
                "水位假加或挂错事件", release_id=rid,
                expected=f"水位令牌挂接入闸事件#{event['编号'] if event else '?'}",
                actual=f"event_id={token.get('event_id')}",
                detail="水位增加没有对应有效入闸事件",
            ))

        if not audits:
            gaps.append(_gap(
                "成功审计缺失", release_id=rid,
                expected="一条 action=入闸 的成功审计", actual="无成功入闸审计",
                detail="成功单号无法由审计重放",
                severity="中",
            ))

        if link is None:
            gaps.append(_gap(
                "挂接行缺失", release_id=rid,
                expected="一条挂接成功行", actual="无成功挂接行",
                detail=f"单号 {serial} 不得无缺口陪绿",
            ))
        elif r["gate_verdict"] and link["verdict"] != r["gate_verdict"]:
            gaps.append(_gap(
                "挂接行裁决不一致", release_id=rid,
                expected=r["gate_verdict"], actual=link["verdict"],
                detail="挂接行与入闸裁决打架",
            ))

        if not r["artifact_digest"]:
            gaps.append(_gap(
                "制品摘要旁读缺失", release_id=rid,
                expected="非空制品摘要", actual="空摘要",
                detail="入闸闸规要求摘要非空；历史脏在途单钉缺口",
                severity="中",
            ))

    water = water_state(conn)
    if water["差额"] != 0:
        gaps.append(_gap(
            "流水线入闸水位差额",
            expected=f"账面水位={water['真实水位']}",
            actual=f"账面水位={water['账面水位']}，差额={water['差额']}",
            detail="水位被假加/漏减，账面与有效入闸事件数不一致",
        ))
    for orphan in water["孤儿水位令牌"]:
        # 逐单缺口通常已经覆盖；这里保证孤儿行也进入对账差额明细。
        rid = orphan.get("release_id")
        if not any(g["缺口类型"] == "水位假加或挂错事件" and g["发版单编号"] == rid for g in gaps):
            gaps.append(_gap(
                "孤儿水位令牌", release_id=rid,
                expected="水位令牌必须挂有效入闸事件", actual=f"water={orphan}",
                detail="存在无有效事件的水位增加",
            ))

    return gaps


def difference_summary(gaps: list[dict[str, Any]], water: dict[str, Any]) -> dict[str, Any]:
    def count(*needles: str) -> int:
        return sum(
            1 for g in gaps
            if any(n in (g.get("缺口类型") or "") for n in needles)
        )

    stamp_gaps = [g for g in gaps if "过线戳" in (g.get("缺口类型") or "")]
    event_gaps = [g for g in gaps if "事件" in (g.get("缺口类型") or "")]
    water_gaps = [g for g in gaps if "水位" in (g.get("缺口类型") or "")]
    link_gaps = [g for g in gaps if "挂接" in (g.get("缺口类型") or "")]
    audit_gaps = [g for g in gaps if "审计" in (g.get("缺口类型") or "")]
    digest_gaps = [g for g in gaps if "摘要" in (g.get("缺口类型") or "") or "digest" in (g.get("缺口类型") or "").lower()]
    water_numeric_difference = abs(int(water.get("差额") or 0))
    # 差额总数按「可定位的差额/缺口记录」计数；水位数值差已经会生成一条
    # 「流水线入闸水位差额」gap，不能再把数值 abs(diff) 与 gap 条数相加。
    total = len(gaps)
    return {
        "差额总数": total,
        "differenceTotal": total,
        "缺口项总数": len(gaps),
        "缺口总数": len(gaps),
        "水位数值差额": water_numeric_difference,
        "waterNumericDifference": water_numeric_difference,
        "gaps": gaps,
        "明细": gaps,
        "过线戳差额": len(stamp_gaps),
        "入闸事件差额": len(event_gaps),
        "水位差额": water.get("差额", 0),
        "流水线入闸水位差额": water.get("差额", 0),
        "水位缺口数": len(water_gaps),
        "挂接行差额": len(link_gaps),
        "审计差额": len(audit_gaps),
        "制品摘要差额": len(digest_gaps),
        "分类差额": {
            "过线戳": len(stamp_gaps),
            "入闸事件": len(event_gaps),
            "水位": len(water_gaps),
            "水位数值": water_numeric_difference,
            "挂接行": len(link_gaps),
            "审计": len(audit_gaps),
            "digest": len(digest_gaps),
        },
        "过线戳缺口": stamp_gaps,
        "入闸事件缺口": event_gaps,
        "水位缺口": water_gaps,
        "挂接行缺口": link_gaps,
        "审计缺口": audit_gaps,
        "对齐": not gaps and water.get("差额") == 0 and water.get("可信", False),
        "aligned": not gaps and water.get("差额") == 0 and water.get("可信", False),
        "水位": water,
    }


def sync_gap_pins(conn) -> list[dict[str, Any]]:
    """把动态缺口钉进 admit_gap_pins，并清除已经消失的旧钉。"""
    gaps = detect_gaps(conn)
    now = now_str()
    active_keys = {(g.get("发版单编号"), g["缺口类型"]) for g in gaps}
    existing = conn.execute("SELECT * FROM admit_gap_pins WHERE cleared=0").fetchall()
    for row in existing:
        key = (row["release_id"], row["gap_type"])
        if key not in active_keys:
            conn.execute(
                "UPDATE admit_gap_pins SET cleared=1, pinned=0, cleared_at=? WHERE id=?",
                (now, row["id"]),
            )
    for g in gaps:
        rid = g.get("发版单编号")
        gt = g["缺口类型"]
        row = conn.execute(
            "SELECT id FROM admit_gap_pins WHERE release_id IS ? AND gap_type=?",
            (rid, gt),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO admit_gap_pins(
                  release_id, gap_type, severity, expected, actual, detail,
                  pinned, cleared, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (rid, gt, g.get("严重度", "高"), g.get("期望", ""), g.get("实际", ""),
                 g.get("说明", ""), now),
            )
        else:
            conn.execute(
                """
                UPDATE admit_gap_pins
                   SET severity=?, expected=?, actual=?, detail=?,
                       pinned=1, cleared=0, cleared_at=NULL
                 WHERE id=?
                """,
                (g.get("严重度", "高"), g.get("期望", ""), g.get("实际", ""),
                 g.get("说明", ""), row["id"]),
            )
    return gaps


def replay_release(conn, release_id: int) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
    if r is None:
        missing_gap = _gap("发版单不存在", release_id=release_id)
        return {
            "发版单编号": release_id,
            "release_id": release_id,
            "releaseId": release_id,
            "id": release_id,
            "存在": False,
            "exists": False,
            "可重放成功": False,
            "replay_ok": False,
            "fake_success": False,
            "假成功": False,
            "缺口": [missing_gap],
            "gaps": [missing_gap],
        }
    raw = dict(r)
    event = active_admit_event(conn, release_id)
    stamp = stamp_by_id(conn, event["stamp_id"] if event else None) or current_stamp(conn, release_id)
    token = water_token(conn, release_id)
    audits = [
        dict(x)
        for x in conn.execute(
            "SELECT id AS 编号, action AS 动作, note AS 备注, created_at AS 时间 "
            "FROM audit_events WHERE ref_id=? ORDER BY id",
            (release_id,),
        ).fetchall()
    ]
    gaps = detect_gaps(conn, release_id)
    coherent = not gaps
    serial = raw["serial"]
    looks_admitted = raw.get("status") in ACTIVE_STATUSES or bool(raw.get("gate_verdict"))
    fake_success = not coherent and looks_admitted
    return {
        "存在": True,
        "exists": True,
        "编号": release_id,
        "id": release_id,
        "release_id": release_id,
        "releaseId": release_id,
        "发版单编号": release_id,
        "单号": serial,
        "serial": serial,
        "发版单号": serial,
        "成功单号": serial if coherent else None,
        "success_no": serial if coherent else None,
        "successNo": serial if coherent else None,
        "可疑成功单号": serial if looks_admitted else None,
        "被揭穿假入闸成功单号": serial if fake_success else None,
        "fake_success_no": serial if fake_success else None,
        "fakeSuccessNo": serial if fake_success else None,
        "可重放成功": coherent,
        "replay_ok": coherent,
        "replayOk": coherent,
        "假成功": fake_success,
        "fake_success": fake_success,
        "fakeSuccess": fake_success,
        "同命运": coherent,
        "过线戳": stamp,
        "过线戳旁读": stamp,
        "入闸事件": event,
        "入闸事件册": list_admit_events(conn, release_id),
        "水位令牌": token,
        "审计": audits,
        "缺口": gaps,
        "gaps": gaps,
        "缺口数": len(gaps),
        "gap_count": len(gaps),
        "gapCount": len(gaps),
    }


def evidence_for_release(conn, enriched: dict[str, Any], raw_row=None) -> dict[str, Any]:
    rid = int(enriched["编号"])
    stamp = current_stamp(conn, rid)
    event = active_admit_event(conn, rid)
    token = water_token(conn, rid)
    gaps = detect_gaps(conn, rid)
    replay = replay_release(conn, rid)
    water = water_state(conn)
    digest = enriched.get("制品摘要") or ""
    env = {
        "环境编号": enriched.get("当前环境编号"),
        "代号": enriched.get("当前环境"),
        "名称": enriched.get("当前环境名"),
        "当前环境": enriched.get("当前环境"),
        "environment": enriched.get("当前环境"),
        "可打开": bool(enriched.get("当前环境")),
    }
    digest_side = {
        "制品摘要": digest,
        "digest": digest,
        "齐全": bool(digest),
        "可打开": True,
        "testid": "digest-side-read",
    }
    return {
        "过线戳": stamp,
        "过线戳旁读": stamp,
        "cross_line_stamp": stamp,
        "stamp_side_read": stamp,
        "入闸事件": event,
        "admit_event": event,
        "入闸事件册": list_admit_events(conn, rid),
        "水位令牌": token,
        "流水线入闸水位": 1 if token is not None else 0,
        "入闸水位": 1 if token is not None else 0,
        "water_level": 1 if token is not None else 0,
        "水位亮灯": token is not None and not gaps,
        "水位账": water,
        "缺口": gaps,
        "gaps": gaps,
        "缺口数": len(gaps),
        "证据重放": replay,
        "事件重放": replay,
        "digest旁读": digest_side,
        "制品摘要旁读": digest_side,
        "环境旁读": env,
        "environment_side_read": env,
        "发版详情旁读": {
            "发版单号": enriched.get("发版单号"),
            "状态": enriched.get("状态"),
            "过线戳旁读": stamp,
            "digest旁读": digest_side,
            "环境旁读": env,
            "可打开": True,
        },
    }


def backfill_evidence(conn) -> None:
    """旧库/种子平滑补账：只根据已有裁决补证据，不处理拧花缺口。"""
    water_row = conn.execute(
        "SELECT scope FROM admit_water_level WHERE scope=?", (WATER_SCOPE,)
    ).fetchone()
    if water_row is None:
        conn.execute(
            "INSERT INTO admit_water_level(scope, level, updated_at) VALUES(?, 0, ?)",
            (WATER_SCOPE, now_str()),
        )

    rows = conn.execute(
        """
        SELECT r.* FROM releases r
         WHERE r.gate_verdict IS NOT NULL
            OR r.status IN ('已入闸','晋升中','已放行','回滚中')
         ORDER BY r.id
        """
    ).fetchall()
    for r in rows:
        rid = int(r["id"])
        event = active_admit_event(conn, rid)
        if event is None:
            color = r["gate_pipeline_status"] if r["gate_pipeline_status"] in ("绿", "红") else r["pipeline_status"]
            stamp_id = insert_stamp(
                conn,
                rid,
                color if color in ("绿", "红") else "绿",
                artifact_digest=r["artifact_digest"],
                source="backfill",
                note="旧库证据账本平滑补录",
            )
            now = r["admitted_at"] or now_str()
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM admit_events WHERE release_id=?", (rid,)
            ).fetchone()["c"]
            cur = conn.execute(
                """
                INSERT INTO admit_events(
                  release_id, stamp_id, event_no, event_type, state, verdict,
                  water_delta, idempotency_key, note, created_at
                ) VALUES (?, ?, ?, '入闸成立', '有效', ?, 1, ?, ?, ?)
                """,
                (
                    rid,
                    stamp_id,
                    f"EV-{r['serial']}-{count + 1:02d}",
                    r["gate_verdict"] or "绿入闸",
                    f"backfill-admit-{rid}-{count + 1}",
                    "旧库入闸事件平滑补录",
                    now,
                ),
            )
            event_id = cur.lastrowid
        else:
            event_id = event["编号"]
            stamp_id = event.get("stamp_id") or insert_stamp(
                conn, rid, r["gate_pipeline_status"] or r["pipeline_status"],
                artifact_digest=r["artifact_digest"], source="backfill"
            )
            conn.execute("UPDATE admit_events SET stamp_id=? WHERE id=?", (stamp_id, event_id))

        exists_token = conn.execute(
            "SELECT release_id FROM admit_water_entries WHERE release_id=?", (rid,)
        ).fetchone()
        if exists_token is None:
            conn.execute(
                "INSERT INTO admit_water_entries(release_id, event_id, level_delta, created_at) "
                "VALUES (?, ?, 1, ?)",
                (rid, event_id, r["admitted_at"] or now_str()),
            )
        else:
            conn.execute(
                "UPDATE admit_water_entries SET event_id=? WHERE release_id=?",
                (event_id, rid),
            )

    count = conn.execute("SELECT COUNT(*) AS c FROM admit_water_entries").fetchone()["c"]
    conn.execute(
        "UPDATE admit_water_level SET level=?, updated_at=? WHERE scope=?",
        (count, now_str(), WATER_SCOPE),
    )
