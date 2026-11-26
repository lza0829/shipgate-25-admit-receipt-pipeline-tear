import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Promote() {
  const [rows, setRows] = useState([])
  const [rid, setRid] = useState('')
  const [env, setEnv] = useState('预')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = async () => {
    const all = await api('/发版单')
    // 已入闸/晋升中都列出，但只有后端读侧判定「晋升可用」的单子可操作
    const active = all.filter((r) => ['已入闸', '晋升中'].includes(r.状态))
    setRows(active)
    setRid((cur) => {
      if (cur && active.some((r) => String(r.编号) === cur)) return cur
      const ok = active.find((r) => r.晋升可用)
      return ok ? String(ok.编号) : (active[0] ? String(active[0].编号) : '')
    })
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])

  const current = rows.find((r) => String(r.编号) === rid)
  const canGo = !!current && current.晋升可用

  const go = async () => {
    setErr(''); setMsg('')
    try {
      const out = await api(`/发版单/${rid}/晋升`, { method: 'POST', body: JSON.stringify({ 目标环境: env }) })
      setMsg(`已晋升：${out.发版单号} → ${env}（当前状态 ${out.状态}）`)
      await load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>环境晋升 · 红入闸不得按绿通过</h2>
      {err && <p className="err" role="alert">{err}</p>}
      {msg && <p className="ok-text">{msg}</p>}
      <div className="row">
        <label>发版单
          <select value={rid} onChange={(e) => setRid(e.target.value)}>
            {rows.map((r) => (
              <option key={r.编号} value={r.编号} disabled={!r.晋升可用}>
                {r.发版单号} · {r.应用} · {r.当前环境 || '未落地'}
                {r.入闸结论 === '红入闸' ? ' · 红入闸告警（禁晋升）' : ''}
                {r.入闸后翻红 ? ' · 入闸后翻红（冻结）' : ''}
                {!r.入闸结论 ? ' · 缺裁决（脏单）' : ''}
              </option>
            ))}
          </select>
        </label>
        <label>目标环境
          <select value={env} onChange={(e) => setEnv(e.target.value)}>
            <option value="测">测</option>
            <option value="预">预</option>
            <option value="产">产</option>
          </select>
        </label>
        <button type="button" onClick={go} disabled={!canGo} data-testid="promote-button">晋升</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      {current && !current.晋升可用 && (
        <p className="err" data-testid="promote-block-reason">晋升入口不可用：{current.晋升不可用原因}</p>
      )}
      <div className="table-wrap">
        <table data-testid="promote-table">
          <thead>
            <tr>
              <th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th>
              <th>入闸裁决</th><th>流水线</th><th>当前环境</th><th>晋升可用性</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.红入闸告警 ? 'row-red' : r.入闸后翻红 ? 'row-warn' : ''}>
                <td>{r.发版单号}</td>
                <td>{r.应用}</td>
                <td>{r.目标版本}</td>
                <td>{r.状态}</td>
                <td>
                  {r.入闸结论 === '红入闸'
                    ? <span className="badge badge-red">红入闸告警</span>
                    : r.入闸结论 === '绿入闸'
                      ? <span className="badge badge-green">绿入闸</span>
                      : <span className="badge badge-gray">缺裁决</span>}
                </td>
                <td>{r.流水线}</td>
                <td>{r.当前环境 || '-'}</td>
                <td>
                  {r.晋升可用
                    ? <span className="ok-text">可晋升</span>
                    : <span className="err" title={r.晋升不可用原因}>不可用：{r.晋升不可用原因}</span>}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={8} className="muted">暂无已入闸/晋升中的发版单</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
