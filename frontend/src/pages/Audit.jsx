import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Audit() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [kind, setKind] = useState('')
  const [refId, setRefId] = useState('')
  const [kw, setKw] = useState('')

  const buildQuery = () => {
    const p = new URLSearchParams()
    if (kind) p.set('类别', kind)
    if (refId) p.set('关联编号', refId)
    if (kw) p.set('关键字', kw)
    const qs = p.toString()
    return '/审计' + (qs ? `?${qs}` : '')
  }
  const load = () => api(buildQuery()).then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="panel">
      <h2>审计流水</h2>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <label>类别
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">全部</option>
            <option value="发版单">发版单</option>
            <option value="晋升">晋升</option>
            <option value="回滚">回滚</option>
            <option value="应用">应用</option>
            <option value="设置">设置</option>
            <option value="系统">系统</option>
          </select>
        </label>
        <label>关联编号<input value={refId} onChange={(e) => setRefId(e.target.value)} placeholder="发版单编号" /></label>
        <label>关键字<input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="红入闸 / 驳回 / 收回" /></label>
        <button type="button" onClick={() => load().catch((e) => setErr(e.message))}>查询</button>
        <button type="button" className="secondary" onClick={() => { setKind(''); setRefId(''); setKw(''); setTimeout(() => api('/审计').then(setRows), 0) }}>清空</button>
      </div>
      <div className="table-wrap">
        <table data-testid="audit-table">
          <thead><tr><th>时间</th><th>类别</th><th>动作</th><th>关联</th><th>备注</th></tr></thead>
          <tbody>
            {rows.map((r) => {
              const hot = /红入闸|驳回|收回|翻红/.test(`${r.动作}${r.备注}`)
              const ok = /绿入闸/.test(r.备注)
              return (
                <tr key={r.编号} className={hot ? 'row-warn' : ok ? 'row-ok' : ''}>
                  <td>{r.时间}</td>
                  <td>{r.类别}</td>
                  <td>{r.动作}</td>
                  <td>{r.关联编号 || '-'}</td>
                  <td className="note-cell" data-testid="audit-note">{r.备注}</td>
                </tr>
              )
            })}
            {rows.length === 0 && (
              <tr><td colSpan={5} className="muted">没有匹配的审计事件</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
