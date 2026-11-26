import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../api'

export default function Events() {
  const [events, setEvents] = useState([])
  const [gaps, setGaps] = useState([])
  const [rid, setRid] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (releaseId = '') => {
    setLoading(true); setErr('')
    try {
      const qs = releaseId ? `?发版单编号=${encodeURIComponent(releaseId)}` : ''
      const [evs, gapData] = await Promise.all([
        api(`/入闸事件册${qs}`),
        api(releaseId ? `/缺口?发版单编号=${encodeURIComponent(releaseId)}` : '/缺口'),
      ])
      setEvents(evs.入闸事件册 || evs.events || [])
      setGaps(gapData.缺口 || gapData.gaps || [])
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load().catch((e) => setErr(e.message)) }, [load])

  return (
    <div className="panel">
      <h2>入闸事件册 · 事件重放</h2>
      <p className="muted">事件册只呈现持久化入闸成立/撤销事实；缺口由过线戳、事件、水位令牌、挂接行和审计重放得出。</p>
      <div className="row">
        <label>发版单编号
          <input
            value={rid}
            onChange={(e) => setRid(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') load(rid) }}
            placeholder="留空查全部"
            data-testid="event-filter-release-id"
          />
        </label>
        <button type="button" onClick={() => load(rid)} data-testid="event-filter-button">过滤事件</button>
        <button type="button" className="secondary" onClick={() => { setRid(''); load('') }}>全部</button>
      </div>
      {err && <p className="err" role="alert">{err}</p>}
      {loading ? <p>加载中</p> : (
        <>
          <h3>缺口钉</h3>
          {gaps.length === 0 ? <div className="ok-banner" data-testid="event-gap-clean">无缺口，事件链可重放</div> : (
            <div data-testid="gap-list">
              <div data-testid="gap-pins">
                <ul className="gap-list" data-testid="event-gap-list">
                  {gaps.map((g, i) => (
                    <li key={`${g.缺口类型}-${i}`} className="gap-item" data-testid="event-gap-pin">
                      <b>{g.缺口类型}</b>
                      <span> 单号 #{g.发版单编号 ?? '-'}：{g.说明}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}

          <div className="table-wrap" data-testid="admit-event-book">
            <table data-testid="event-book-table">
              <thead>
                <tr><th>事件#</th><th>发版单</th><th>过线戳</th><th>类型</th><th>状态</th><th>裁决</th><th>水位增量</th><th>幂等键</th><th>时间</th><th>备注</th></tr>
              </thead>
              <tbody>
                {events.map((e) => (
                  <tr
                    key={e.编号}
                    data-release-id={e.发版单编号 ?? e.release_id ?? ''}
                    data-event-id={e.编号 ?? e.id ?? ''}
                    className={e.事件状态 === '有效' ? '' : 'row-warn'}
                    data-testid="event-book-row"
                  >
                    <td>{e.编号}</td>
                    <td>{e.发版单编号}</td>
                    <td data-testid="event-stamp-link">{e.过线戳编号 || '-'}</td>
                    <td>{e.事件类型}</td>
                    <td data-testid="event-state">{e.事件状态}</td>
                    <td>{e.裁决 || '-'}</td>
                    <td>{e.水位增量}</td>
                    <td className="digest">{e.幂等键 || e.idempotency_key || '-'}</td>
                    <td>{e.时间}</td>
                    <td className="note-cell">{e.备注}</td>
                  </tr>
                ))}
                {events.length === 0 && <tr><td colSpan={10} className="muted">事件册无记录</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
