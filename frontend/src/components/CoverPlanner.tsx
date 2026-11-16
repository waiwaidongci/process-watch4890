import {FormEvent,useEffect,useMemo,useRef,useState} from 'react';
import {Play,Pause,Plus,Trash2,Link2,Search,RefreshCw,Check} from 'lucide-react';
import {Cover,CoverSignal} from '../types/domain';
import {addCoverCause,addCoverConflict,getCover,removeCoverCause,removeCoverConflict,resetCoverSearch,runCoverSearch} from '../api/cover';
import {useAuth} from '../stores/auth';
import {CoverStatus,SignalKinds} from '../constants/enums';
import {Notice} from './common/Notice';
import {number} from '../utils/format';

const KIND_ORDER = ['individual','moving_range','ewma'] as const;
const key = (s:{sequence:number;kind:string}) => s.sequence + ':' + s.kind;
const label = (kind:string) => SignalKinds[kind as keyof typeof SignalKinds] || kind;

export function CoverPlanner({runId}:{runId:number}){
  const {user}=useAuth();
  const readOnly=user?.role==='viewer';
  const [cover,setCover]=useState<Cover|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [note,setNote]=useState('');
  const [title,setTitle]=useState('');
  const [cost,setCost]=useState('2');
  const [picked,setPicked]=useState<Set<string>>(new Set());
  const pickedRef=useRef(picked);
  pickedRef.current=picked;
  const [conflictA,setConflictA]=useState('');
  const [conflictB,setConflictB]=useState('');
  const [budget,setBudget]=useState('2000');

  async function load(){
    setError('');
    try{setCover(await getCover(runId))}
    catch(e){setError((e as Error).message);setCover(null)}
  }
  useEffect(()=>{setPicked(new Set());setNote('');load()},[runId]);

  async function act(fn:()=>Promise<unknown>,okNote?:string){
    setBusy(true);setError('');setNote('');
    try{
      const res=await fn() as {invalidated?:boolean}|undefined;
      if(res&&res.invalidated)setNote('规则已更新，旧搜索进度已失效，需重新开始。');
      else if(okNote)setNote(okNote);
      await load();
    }catch(e){setError((e as Error).message)}
    finally{setBusy(false)}
  }

  async function startOrResume(){
    setBusy(true);setError('');setNote('');
    try{
      const n=Math.max(1,Math.min(200000,Math.floor(Number(budget)||1)));
      const next=await runCoverSearch(runId,n);
      setCover(next);
      setNote(next.started?'已建立新的搜索并从首个节点开始。':'已从上次暂停处继续搜索。');
    }catch(e){setError((e as Error).message)}
    finally{setBusy(false)}
  }

  const causes=cover?.causes||[];
  const causeTitle=(id:number)=>causes.find(c=>c.id===id)?.title||('原因 '+id);
  const groups=useMemo(()=>{
    const map:Record<string,CoverSignal[]>={};
    for(const kind of KIND_ORDER)map[kind]=[];
    for(const s of cover?.signals||[]) (map[s.kind]||(map[s.kind]=[])).push(s);
    return map;
  },[cover]);

  function updatePicked(next:Set<string>,syncRef=false){
    if(syncRef)pickedRef.current=next;
    setPicked(next);
  }
  function toggle(s:CoverSignal){
    const k=key(s);
    const next=new Set(pickedRef.current);
    next.has(k)?next.delete(k):next.add(k);
    updatePicked(next,true);
  }
  function toggleGroup(kind:string){
    const list=groups[kind]||[];
    const next=new Set(pickedRef.current);
    const all=list.every(s=>next.has(key(s)));
    list.forEach(s=>all?next.delete(key(s)):next.add(key(s)));
    updatePicked(next,true);
  }
  function submit(e:FormEvent){
    e.preventDefault();
    const current=pickedRef.current;
    const signals=(cover?.signals||[]).filter(s=>current.has(key(s))).map(s=>({sequence:s.sequence,kind:s.kind}));
    if(!signals.length){setError('至少选择一条该原因能解释的信号。');return}
    const c=Math.floor(Number(cost));
    if(!Number.isFinite(c)||c<=0){setError('代价必须为正整数。');return}
    act(()=>addCoverCause(runId,{title:title.trim(),cost:c,signals}),'候选原因已登记。').then(()=>{setTitle('');setCost('2');setPicked(new Set())});
  }
  function submitConflict(e:FormEvent){
    e.preventDefault();
    if(!conflictA||!conflictB||conflictA===conflictB){setError('请选择两条不同的候选原因。');return}
    act(()=>addCoverConflict(runId,{cause_a_id:Number(conflictA),cause_b_id:Number(conflictB)}),'互斥关系已登记。').then(()=>{setConflictA('');setConflictB('')});
  }

  if(!cover)return <section className="cover-planner"><h2>排查组合</h2><Notice message={error} retry={load}/>{!error&&<p className="empty">正在载入该运行的信号清单…</p>}</section>;

  const search=cover.search;
  const best=search.best;
  const selectedIds=new Set(best?.cause_ids||[]);
  const reachable=search.status!=='infeasible';
  const proven=!!best&&search.cost_proven;
  const tieProven=search.proof;

  return <section className="cover-planner">
    <div className="section-heading">
      <h2>排查组合</h2>
      <span>{cover.signal_total} 条信号 / {causes.length} 条候选原因 / {cover.conflicts.length} 条互斥</span>
    </div>
    <p className="caption">用最少的调查代价解释全部信号：先使总代价最小，再按原因条数、编号序列决胜。结果是排查建议，不是因果证明，也不自动控制设备。</p>
    <Notice message={error} retry={load}/>
    {note&&<div className="cover-note" role="status">{note}</div>}

    <div className="cover-status">
      <div><span>搜索状态</span><b className={search.status==='infeasible'?'alert':proven?'safe':''}>{cover.signal_total===0?'无信号 · 无需排查':(CoverStatus[search.status as keyof typeof CoverStatus]||search.status)}</b></div>
      <div><span>当前可行组合代价</span><b>{best?best.cost:'—'}</b><small>{best?(best.cause_count?`${best.cause_count} 条原因 · ${best.cause_ids.join(', ')}`:'空组合 · 无需选择原因'):'尚无可行组合'}</small></div>
      <div><span>可信代价下界</span><b>{search.lower_bound===null?'—':search.lower_bound}</b><small>{best?(search.lower_bound===best.cost&&proven?'下界已与当前组合相等':'下界仍低于当前组合'):'尚未得出结论'}</small></div>
      <div><span>是否已证明最优</span><b className={tieProven?'safe':''}>{tieProven?'已证明最优':proven?'代价已证最优 · 决胜未穷尽':'尚未证明最优'}</b><small>节点 {search.nodes} · 轮次 {search.episodes} · 待展开 {search.frontier}</small></div>
    </div>

    {cover.signal_total===0&&<div className="cover-note" role="status">该运行没有本工具三类越界信号，无需排查组合；这不等于证明过程绝对稳定。</div>}
    {search.status==='infeasible'&&<div className="cover-infeasible" role="alert">
      <b>无解：不存在覆盖全部信号且不违反互斥的候选组合。</b>
      <p>{search.infeasible_reason}</p>
      {search.uncoverable_signals.length>0&&<p>没有任何候选原因可解释的信号共 {search.uncoverable_signals.length} 条，例如
        {search.uncoverable_signals.slice(0,6).map(x=>`#${x.sequence} ${label(x.kind)}`).join('、')}{search.uncoverable_signals.length>6?' 等':''}。请补充候选原因或调整其覆盖范围。</p>}
    </div>}

    <div className="cover-columns">
      <div className="cover-causes">
        <h3>候选原因</h3>
        {!causes.length&&<p className="empty">尚未登记。每条原因需要一个正整数代价，以及它能解释的信号。</p>}
        <ul className="cover-cause-list">
          {causes.map(c=><li key={c.id} className={selectedIds.has(c.id)?'selected':''}>
            <div className="cause-head">
              <b>{c.title}</b>
              <span className="cause-cost">代价 {c.cost}</span>
              {selectedIds.has(c.id)&&<span className="cause-tag">当前组合</span>}
            </div>
            <small>解释 {c.signals.length} 条信号 · {[...new Set(c.signals.map(s=>label(s.kind)))].join(' / ')} · 登记人 {c.owner}</small>
            {!readOnly&&<button className="icon-button" aria-label={'删除原因 '+c.id} disabled={busy} onClick={()=>act(()=>removeCoverCause(runId,c.id),'候选原因已删除。')}><Trash2 size={13}/></button>}
          </li>)}
        </ul>

        <h3>互斥关系</h3>
        {!cover.conflicts.length&&<p className="empty">未登记互斥。互斥表示两条原因不可能同时成立。</p>}
        <ul className="cover-conflict-list">
          {cover.conflicts.map(c=><li key={c.id}>
            <Link2 size={12}/>
            <span>{causeTitle(c.cause_a_id)} <i>×</i> {causeTitle(c.cause_b_id)}</span>
            {!readOnly&&<button className="icon-button" aria-label={'删除互斥 '+c.id} disabled={busy} onClick={()=>act(()=>removeCoverConflict(runId,c.id),'互斥关系已删除。')}><Trash2 size={13}/></button>}
          </li>)}
        </ul>
      </div>

      <div className="cover-search-panel">
        <h3>搜索控制</h3>
        <p className="caption">搜索按约定节点数暂停，之后可从断点继续；登记、删除原因或互斥后，旧进度失效。</p>
        <label>本次搜索量（节点）
          <input aria-label="本次搜索量" type="number" min="1" max="200000" value={budget} onChange={e=>setBudget(e.target.value)} disabled={readOnly}/>
        </label>
        {!readOnly&&<div className="cover-actions">
          <button className="primary" disabled={busy||!reachable||!causes.length||(tieProven&&!search.ties_pending)} onClick={startOrResume}>
            {search.status==='paused'?<><Play size={14}/>继续搜索</>:tieProven?<><Check size={14}/>已穷尽全部组合</>:<><Search size={14}/>开始搜索</>}
          </button>
          <button className="outline" disabled={busy||search.status===CoverStatus.draft} onClick={()=>act(()=>resetCoverSearch(runId),'搜索进度已清空。')}><RefreshCw size={14}/>清空进度</button>
        </div>}
        {search.status==='paused'&&<p className="pause-hint"><Pause size={12}/>本次搜索量已用尽，仍有 {search.frontier} 个分支待查；当前组合与下界均按已搜索部分给出。</p>}
        {readOnly&&<p className="role-note">观察员可阅读组合与解释；登记原因和搜索需要分析师权限。</p>}
        <p className="fingerprint">规则指纹 {cover.rules_hash.slice(0,16)}…</p>
      </div>
    </div>

    {!readOnly&&cover.signal_total>0&&<div className="cover-forms">
      <form className="cover-form" onSubmit={submit}>
        <div className="form-heading"><span>01</span><h3>登记候选原因</h3></div>
        <div className="form-row">
          <label>原因名称<input aria-label="原因名称" minLength={2} maxLength={160} value={title} onChange={e=>setTitle(e.target.value)} placeholder="例如：温控回路漂移" required/></label>
          <label>调查代价（正整数）<input aria-label="调查代价" type="number" min="1" step="1" value={cost} onChange={e=>setCost(e.target.value)} required/></label>
        </div>
        <div className="signal-picker">
          {KIND_ORDER.map(kind=>{
            const list=groups[kind]||[];
            if(!list.length)return null;
            const all=list.every(s=>picked.has(key(s)));
            return <div key={kind} className="signal-group">
              <div className="signal-group-head">
                <b>{label(kind)}</b><small>{list.length} 条 · 已选 {list.filter(s=>picked.has(key(s))).length}</small>
                <button type="button" className="text-link" onClick={()=>toggleGroup(kind)}>{all?'清除':'全选'}</button>
              </div>
              <div className="signal-chips">
                {list.map(s=><button type="button" key={key(s)} aria-pressed={picked.has(key(s))} className={picked.has(key(s))?'chip on':'chip'} onClick={()=>toggle(s)}>
                  #{s.sequence}<i>{s.kind==='individual'?'I':s.kind==='ewma'?'E':'MR'}</i>
                  {s.candidate_cause_ids.length>0&&<span className="chip-dot" title={s.candidate_cause_ids.length+' 条已登记原因可解释'}/>}
                </button>)}
              </div>
            </div>
          })}
        </div>
        <p className="caption">已选 {picked.size} 条信号。带标记的信号已至少被一条已登记原因覆盖。</p>
        <button className="outline" disabled={busy}><Plus size={14}/>登记原因</button>
      </form>

      <form className="cover-form" onSubmit={submitConflict}>
        <div className="form-heading"><span>02</span><h3>登记互斥</h3></div>
        <label>原因 A<select aria-label="互斥原因 A" value={conflictA} onChange={e=>setConflictA(e.target.value)}><option value="">请选择</option>{causes.map(c=><option key={c.id} value={c.id}>{c.id} / {c.title}</option>)}</select></label>
        <label>原因 B<select aria-label="互斥原因 B" value={conflictB} onChange={e=>setConflictB(e.target.value)}><option value="">请选择</option>{causes.map(c=><option key={c.id} value={c.id}>{c.id} / {c.title}</option>)}</select></label>
        <button className="outline" disabled={busy||causes.length<2}><Link2 size={14}/>登记互斥</button>
      </form>
    </div>}

    <div className="section-heading"><h2>逐条信号解释</h2><span>{best?`当前组合：${best.cause_ids.map(id=>'#'+id).join(' + ')}`:'尚无可行组合'}</span></div>
    <div className="cover-signal-table">
      {KIND_ORDER.map(kind=>{
        const list=groups[kind]||[];
        if(!list.length)return null;
        return <div key={kind} className="cover-signal-group">
          <h4>{label(kind)} <small>{list.length} 条{best?` · 未解释 ${list.filter(s=>!s.explained_by.length).length}`:''}</small></h4>
          <div className="cover-signal-rows">
            {list.map(s=><div key={key(s)} className={'cover-signal-row '+(best?(s.explained_by.length?'ok':'missing'):'')}>
              <span className="seq">#{s.sequence}</span>
              <span className="val">{s.value===null?'—':number(s.value,3)}</span>
              <span className="cands">{s.candidate_cause_ids.length?`候选 ${s.candidate_cause_ids.map(id=>'#'+id).join(' ')}`:'无候选原因'}</span>
              <span className="expl">{best?(s.explained_by.length?`由 ${s.explained_by.map(id=>'#'+id).join(' ')} 解释`:'未解释'):'—'}</span>
            </div>)}
          </div>
        </div>
      })}
    </div>
    <p className="caption">信号的序号、种类与数值来自该已完成运行；覆盖集合仅代表登记者的判断，不构成已证实的因果解释。</p>
  </section>;
}
