import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api'

function pipeClass(p) {
  if (p === '绿') return 'pipe pipe-green'
  if (p === '红') return 'pipe pipe-red'
  return 'pipe pipe-gray'
}

function PipelineBadge({ value }) {
  return (
    <span className={pipeClass(value)} data-testid="pipeline-badge">
      <i className="dot" />流水线{value}
    </span>
  )
}

function VerdictBadge({ row }) {
  if (row.入闸结论 === '红入闸' || row.红入闸告警) {
    return <span className="badge badge-red" data-testid="gate-verdict">红入闸</span>
  }
  if (row.入闸结论 === '绿入闸') {
    return <span className="badge badge-green" data-testid="gate-verdict">绿入闸</span>
  }
  return <span className="badge badge-gray" data-testid="gate-verdict">未裁决</span>
}

function AlarmTag({ row }) {
  if (row.告警标 === '红入闸告警' || row.红入闸告警) {
    return <span className="alarm-tag" data-testid="red-gate-alarm">⚠ 红入闸告警</span>
  }
  if (row.入闸后翻红) {
    return <span className="warn-tag" data-testid="post-gate-red">⚑ 入闸后翻红</span>
  }
  return null
}

function LinkCell({ row }) {
  const link = row.挂接行
  if (!link) return <span className="muted">无挂接行</span>
  const cls = link.结论 === '红入闸' || link.告警 ? 'link link-red' : 'link link-green'
  return (
    <div className={cls} data-testid="artifact-link-row" data-link-id={link.编号}>
      <div>#{link.编号} · {link.状态}</div>
      <div className="muted">
        裁决 {link.结论 || '-'}
        {link.告警 ? ' · 告警' : ''}
      </div>
      <div className="digest" title={link.制品}>{link.制品 || '（空摘要）'}</div>
      {row.挂接裁决一致 === false && <div className="err">挂接与入闸裁决不一致</div>}
    </div>
  )
}

function StampCell({ row, onRefresh, onHalfFixture }) {
  const stamp = row.过线戳旁读
  const bad = (row.缺口 || []).some((g) => String(g.缺口类型 || '').includes('过线戳'))
  const cls = !stamp ? 'stamp stamp-missing' : bad || stamp.异常红戳 || stamp.刷新半截 ? 'stamp stamp-red' : stamp.过线戳红 ? 'stamp stamp-red' : 'stamp stamp-green'
  return (
    <div className="evidence-stack" data-testid="cross-line-stamp-side-read">
      <div className={cls} data-testid="stamp-side-read" data-stamp-id={stamp?.编号 || ''}>
        <div
          data-testid="cross-line-stamp"
          data-state={stamp?.过线戳状态 || '缺失'}
          data-ok={stamp?.ok ? 'true' : 'false'}
        >
          <b>过线戳旁读</b>
          {stamp ? (
            <>
              <span>#{stamp.编号} {stamp.过线戳单号}</span>
              <span>{stamp.过线戳颜色}/{stamp.过线戳状态}</span>
              {stamp.刷新半截 && <span className="err">刷新半截</span>}
            </>
          ) : <span className="err">无过线戳</span>}
        </div>
      </div>
      <div className="button-row tiny-row">
        <button type="button" className="secondary tiny" data-testid="stamp-refresh-button"
          onClick={() => onRefresh(row.编号, row.流水线 === '红' ? '红' : '绿')}>
          刷新过线戳
        </button>
        <button type="button" className="danger tiny" data-testid="half-refresh-fixture-button"
          onClick={() => onHalfFixture(row.编号)}>
          <span data-testid="half-refresh-button">半截刷新夹具</span>
        </button>
      </div>
    </div>
  )
}

function WaterCell({ row }) {
  const lit = row.在途入闸格?.水位亮灯
  return (
    <div className="evidence-stack" data-testid="row-water-level">
      <span className={lit ? 'water-light green' : 'water-light red'} data-testid="water-level-light">
        <i />{lit ? '水位亮已入闸' : '缺口未清水位不亮'}
      </span>
      <span data-testid="pipeline-admit-water-level">
        本单令牌：{row.水位令牌 ? `#${row.水位令牌.event_id}` : '无'}
      </span>
      {row.流水线入闸水位 && (
        <span className="muted" data-testid="global-water-level">
          <span data-testid="water-level" data-lit={lit ? 'true' : 'false'}>
            真{row.流水线入闸水位.真实水位} / 账{row.流水线入闸水位.账面水位} / 差{row.流水线入闸水位.差额}
          </span>
        </span>
      )}
    </div>
  )
}

function GapList({ gaps = [], testid = 'gap-list' }) {
  if (!gaps.length) {
    return (
      <div data-testid="gap-list" data-gap-count="0">
        <div className="ok-text" data-testid={`${testid}-empty`}>无缺口，证据可重放</div>
      </div>
    )
  }
  return (
    <div data-testid="gap-list" data-gap-count={gaps.length}>
      <div data-testid="gap-pins">
        <ul className="gap-list" data-testid={testid}>
          {gaps.map((g, i) => (
            <li
              key={`${g.缺口类型}-${i}`}
              data-testid="gap-pin"
              data-gap-type={g.缺口类型 || g.type || g.gap_type || ''}
              data-release-id={g.发版单编号 ?? g.release_id ?? ''}
              className="gap-item"
            >
              <b>{g.缺口类型 || g.type}</b>
              <div>期望：{g.期望 || g.expected || '-'}</div>
              <div>实际：{g.实际 || g.actual || '-'}</div>
              <div className="muted">{g.说明 || g.detail}</div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

function DetailDrawer({ id, detail, replay, onClose }) {
  if (id == null) return null
  const d = detail || {}
  const rp = replay || d.证据重放 || {}
  const stamp = d.过线戳旁读
  const digestSide = d.digest旁读 || { 制品摘要: d.制品摘要, digest: d.制品摘要, 齐全: !!d.制品摘要 }
  const envSide = d.环境旁读 || { 当前环境: d.当前环境, 名称: d.当前环境名, 代号: d.当前环境 }
  return (
    <div className="detail-drawer" data-testid="release-detail-side-read">
      <div className="drawer-head">
        <h3 data-testid="release-detail">发版详情旁读 · #{id} {d.发版单号}</h3>
        <button type="button" className="secondary" onClick={onClose}>关闭</button>
      </div>

      <section data-testid="digest-side-read" className="side-card">
        <h4>digest 旁读</h4>
        <div>制品摘要：<code>{digestSide.制品摘要 || digestSide.digest || '（空）'}</code></div>
        <div>齐全：{digestSide.齐全 ? '是' : '否'}</div>
      </section>

      <section data-testid="environment-side-read" className="side-card">
        <h4>环境旁读</h4>
        <div data-testid="env-side-read">环境：{envSide.代号 || envSide.当前环境 || '-'} {envSide.名称 || envSide.当前环境名 || ''}</div>
      </section>

      <section className="side-card" data-testid="detail-stamp-side-read">
        <h4>过线戳旁读</h4>
        {stamp ? (
          <pre>{JSON.stringify({
            id: stamp.编号, no: stamp.过线戳单号, color: stamp.过线戳颜色,
            state: stamp.过线戳状态, established: stamp.过线戳成立,
            fatal: stamp.异常红戳, half: stamp.刷新半截,
          }, null, 2)}</pre>
        ) : <p className="err">无过线戳</p>}
      </section>

      <section className="side-card" data-testid="detail-event-book">
        <h4>入闸事件 / 事件册</h4>
        {(d.入闸事件册 || []).length === 0 ? <p className="err">事件册缺证</p> : (
          <table data-testid="detail-event-table">
            <thead><tr><th>编号</th><th>类型</th><th>状态</th><th>裁决</th><th>戳</th><th>时间</th></tr></thead>
            <tbody>
              {(d.入闸事件册 || []).map((e) => (
                <tr key={e.编号}>
                  <td>{e.编号}</td><td>{e.事件类型}</td><td>{e.事件状态}</td>
                  <td>{e.裁决 || '-'}</td><td>{e.过线戳编号 || '-'}</td><td>{e.时间}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="side-card" data-testid="replay-panel">
        <h4>事件重放 / 缺口</h4>
        <div data-testid={rp.可重放成功 ? 'replay-ok' : 'replay-failed'} data-replay-ok={rp.可重放成功 ? 'true' : 'false'}>
          可重放成功：<b>{rp.可重放成功 ? '是' : '否'}</b>
        </div>
        {rp.成功单号 && rp.可重放成功 && (
          <div data-testid="success-no">成功单号：{rp.成功单号}</div>
        )}
        {rp.被揭穿假入闸成功单号 && (
          <div data-testid="fake-success" className="err">
            假成功单号：
            <span data-testid="fake-success-no">{rp.被揭穿假入闸成功单号}</span>
          </div>
        )}
        <GapList gaps={rp.缺口 || d.缺口 || []} testid="detail-gap-list" />
      </section>
    </div>
  )
}

export default function Releases() {
  const [rows, setRows] = useState([])
  const [apps, setApps] = useState([])
  const [appId, setAppId] = useState('')
  const [ver, setVer] = useState('')
  const [digest, setDigest] = useState('')
  const [err, setErr] = useState('')
  const [auditFor, setAuditFor] = useState(null)
  const [auditRows, setAuditRows] = useState([])
  const [detailFor, setDetailFor] = useState(null)
  const [detail, setDetail] = useState(null)
  const [replay, setReplay] = useState(null)
  const [receipt, setReceipt] = useState(null)

  const loadAudit = useCallback(async (id) => {
    if (id == null) { setAuditRows([]); return }
    const data = await api('/审计' + `?关联编号=${id}`)
    setAuditRows(data)
  }, [])

  const load = useCallback(async () => {
    const list = await api('/发版单')
    setRows(list)
    const a = await api('/应用')
    setApps(a)
    setAppId((cur) => cur || (a[0] ? String(a[0].编号) : ''))
    if (auditFor != null) {
      try { await loadAudit(auditFor) } catch { /* 审计刷新失败不阻断主表 */ }
    }
    if (detailFor != null) {
      try {
        const [d, rp] = await Promise.all([
          api(`/发版单/${detailFor}`),
          api(`/事件重放/${detailFor}`),
        ])
        setDetail(d); setReplay(rp)
      } catch (e) { setErr(e.message) }
    }
  }, [auditFor, detailFor, loadAudit])

  useEffect(() => { load().catch((e) => setErr(e.message)) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const create = async () => {
    setErr('')
    try {
      await api('/发版单', {
        method: 'POST',
        body: JSON.stringify({ 应用编号: Number(appId), 目标版本: ver, 制品摘要: digest }),
      })
      setVer(''); setDigest(''); load()
    } catch (e) { setErr(e.message) }
  }

  const pipeline = async (id, status) => {
    setErr('')
    try {
      await api(`/发版单/${id}/流水线`, { method: 'POST', body: JSON.stringify({ 流水线: status }) })
      await load()
    } catch (e) { setErr(e.message) }
  }

  const refreshStamp = async (id, status) => {
    setErr('')
    try {
      await api(`/发版单/${id}/过线戳刷新`, { method: 'POST', body: JSON.stringify({ 流水线: status }) })
      await load()
    } catch (e) { setErr(e.message) }
  }

  const makeHalfFixture = async (id) => {
    setErr('')
    try {
      await api(`/发版单/${id}/过线戳刷新`, {
        method: 'POST',
        body: JSON.stringify({ 流水线: '绿', 半截: true, 戳状态: '刷新半截' }),
      })
      await load()
    } catch (e) { setErr(e.message) }
  }

  const admit = async (id) => {
    setErr('')
    try {
      const out = await api(`/发版单/${id}/入闸`, { method: 'POST', body: '{}' })
      setReceipt(out)
      setAuditFor(id)
      await loadAudit(id)
      await load()
    } catch (e) { setErr(e.message) }
  }

  const reclaim = async (id) => {
    setErr('')
    try {
      await api(`/发版单/${id}/收回挂接`, { method: 'POST', body: '{}' })
      await load()
      if (auditFor === id) await loadAudit(id)
    } catch (e) { setErr(e.message) }
  }

  const toggleAudit = async (id) => {
    if (auditFor === id) { setAuditFor(null); setAuditRows([]) }
    else { setAuditFor(id); await loadAudit(id) }
  }

  const openDetail = async (id) => {
    setDetailFor(id); setDetail(null); setReplay(null)
    try {
      const [d, rp] = await Promise.all([api(`/发版单/${id}`), api(`/事件重放/${id}`)])
      setDetail(d); setReplay(rp)
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>发版单 · 过线戳 / 入闸事件 / 水位同命运</h2>
      {err && <p className="err" role="alert">{err}</p>}
      {receipt && (
        <div className="ok-banner" data-testid="admit-receipt" role="status"
          data-ok={receipt.同命运 === false ? 'false' : 'true'}
          data-release-id={receipt.编号}>
          入闸回执：成功单号
          <b data-testid="admit-success-no">{receipt.发版单号}</b>
          ｜过线戳#{receipt.入闸回执?.过线戳编号 ?? receipt.过线戳旁读?.编号 ?? '-'}
          ｜入闸事件#{receipt.入闸回执?.入闸事件编号 ?? receipt.入闸事件?.编号 ?? '-'}
          ｜水位令牌=1
          ｜<span data-testid="co-destiny" data-ok={receipt.同命运 === false ? 'false' : 'true'}>
            {receipt.同命运 === false ? '证据有缺口' : '证据同命运'}
          </span>
        </div>
      )}
      <div className="row">
        <label>应用
          <select value={appId} onChange={(e) => setAppId(e.target.value)} data-testid="release-app-select">
            {apps.map((a) => <option key={a.编号} value={a.编号}>{a.名称}</option>)}
          </select>
        </label>
        <label>目标版本<input value={ver} onChange={(e) => setVer(e.target.value)} placeholder="1.4.0" data-testid="release-version-input" /></label>
        <label>制品摘要<input value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="sha256:..." data-testid="release-digest-input" /></label>
        <button type="button" onClick={create} data-testid="create-release-button">申请入闸</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <p className="muted">
        当前贯彻架构臂：<b>红单可告警入闸</b>——普通红流水线可入闸，但永久标记「红入闸告警」；致命过线戳/事件缺/水位假加会钉缺口并揭穿假成功单号。
      </p>
      <div className="table-wrap">
        <table data-testid="release-table">
          <thead>
            <tr>
              <th>发版单号</th><th>应用</th><th>版本</th><th>状态</th>
              <th>流水线区</th><th>入闸裁决 / 告警</th><th>过线戳旁读</th><th>事件/水位同屏对读</th><th>制品挂接行</th>
              <th>环境</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const detailDecision = r.入闸判定明细
              const planned = detailDecision && detailDecision.拟定入闸结论
              const gateHint = r.状态 === '待入闸'
                ? (r.可入闸
                    ? (planned === '红入闸' ? '可入闸：将形成红入闸告警' : '可入闸：绿入闸')
                    : (detailDecision ? detailDecision.原因.join('；') : r.入闸判定))
                : (r.晋升不可用原因 || r.入闸判定 || '')
              const dirtyLink = r.挂接行 && r.挂接行.状态 === '挂接中'
              return (
                <tr
                  key={r.编号}
                  data-release-id={r.编号}
                  data-testid="release-row"
                  className={r.红入闸告警 ? 'row-red' : ((r.入闸后翻红 || r.有缺口) ? 'row-warn' : '')}
                >
                  <td>
                    <button type="button" className="linklike" data-testid="release-serial" onClick={() => openDetail(r.编号)}>
                      {r.发版单号}
                    </button>
                    {r.有缺口 && <div className="gap-badge" data-testid="row-gap-badge">缺口 {r.证据缺口数}</div>}
                  </td>
                  <td>{r.应用}</td>
                  <td>{r.目标版本}</td>
                  <td><span className="status-tag">{r.状态}</span></td>
                  <td><PipelineBadge value={r.流水线} /></td>
                  <td>
                    <VerdictBadge row={r} />{' '}
                    <AlarmTag row={r} />
                    <div className="hint" title={gateHint}>{gateHint}</div>
                  </td>
                  <td><StampCell row={r} onRefresh={refreshStamp} onHalfFixture={makeHalfFixture} /></td>
                  <td>
                    <div data-testid="event-side-read">
                      {r.入闸事件
                        ? <span className="badge badge-green" data-testid="admit-event">#{r.入闸事件.编号} {r.入闸事件.事件状态}</span>
                        : <span className="err" data-testid="admit-event-missing">事件缺</span>}
                    </div>
                    <WaterCell row={r} />
                    {(r.缺口 || []).length > 0 && <GapList gaps={r.缺口.slice(0, 2)} testid="row-gap-list" />}
                  </td>
                  <td><LinkCell row={r} /></td>
                  <td>{r.当前环境 || '-'}</td>
                  <td className="actions-cell">
                    <button type="button" className="secondary" data-testid="pipeline-green-button" onClick={() => pipeline(r.编号, '绿')}>流水线绿</button>{' '}
                    <button type="button" className="danger" data-testid="pipeline-red-button" onClick={() => pipeline(r.编号, '红')}>流水线红</button>{' '}
                    <button
                      type="button"
                      className={planned === '红入闸' && r.可入闸 ? 'danger' : ''}
                      onClick={() => admit(r.编号)}
                      disabled={!r.可入闸}
                      title={r.可入闸 ? gateHint : (detailDecision ? detailDecision.原因.join('；') : '当前不可入闸')}
                      data-testid="admit-button"
                    >入闸</button>{' '}
                    <button type="button" className="secondary" data-testid="open-detail-side-read" onClick={() => openDetail(r.编号)}>
                      发版详情旁读
                    </button>{' '}
                    {dirtyLink && (
                      <button type="button" className="warn-btn" data-testid="release-reclaim-button" onClick={() => reclaim(r.编号)}>收回挂接</button>
                    )}{' '}
                    <button type="button" className="secondary" onClick={() => toggleAudit(r.编号)}>
                      {auditFor === r.编号 ? '收起审计' : '看审计'}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <DetailDrawer id={detailFor} detail={detail} replay={replay} onClose={() => setDetailFor(null)} />

      {auditFor != null && (
        <div className="audit-drawer" data-testid="release-audit-drawer">
          <h3>本单审计流水 · 关联 #{auditFor}</h3>
          {auditRows.length === 0 ? <p className="muted">暂无审计事件</p> : (
            <table>
              <thead><tr><th>时间</th><th>动作</th><th>备注</th></tr></thead>
              <tbody>
                {auditRows.map((a) => (
                  <tr key={a.编号} className={/红|驳回|收回|翻红/.test(a.动作 + a.备注) ? 'row-warn' : ''}>
                    <td>{a.时间}</td><td>{a.动作}</td><td className="note-cell">{a.备注}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
