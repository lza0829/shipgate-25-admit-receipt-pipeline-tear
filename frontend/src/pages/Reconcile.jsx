import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Reconcile() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const load = () => api('/对账').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])

  const reclaim = async (releaseId, linkId) => {
    setErr(''); setMsg(''); setBusy(true)
    try {
      const body = linkId ? { 挂接编号: linkId } : {}
      const out = await api(`/发版单/${releaseId}/收回挂接`, { method: 'POST', body: JSON.stringify(body) })
      setMsg(out.整笔重置
        ? `已整笔收回：发版单回到待入闸，收回挂接行 ${JSON.stringify(out.收回挂接行)}`
        : `已收回脏挂接行 ${JSON.stringify(out.收回挂接行)}，有效入闸保留`)
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const correct = async () => {
    setErr(''); setMsg(''); setBusy(true)
    try {
      const out = await api('/对账/纠偏', { method: 'POST', body: JSON.stringify({}) })
      setMsg(`纠偏完成：整笔退回 ${JSON.stringify(out.整笔退回 || [])}，剩余缺口 ${out.缺口总数 ?? 0}`)
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (err && !data) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const s = data.发版统计 || {}
  const c = data.入闸一致性 || {}
  const diff = data.对账差额 || c.对账差额 || {}
  const gaps = data.缺口 || diff.明细 || c.缺口 || []
  const water = data.流水线入闸水位 || diff.水位 || c.流水线入闸水位 || {}
  return (
    <div className="panel">
      <h2>发布对账 · 红入闸不计绿通过</h2>
      {err && <p className="err" role="alert">{err}</p>}
      {msg && <p className="ok-text">{msg}</p>}

      <div className="grid">
        {Object.entries(s).map(([k, v]) => (
          <div className={'stat' + (k.includes('红入闸') && v ? ' stat-red is-alarm' : '')} key={k}>
            <div className="n">{v}</div><div className="l">{k}</div>
          </div>
        ))}
        <div className="stat"><div className="n">{data.晋升事件数}</div><div className="l">晋升事件数</div></div>
      </div>

      <h2>对账差额 · 过线戳/事件/水位重放</h2>
      <div className={diff.对齐 === false ? 'diff-panel diff-bad' : 'diff-panel diff-ok'} data-testid="reconcile-difference">
        <div className="diff-total" data-testid="difference-total">差额总数：{diff.差额总数 ?? gaps.length}</div>
        <div data-testid="difference-water">水位差额：{water.差额 ?? diff.水位差额 ?? 0}（真实 {water.真实水位 ?? '-'} / 账面 {water.账面水位 ?? '-'}）</div>
        <button type="button" className="warn-btn" disabled={busy} onClick={correct} data-testid="reconcile-correct-button">
          显式纠偏 / 致命撕裂整笔退回
        </button>
        {gaps.length > 0 && (
          <div data-testid="gap-list">
            <div data-testid="gap-pins">
              <ul className="gap-list" data-testid="reconcile-gap-list">
                {gaps.map((g, i) => (
                  <li key={`${g.缺口类型}-${i}`} className="gap-item" data-testid="reconcile-gap-pin">
                    <b>{g.缺口类型}</b>（单 #{g.发版单编号 ?? '-'}）：{g.说明 || g.detail}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>

      <h2>入闸 / 挂接一致性</h2>
      {c.一致
        ? <div className="ok-banner">✓ 裁决、挂接行、在途口径一致，无半截脏挂接。架构臂：{c.架构臂}</div>
        : <div className="warn-banner" data-testid="reconcile-inconsistent">
            检测到不一致：证据缺口 {gaps.length}；挂接中脏行 {c.挂接中脏行}；无挂接行在途单 {(c.无挂接行的在途单 || []).length}；
            裁决不一致行 {(c.裁决不一致挂接行 || []).length}。可纠偏或在下方整笔收回。
          </div>}

      {(c.无挂接行的在途单 || []).length > 0 && (
        <div className="table-wrap">
          <h3>无成功挂接行的在途单（半截入闸）</h3>
          <table>
            <thead><tr><th>发版单</th><th>状态</th><th>库内裁决</th><th>处置</th></tr></thead>
            <tbody>
              {c.无挂接行的在途单.map((x) => (
                <tr key={x.id} className="row-warn">
                  <td>{x.serial}（#{x.id}）</td><td>{x.status}</td><td>{x.gate_verdict || '缺裁决'}</td>
                  <td>
                    {x.status === '已入闸' && (
                      <button type="button" className="warn-btn" disabled={busy}
                        data-testid="reconcile-reclaim-button"
                        onClick={() => reclaim(x.id)}>整笔收回为待入闸</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>制品挂接行</h2>
      <div className="table-wrap">
        <table data-testid="reconcile-links-table">
          <thead><tr><th>挂接编号</th><th>发版单</th><th>状态</th><th>结论</th><th>告警</th><th>时间</th><th>处置</th></tr></thead>
          <tbody>
            {(data.挂接行 || []).map((l) => (
              <tr key={l.挂接编号} className={l.挂接状态 === '挂接中' ? 'row-warn' : (l.告警 ? 'row-red' : '')}>
                <td>#{l.挂接编号}</td>
                <td>{l.发版单号}（#{l.发版单编号}）</td>
                <td>{l.挂接状态}</td>
                <td>{l.挂接结论}</td>
                <td>{l.告警 ? '⚠ 是' : '否'}</td>
                <td>{l.时间}</td>
                <td>
                  {l.挂接状态 === '挂接中' && (
                    <button type="button" className="warn-btn" disabled={busy}
                      data-testid="reconcile-link-reclaim-button"
                      onClick={() => reclaim(l.发版单编号, l.挂接编号)}>收回半截行</button>
                  )}
                </td>
              </tr>
            ))}
            {(data.挂接行 || []).length === 0 && (
              <tr><td colSpan={7} className="muted">暂无有效挂接行</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <h2>名册与实例</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>名册版本</th><th>实例版本们</th><th>红灯数</th></tr></thead>
          <tbody>
            {(data.名册与实例 || []).map((r, i) => (
              <tr key={i}><td>{r.应用}</td><td>{r.环境}</td><td>{r.名册版本}</td><td>{r.实例版本们 || '-'}</td><td>{r.红灯数}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
