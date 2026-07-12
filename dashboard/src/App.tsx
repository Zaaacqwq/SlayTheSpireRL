import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Activity, AlertCircle, BarChart3, BookOpen, BrainCircuit, ChevronLeft, ChevronRight, CircleDot, Coins, Crown, Database, Footprints, Heart, Layers3, RefreshCw, Route, Search, Shield, Skull, Sparkles, Swords, Trophy, Zap } from 'lucide-react'
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Episode, MetricRow, ReplayStep, Run } from './types'

const API = async <T,>(path: string): Promise<T> => {
  const response = await fetch(path)
  if (!response.ok) throw new Error((await response.json()).error || response.statusText)
  return response.json()
}
const num = (value: unknown, digits = 2) => typeof value === 'number' ? value.toFixed(digits).replace(/\.00$/, '') : '—'
const pct = (value: unknown) => typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'
const won = (value: unknown) => value === true || value === 'true'
const outcomeLabel = (value: unknown) => value === null || value === undefined ? 'Unknown' : won(value) ? 'Victory' : 'Defeat'
const assetMisses = new Set<string>()

const actionLabel = (step: ReplayStep) => {
  const action = step.action?.action || 'unknown'
  const picked = step.selected?.name || step.selected?.title
  const target = step.action?.args?.target_index
  return `${action.replaceAll('_', ' ')}${picked ? ` · ${picked}` : ''}${target !== undefined && target !== null ? ` → target ${target}` : ''}`
}

function Stat({ icon, label, value, detail, unavailable = false }: { icon: ReactNode; label: string; value: string; detail?: string; unavailable?: boolean }) {
  return <div className={`stat ${unavailable ? 'stat-unavailable' : ''}`}><div className="stat-icon">{icon}</div><div><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div></div>
}

function App() {
  const [runs, setRuns] = useState<Run[]>([])
  const [runName, setRunName] = useState('')
  const [tab, setTab] = useState<'overview' | 'episodes' | 'replay'>('overview')
  const [error, setError] = useState('')
  const [updated, setUpdated] = useState<Date | null>(null)

  const refresh = async () => {
    try {
      const payload = await API<{items: Run[]; legacy_episode_count: number}>('/api/runs')
      const next = [...payload.items]
      if (payload.legacy_episode_count) next.push({ name: 'legacy', config: {}, history_count: 0, episode_count: payload.legacy_episode_count, checkpoints: 0, latest: {iteration: 0}, best: {iteration: 0}, stats: {wins: 0, finished: 0, win_rate: null, avg_reward: null, total_reward: null, avg_floor: null, errors: 0, truncated: 0}, availability: {metrics: false, episodes: true} })
      setRuns(next)
      setRunName(current => current && next.some(run => run.name === current) ? current : next[0]?.name || '')
      setUpdated(new Date()); setError('')
    } catch (err) { setError(String(err)) }
  }
  useEffect(() => { void refresh(); const timer = window.setInterval(() => { if (!document.hidden) void refresh() }, 5000); return () => clearInterval(timer) }, [])
  const run = runs.find(item => item.name === runName)
  const trainingRuns = runs.filter(item => item.name !== 'legacy')
  const legacy = runs.find(item => item.name === 'legacy')

  return <div className="app-shell">
    <aside className="rail">
      <div className="brand"><div className="brand-mark"><Crown /></div><div><b>SPIRE</b><span>OBSERVATORY</span></div></div>
      <nav>
        <button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}><BarChart3 />Overview <i>训练总览</i></button>
        <button className={tab === 'episodes' ? 'active' : ''} onClick={() => setTab('episodes')}><BookOpen />Episodes <i>对局记录</i></button>
        <button className={tab === 'replay' ? 'active' : ''} onClick={() => setTab('replay')}><Route />Replay <i>完整复盘</i></button>
      </nav>
      <div className="run-picker"><label>DATA SOURCE / 数据来源</label><select value={runName} onChange={e => setRunName(e.target.value)}><optgroup label="Training runs / 训练实验">{trainingRuns.map(item => <option key={item.name}>{item.name}</option>)}</optgroup>{legacy && <optgroup label="Archived trajectories / 历史轨迹"><option value="legacy">legacy · {legacy.episode_count} episodes</option></optgroup>}</select></div>
      <div className="rail-status"><span className="live-dot" /> Live · 5s polling<br/><small>{updated ? `Synced ${updated.toLocaleTimeString()}` : 'Connecting…'}</small></div>
    </aside>
    <main>
      <header><div><p>REINFORCEMENT LEARNING / 强化学习</p><h1>{tab === 'overview' ? 'Training Overview · 训练总览' : tab === 'episodes' ? 'Episode Archive · 对局档案' : 'Run Replay · 对局复盘'}</h1></div><button className="refresh" onClick={() => void refresh()}><RefreshCw /> Refresh</button></header>
      {error && <div className="error">{error}</div>}
      {!run ? <Empty text="No training runs found / 未发现训练记录"/> : tab === 'overview' ? <Overview run={run}/> : tab === 'episodes' ? <Episodes run={run} onSelectRun={setRunName} onReplay={() => setTab('replay')}/> : <Replay run={run}/>} 
    </main>
  </div>
}

function Overview({ run }: { run: Run }) {
  const [metrics, setMetrics] = useState<MetricRow[]>([])
  useEffect(() => { if (run.name !== 'legacy') void API<{rows: MetricRow[]}>(`/api/runs/${encodeURIComponent(run.name)}/metrics`).then(data => setMetrics(data.rows)); else setMetrics([]) }, [run.name, run.history_count])
  const stageChanges = metrics.filter((row, index) => index === 0 || row.stage !== metrics[index - 1].stage)
  const hasEpisodes = run.episode_count > 0 && run.name !== 'legacy'
  const seenStages = new Set<string>()
  return <>
    {!hasEpisodes && run.name !== 'legacy' && <CoverageNotice/>}
    <section className="hero-stats">
      <Stat icon={<Trophy/>} label="EPISODE WIN RATE / 对局胜率" value={hasEpisodes ? pct(run.stats.win_rate) : '—'} detail={hasEpisodes ? `${run.stats.wins} / ${run.stats.finished} finished` : 'Not recorded for this legacy run'} unavailable={!hasEpisodes}/>
      <Stat icon={<Sparkles/>} label="AVG REWARD / 平均奖励" value={hasEpisodes ? num(run.stats.avg_reward) : '—'} detail={hasEpisodes ? `Total ${num(run.stats.total_reward)}` : 'Available for newly recorded episodes'} unavailable={!hasEpisodes}/>
      <Stat icon={<Footprints/>} label="AVG FLOOR / 平均楼层" value={num(run.stats.avg_floor)} detail={`Latest stage · ${String(run.latest?.stage || '—')}`}/>
      <Stat icon={<Database/>} label="RECORDED EPISODES / 已记录对局" value={run.episode_count.toLocaleString()} detail={`${run.checkpoints} checkpoints`}/>
    </section>
    <section className="chart-grid">
      <SuccessChart rows={metrics} stageChanges={stageChanges}/>
      <Chart title="Optimization / 优化指标" rows={metrics} lines={[['loss','#d96c68'],['policy_loss','#e3b15a'],['value_loss','#7ca6d8'],['entropy','#77b7a4']]} stageChanges={stageChanges}/>
    </section>
    <section className="lower-grid">
      <div className="panel"><PanelTitle icon={<Layers3/>} title="Stage segments / 阶段区间"/><div className="stage-list">{stageChanges.length ? stageChanges.map((row, i) => { const stage = String(row.stage || 'unknown'); const resumed = seenStages.has(stage); seenStages.add(stage); return <div key={`${stage}-${row.iteration}`}><span>{i + 1}</span><b>{stage}</b><small>{resumed ? 'RESUMED · ' : ''}iteration {row.iteration}</small></div> }) : <Empty text="No stage data / 暂无阶段数据"/>}</div></div>
      <ConfigPanel config={run.config}/>
    </section>
  </>
}

function CoverageNotice() { return <div className="coverage-notice"><AlertCircle/><div><b>Partial historical data / 历史数据不完整</b><span>This run predates full episode recording. Training curves remain valid, but per-episode win rate and reward are unavailable.</span></div></div> }

function SuccessChart({ rows, stageChanges }: { rows: MetricRow[]; stageChanges: MetricRow[] }) {
  return <div className="panel chart-panel"><PanelTitle icon={<Activity/>} title="Success & Progress / 胜率与进度"/>{rows.length ? <ResponsiveContainer width="100%" height={300}><LineChart data={rows} margin={{top:12,right:8,bottom:5,left:0}}><CartesianGrid stroke="#332c3b" strokeDasharray="3 4"/><XAxis dataKey="iteration" stroke="#9c92a1"/><YAxis yAxisId="floor" stroke="#b487d5" width={34}/><YAxis yAxisId="rate" orientation="right" domain={[0,1]} tickFormatter={value => `${Math.round(value*100)}%`} stroke="#e3b15a" width={44}/><Tooltip formatter={(value, name) => String(name).includes('win_rate') ? pct(value) : num(value)} contentStyle={{background:'#17131d',border:'1px solid #493e50',borderRadius:8}}/><Legend/>{stageChanges.slice(1).map((row,i)=><ReferenceLine yAxisId="floor" key={i} x={row.iteration} stroke="#765f3e" strokeDasharray="4 4"/>)}<Line yAxisId="floor" type="monotone" dataKey="avg_floor" name="Avg floor" stroke="#b487d5" dot={false} strokeWidth={2}/><Line yAxisId="rate" type="monotone" dataKey="train_win_rate" name="Train win rate" stroke="#e3b15a" dot={false} strokeWidth={2}/><Line yAxisId="rate" type="monotone" dataKey="dev_win_rate" name="Dev win rate" stroke="#77b7a4" dot={false} strokeWidth={2}/></LineChart></ResponsiveContainer> : <Empty text="No metrics available / 暂无指标"/>}</div>
}

function Chart({ title, rows, lines, stageChanges }: {title:string; rows:MetricRow[]; lines:[string,string][]; stageChanges:MetricRow[]}) {
  return <div className="panel chart-panel"><PanelTitle icon={<Activity/>} title={title}/>{rows.length ? <ResponsiveContainer width="100%" height={300}><LineChart data={rows} margin={{top:12,right:18,bottom:5,left:-10}}><CartesianGrid stroke="#332c3b" strokeDasharray="3 4"/><XAxis dataKey="iteration" stroke="#9c92a1"/><YAxis stroke="#9c92a1"/><Tooltip contentStyle={{background:'#17131d',border:'1px solid #493e50',borderRadius:8}}/><Legend/>{stageChanges.slice(1).map((row,i)=><ReferenceLine key={i} x={row.iteration} stroke="#765f3e" strokeDasharray="4 4"/>)}{lines.map(([key,color])=><Line key={key} type="monotone" dataKey={key} stroke={color} dot={false} strokeWidth={2}/>)}</LineChart></ResponsiveContainer> : <Empty text="No metrics available / 暂无指标"/>}</div>
}

function ConfigPanel({ config }: { config: Record<string, unknown> }) {
  const rows = Object.entries(config).filter(([, value]) => typeof value !== 'object').slice(0, 18)
  return <div className="panel"><PanelTitle icon={<BrainCircuit/>} title="Run Configuration / 实验配置"/><div className="config-grid">{rows.map(([key,value])=><div key={key}><span>{key.replaceAll('_',' ')}</span><b>{String(value ?? '—')}</b></div>)}</div><details><summary>Raw config / 原始配置</summary><pre className="config">{JSON.stringify(config, null, 2)}</pre></details></div>
}

function Episodes({ run, onReplay, onSelectRun }: {run: Run; onReplay:()=>void; onSelectRun:(name:string)=>void}) {
  const [items,setItems]=useState<Episode[]>([]), [total,setTotal]=useState(0), [page,setPage]=useState(1)
  const [search,setSearch]=useState(''), [split,setSplit]=useState(''), [outcome,setOutcome]=useState('')
  const load=()=>API<{items:Episode[];total:number}>(`/api/runs/${encodeURIComponent(run.name)}/episodes?page=${page}&page_size=30&search=${encodeURIComponent(search)}&split=${split}&outcome=${outcome}`).then(data=>{setItems(data.items);setTotal(data.total)})
  useEffect(()=>{ void load() },[run.name,run.episode_count,page,search,split,outcome])
  const historicalEmpty = total === 0 && run.name !== 'legacy' && !run.availability.episodes
  return <div className="panel episodes-panel"><div className="episode-toolbar"><div className="search"><Search/><input value={search} onChange={e=>{setPage(1);setSearch(e.target.value)}} placeholder="Seed or episode / 搜索对局"/></div><select value={split} onChange={e=>setSplit(e.target.value)}><option value="">All splits / 全部</option><option value="train">Train</option><option value="dev">Dev</option><option value="legacy">Legacy</option></select><select value={outcome} onChange={e=>setOutcome(e.target.value)}><option value="">All outcomes / 全部结果</option><option value="win">Victory / 胜利</option><option value="loss">Defeat / 失败</option></select><span>{total.toLocaleString()} episodes</span></div>{historicalEmpty ? <div className="archive-empty"><BookOpen/><h2>No full episodes were recorded for this run</h2><p>该实验早于完整轨迹记录功能，训练指标仍然可用。</p><button onClick={()=>onSelectRun('legacy')}>Browse archived trajectories / 查看历史轨迹</button></div> : <EpisodeTable items={items} onOpen={episode=>{sessionStorage.setItem('replayEpisode',episode.episode_id);onReplay()}}/>}<div className="pager"><button disabled={page===1} onClick={()=>setPage(page-1)}><ChevronLeft/></button><span>Page {page} / {Math.max(1,Math.ceil(total/30))}</span><button disabled={page*30>=total} onClick={()=>setPage(page+1)}><ChevronRight/></button></div></div>
}

function EpisodeTable({items,onOpen}:{items:Episode[];onOpen:(e:Episode)=>void}) { return <div className="episode-list"><div className="episode-head"><span>RESULT</span><span>EPISODE / SEED</span><span>CHARACTER</span><span>STAGE</span><span>FLOOR</span><span>REWARD</span><span>STEPS</span></div>{items.map(ep=>{const known=ep.outcome!==null&&ep.outcome!==undefined;return <button className="episode-row" key={`${ep.path}-${ep.episode_id}`} onClick={()=>onOpen(ep)}><span className={`result ${!known?'unknown':won(ep.outcome)?'win':'loss'}`}>{!known?<CircleDot/>:won(ep.outcome)?<Trophy/>:<Skull/>}{outcomeLabel(ep.outcome)}</span><span><b>{ep.episode_id}</b><small>{ep.split} · iteration {ep.iteration ?? '—'}</small></span><span>{ep.character || '—'}</span><span className="stage-chip">{ep.stage || '—'}</span><span>{num(ep.final_floor,0)}</span><span>{num(ep.total_reward)}</span><span>{ep.steps}</span></button>})}{!items.length&&<Empty text="No episodes match / 没有匹配的对局"/>}</div> }

function Replay({run}:{run:Run}) {
  const [episodes,setEpisodes]=useState<Episode[]>([]), [episodeId,setEpisodeId]=useState(sessionStorage.getItem('replayEpisode')||''), [rows,setRows]=useState<ReplayStep[]>([]), [selected,setSelected]=useState(0)
  useEffect(()=>{void API<{items:Episode[]}>(`/api/runs/${encodeURIComponent(run.name)}/episodes?page_size=200`).then(data=>{setEpisodes(data.items);setEpisodeId(current=>data.items.some(e=>e.episode_id===current)?current:data.items[0]?.episode_id||'')})},[run.name,run.episode_count])
  useEffect(()=>{if(episodeId)void API<{rows:ReplayStep[]}>(`/api/runs/${encodeURIComponent(run.name)}/episodes/${encodeURIComponent(episodeId)}`).then(data=>{setRows(data.rows);setSelected(0)})},[run.name,episodeId])
  useEffect(()=>{const key=(event:KeyboardEvent)=>{if(event.key==='ArrowLeft')setSelected(value=>Math.max(0,value-1));if(event.key==='ArrowRight')setSelected(value=>Math.min(rows.length-1,value+1))};window.addEventListener('keydown',key);return()=>window.removeEventListener('keydown',key)},[rows.length])
  const step=rows[selected], next=rows[selected+1]
  const rooms=useMemo(()=>{const result:{row:ReplayStep;index:number}[]=[];rows.forEach((row,index)=>{if(row.room_type==='Map')return;const prior=result[result.length-1]?.row;if(!prior||prior.floor!==row.floor||prior.room_type!==row.room_type)result.push({row,index})});return result},[rows])
  const activeRoom=Math.max(0,rooms.findIndex((room,index)=>selected>=room.index&&(index===rooms.length-1||selected<rooms[index+1].index)))
  const cards=step ? step.cards||step.hand||step.options||[] : []
  const currentEpisode=episodes.find(item=>item.episode_id===episodeId)
  return <><div className="replay-picker"><label>EPISODE / 对局</label><select value={episodeId} onChange={e=>setEpisodeId(e.target.value)}>{episodes.map(e=><option key={e.episode_id} value={e.episode_id}>{e.episode_id} · {outcomeLabel(e.outcome)} · F{e.final_floor ?? '—'}</option>)}</select>{currentEpisode&&<span className={`replay-outcome ${won(currentEpisode.outcome)?'win':'loss'}`}>{outcomeLabel(currentEpisode.outcome)}</span>}</div>{step?<div className="replay-layout"><aside className="route-panel"><PanelTitle icon={<Route/>} title="Rooms / 房间路线"/><div className="route-line">{rooms.map((room,i)=><button key={room.index} className={activeRoom===i?'active':''} onClick={()=>setSelected(room.index)}><RoomIcon room={room.row.room_type}/><span><b>F{room.row.floor ?? '—'} · {room.row.room_type || room.row.decision}</b><small>Act {room.row.act ?? '—'} · starts at action {room.index+1}</small></span></button>)}</div></aside><section className="replay-main"><div className="battle-head"><div><p>ACTION {selected+1} · TURN {step.round ?? '—'} · ACT {step.act ?? '—'} · FLOOR {step.floor ?? '—'}</p><h2>{step.room_type || step.decision}</h2></div><div className="step-nav"><button aria-label="Previous action" disabled={selected===0} onClick={()=>setSelected(selected-1)}><ChevronLeft/></button><span>{selected+1} / {rows.length}</span><button aria-label="Next action" disabled={selected===rows.length-1} onClick={()=>setSelected(selected+1)}><ChevronRight/></button></div></div><input className="replay-scrubber" aria-label="Replay position" type="range" min="0" max={Math.max(0,rows.length-1)} value={selected} onChange={e=>setSelected(Number(e.target.value))}/><PlayerStrip step={step}/><StateDelta step={step} next={next}/><div className="combat-board"><div><h3>Enemies & intents / 敌人与意图</h3><div className="enemy-grid">{step.enemies?.length?step.enemies.map((enemy:any,i)=><EntityCard key={i} entity={enemy} enemy/>):<Empty text="No enemies / 非战斗节点"/>}</div></div><div><h3>{DecisionHeading(step)}</h3><div className="card-grid">{cards.map((card:any,i)=><GameCard key={i} card={card} option={!step.hand?.length} selected={step.selected?.index===card.index}/>)}</div></div></div><div className="action-log"><Zap/><div><span>CHOSEN ACTION / 实际选择</span><b>{actionLabel(step)}</b></div><div><span>REWARD</span><b>{num(step.reward,3)}</b></div><div><span>VALUE</span><b>{num(step.value,3)}</b></div><div><span>LOG P</span><b>{num(step.logp,3)}</b></div></div><StateDetails step={step}/><details><summary>Raw state / 原始状态</summary><pre className="config">{JSON.stringify(step.state,null,2)}</pre></details></section><DeckPanel step={step}/></div>:<Empty text="No replay data / 暂无可复盘数据"/>}</>
}

function StateDelta({step,next}:{step:ReplayStep;next?:ReplayStep}) { if(!next)return null; const fields:[string,number|undefined,number|undefined][]=[['HP',step.player?.hp,next.player?.hp],['Block',step.player?.block,next.player?.block],['Gold',step.player?.gold,next.player?.gold],['Energy',step.energy,next.energy],['Enemy HP',step.enemies?.reduce((n:number,e:any)=>n+(e.hp||0),0),next.enemies?.reduce((n:number,e:any)=>n+(e.hp||0),0)]];const changes=fields.map(([label,a,b])=>[label,typeof a==='number'&&typeof b==='number'?b-a:0] as const).filter(([,delta])=>delta!==0);return changes.length?<div className="state-delta"><span>ACTION RESULT / 动作结果</span>{changes.map(([label,delta])=><b className={delta>0?'positive':'negative'} key={label}>{label} {delta>0?'+':''}{delta}</b>)}</div>:null }

function DecisionHeading(step:ReplayStep) { if(step.hand?.length)return 'Hand / 手牌';if(step.cards?.length)return 'Card reward / 卡牌奖励';if(step.options?.length)return 'Options / 事件选项';return 'Choices / 可选动作' }
function PlayerStrip({step}:{step:ReplayStep}) { const p=step.player||{}; return <div className="player-strip"><b>{p.name||'Unknown'}</b><span><Heart/> {p.hp??'—'} / {p.max_hp??'—'}</span><span><Shield/> {p.block??0}</span><span><Coins/> {p.gold??0}</span>{step.energy!==null&&step.energy!==undefined&&<span><Zap/> {step.energy} / {step.max_energy??'—'}</span>}</div> }

function renderDescription(card:any) { let text=String(card.description||'');const values={...(card.vars||{}),...(card.stats||{})};for(const [key,value] of Object.entries(values)){if(value===null||value===undefined)continue;text=text.replace(new RegExp(`\\{${key}(?::[^}]*)?\\}`, 'gi'),String(value))}return text.replace(/\{([^}:]+)(?::[^}]*)?\}/g,'$1') }
function GameCard({card,selected=false,option=false}:{card:any;selected?:boolean;option?:boolean}) { const asset=card.id||card.name;const [showArt,setShowArt]=useState(Boolean(asset&&!assetMisses.has(asset)));const hasCost=!option&&card.cost!==null&&card.cost!==undefined;return <div className={`game-card ${selected?'chosen':''} ${option?'option-card':''}`}>{showArt&&<img className="card-art" src={`/api/assets/by-name/${encodeURIComponent(asset)}`} onError={()=>{assetMisses.add(asset);setShowArt(false)}}/>}<span className={`cost ${option?'option-index':''}`}>{hasCost?card.cost:`#${card.index??'•'}`}</span><b>{card.name||card.title||`Option ${card.index}`}{card.upgraded?'+':''}</b><small>{card.rarity||card.type||''}</small><p>{renderDescription(card)}</p>{selected&&<em>CHOSEN / 已选择</em>}</div> }
function EntityCard({entity,enemy}:{entity:any;enemy?:boolean}) { return <div className={`entity-card ${enemy?'enemy':''}`}><Swords/><b>{entity.name}</b><span><Heart/> {entity.hp}/{entity.max_hp}</span><small>{entity.intents?.map((intent:any)=>`${intent.type}${intent.damage!==null&&intent.damage!==undefined?` ${intent.damage}`:''}`).join(' · ')||'No intent'}</small>{entity.powers?.length>0&&<div className="power-list">{entity.powers.map((power:any)=><i key={power.name}>{power.name} {power.amount}</i>)}</div>}</div> }
function StateDetails({step}:{step:ReplayStep}) { const state=step.state as any;return <div className="state-details"><span>Draw {state?.draw_pile_count??'—'}</span><span>Discard {state?.discard_pile_count??'—'}</span>{step.player_powers?.map((power:any)=><span key={power.name}>{power.name} {power.amount}</span>)}</div> }
function DeckPanel({step}:{step:ReplayStep}) { const deck=step.player?.deck||[], relics=step.player?.relics||[], potions=step.player?.potions||[];const grouped=Array.from(deck.reduce((map:Map<string,{card:any;count:number}>,card:any)=>{const key=`${card.name}${card.upgraded?'+':''}`;const current=map.get(key);current?current.count++:map.set(key,{card,count:1});return map},new Map()).values());return <aside className="deck-panel"><PanelTitle icon={<Layers3/>} title={`Deck / 卡组 (${step.player?.deck_size ?? deck.length})`}/><div className="deck-summary">{grouped.map(({card,count}:any)=><div key={`${card.name}-${card.upgraded}`}><span>{card.cost??'•'}</span><b>{card.name}{card.upgraded?'+':''}</b><em>×{count}</em></div>)}</div><PanelTitle icon={<Crown/>} title="Relics / 遗物"/><div className="relics">{relics.length?relics.map((relic:any,i:number)=><span key={i}>{relic.name}</span>):<span>None / 无</span>}</div><PanelTitle icon={<Sparkles/>} title="Potions / 药水"/><div className="relics">{potions.length?potions.map((potion:any,i:number)=><span key={i}>{potion.name}</span>):<span>None / 无</span>}</div></aside> }
function RoomIcon({room}:{room?:string}) { const value=room?.toLowerCase()||'';return value.includes('boss')?<Crown/>:value.includes('monster')||value.includes('combat')?<Swords/>:value.includes('rest')?<Heart/>:<CircleDot/> }
function PanelTitle({icon,title}:{icon:ReactNode;title:string}) { return <div className="panel-title">{icon}<h2>{title}</h2></div> }
function Empty({text}:{text:string}) { return <div className="empty">{text}</div> }
export default App
