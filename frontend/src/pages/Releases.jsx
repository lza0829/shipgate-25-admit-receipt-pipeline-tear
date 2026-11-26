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

const surface_panel = true
export default function Releases() {
  const [rows, setRows] = useState([])
  const [apps, setApps] = useState([])
  const [appId, setAppId] = useState('')
  const [ver, setVer] = useState('')
  const [digest, setDigest] = useState('')
  const [err, setErr] = useState('')
  const [auditFor, setAuditFor] = useState(null)
  const [auditRows, setAuditRows] = useState([])

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
  }, [auditFor, loadAudit])

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

  const admit = async (id) => {
    setErr('')
    try {
      await api(`/发版单/${id}/入闸`, { method: 'POST', body: '{}' })
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

  return (
    <div className="panel">
      <h2>发版单 · 流水线与入闸同一裁决</h2>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="row">
        <label>应用
          <select value={appId} onChange={(e) => setAppId(e.target.value)}>
            {apps.map((a) => <option key={a.编号} value={a.编号}>{a.名称}</option>)}
          </select>
        </label>
        <label>目标版本<input value={ver} onChange={(e) => setVer(e.target.value)} placeholder="1.4.0" /></label>
        <label>制品摘要<input value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="sha256:..." /></label>
        <button type="button" onClick={create}>申请入闸</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <p className="muted">
        当前贯彻架构臂：<b>红单可告警入闸</b>——红流水线可入闸，但永久标记「红入闸告警」、挂接行按红登记、晋升硬不可用；绿入闸为对照。
      </p>
      <div className="table-wrap">
        <table data-testid="release-table">
          <thead>
            <tr>
              <th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th>
              <th>流水线区</th><th>入闸裁决 / 告警</th><th>制品挂接行</th>
              <th>当前环境</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const detail = r.入闸判定明细
              const planned = detail && detail.拟定入闸结论
              const gateHint = r.状态 === '待入闸'
                ? (r.可入闸
                    ? (planned === '红入闸' ? '可入闸：将形成红入闸告警' : '可入闸：绿入闸')
                    : (detail ? detail.原因.join('；') : r.入闸判定))
                : (r.晋升不可用原因 || r.入闸判定 || '')
              const dirtyLink = r.挂接行 && r.挂接行.状态 === '挂接中'
              return (
                <tr key={r.编号} className={r.红入闸告警 ? 'row-red' : (r.入闸后翻红 ? 'row-warn' : '')}>
                  <td>{r.发版单号}</td>
                  <td>{r.应用}</td>
                  <td>{r.目标版本}</td>
                  <td><span className="status-tag">{r.状态}</span></td>
                  <td><PipelineBadge value={r.流水线} /></td>
                  <td>
                    <VerdictBadge row={r} />{' '}
                    <AlarmTag row={r} />
                    <div className="hint" title={gateHint}>{gateHint}</div>
                  </td>
                  <td><LinkCell row={r} /></td>
                  <td>{r.当前环境 || '-'}</td>
                  <td className="actions-cell">
                    <button type="button" className="secondary" onClick={() => pipeline(r.编号, '绿')}>流水线绿</button>{' '}
                    <button type="button" className="danger" onClick={() => pipeline(r.编号, '红')}>流水线红</button>{' '}
                    <button
                      type="button"
                      className={planned === '红入闸' && r.可入闸 ? 'danger' : ''}
                      onClick={() => admit(r.编号)}
                      disabled={!r.可入闸}
                      title={r.可入闸 ? gateHint : (detail ? detail.原因.join('；') : '当前不可入闸')}
                      data-testid="admit-button"
                    >入闸</button>{' '}
                    {dirtyLink && (
                      <button type="button" className="warn-btn" onClick={() => reclaim(r.编号)}>收回挂接</button>
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
