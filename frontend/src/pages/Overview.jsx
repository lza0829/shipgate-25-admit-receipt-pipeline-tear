import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const d = data.分布 || {}
  const redCount = data.红入闸告警数 || 0
  const dirty = data.挂接脏行数 || 0
  const consistency = data.入闸一致性 || {}
  const water = data.流水线入闸水位 || data.水位 || {}
  const gaps = data.缺口 || consistency.缺口 || []
  return (
    <div className="panel">
      <h2>总览</h2>

      {redCount > 0 && (
        <div className="alarm-banner" data-testid="overview-red-gate-banner" role="alert">
          ⚠ 有 {redCount} 张「红入闸」发版单在途：已告警登记，不得按绿通过晋升。
        </div>
      )}
      {dirty > 0 && (
        <div className="warn-banner" data-testid="overview-dirty-link-banner">
          ⚑ 检测到 {dirty} 行半截「挂接中」脏挂接，请前往对账页整笔收回。
        </div>
      )}
      {gaps.length > 0 && (
        <div className="alarm-banner" data-testid="overview-gap-banner" role="alert">
          ⛓ 检测到 {gaps.length} 个过线戳/入闸事件/水位证据缺口；缺口未清，水位不得亮已入闸。
        </div>
      )}
      {water.可信 === false && (
        <div className="alarm-banner" data-testid="overview-water-difference">
          流水线入闸水位差额 {water.差额}：真实 {water.真实水位}，账面 {water.账面水位}。
        </div>
      )}
      {consistency.一致 && gaps.length === 0 && (
        <div className="ok-banner" data-testid="overview-co-destiny" data-ok="true">
          ✓ 过线戳、入闸事件、水位、挂接行、审计
          <span data-testid="co-destiny" data-ok="true">同命运</span>
          且可重放。
        </div>
      )}

      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
        <div className={'stat stat-red' + (redCount ? ' is-alarm' : '')} data-testid="overview-red-gate-cell">
          <div className="n">{redCount}</div><div className="l">红入闸告警在途</div>
        </div>
        <div className="stat stat-green">
          <div className="n">{data.绿入闸在途数 || 0}</div><div className="l">绿入闸在途</div>
        </div>
        <div className={'stat stat-warn' + (dirty ? ' is-alarm' : '')}>
          <div className="n">{dirty}</div><div className="l">挂接脏行</div>
        </div>
        <div className={'stat ' + (water.可信 === false ? 'stat-red is-alarm' : 'stat-green')} data-testid="overview-water-level">
          <div className="n" data-testid="water-level" data-lit={water.可信 === false ? 'false' : 'true'}>{water.真实水位 ?? 0}</div>
          <div className="l">流水线入闸真实水位（账面 {water.账面水位 ?? 0} / 差 {water.差额 ?? 0}）</div>
        </div>
        <div className={'stat ' + (gaps.length ? 'stat-red is-alarm' : 'stat-green')} data-testid="overview-gap-count">
          <div className="n">{gaps.length}</div><div className="l">证据缺口 / 对账差额</div>
        </div>
      </div>

      <h2>在途发版（裁决 / 挂接 / 流水线同一口径）</h2>
      <div className="table-wrap">
        <table data-testid="overview-transit-table">
          <thead>
            <tr>
              <th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th>
              <th>入闸裁决</th><th>告警</th><th>流水线</th><th>过线戳与在途入闸格</th><th>挂接行</th><th>当前环境</th>
            </tr>
          </thead>
          <tbody>
            {(data.在途 || []).map((r) => (
              <tr
                key={r.编号}
                data-release-id={r.编号}
                data-testid="overview-transit-row"
                className={r.红入闸告警 ? 'row-red' : (r.入闸后翻红 ? 'row-warn' : '')}
              >
                <td>{r.发版单号}</td>
                <td>{r.应用}</td>
                <td>{r.目标版本}</td>
                <td>{r.状态}</td>
                <td>
                  {r.入闸结论 === '红入闸'
                    ? <span className="badge badge-red">红入闸</span>
                    : r.入闸结论 === '绿入闸'
                      ? <span className="badge badge-green">绿入闸</span>
                      : <span className="badge badge-gray">缺裁决</span>}
                </td>
                <td>
                  {r.红入闸告警 && <span className="alarm-tag">⚠ 红入闸告警</span>}
                  {!r.红入闸告警 && r.入闸后翻红 && <span className="warn-tag">⚑ 入闸后翻红</span>}
                  {!r.告警标 && <span className="muted">-</span>}
                </td>
                <td>
                  <span className={r.流水线 === '绿' ? 'pipe pipe-green' : r.流水线 === '红' ? 'pipe pipe-red' : 'pipe pipe-gray'}>
                    <i className="dot" />{r.流水线}
                  </span>
                </td>
                <td
                  data-testid="transit-admit-cell"
                  data-release-id={r.编号}
                  data-lit={r.在途入闸格?.水位亮灯 ? 'true' : 'false'}
                  className={r.有缺口 ? 'transit-cell-gap' : 'transit-cell-ok'}
                >
                  <div data-testid="overview-stamp-side-read" data-release-id={r.编号}>
                    戳：{r.过线戳旁读 ? `#${r.过线戳旁读.编号} ${r.过线戳旁读.过线戳状态}` : '缺'}
                  </div>
                  <div
                    data-testid="overview-water-light"
                    data-lit={r.在途入闸格?.水位亮灯 ? 'true' : 'false'}
                  >
                    水位：{r.在途入闸格?.水位亮灯 ? '亮已入闸' : '缺口未清不亮'}
                  </div>
                  {r.有缺口 && <div className="err" data-testid="transit-gap-count">缺口 {r.证据缺口数}</div>}
                </td>
                <td>
                  {r.挂接行
                    ? <span className={r.挂接行.告警 ? 'link link-red compact' : 'link link-green compact'}>
                        #{r.挂接行.编号} {r.挂接行.状态}/{r.挂接行.结论}
                      </span>
                    : <span className="err">无挂接行</span>}
                </td>
                <td>{r.当前环境 || '-'}</td>
              </tr>
            ))}
            {(data.在途 || []).length === 0 && (
              <tr><td colSpan={10} className="muted">暂无在途发版</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <h2>各环境版本</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>版本</th><th>更新时间</th></tr></thead>
          <tbody>
            {(data.环境版本 || []).map((v, i) => (
              <tr key={i}><td>{v.应用}</td><td>{v.环境}</td><td>{v.版本}</td><td>{v.更新时间}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
