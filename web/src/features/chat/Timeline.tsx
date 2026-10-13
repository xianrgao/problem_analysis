import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowDown, Braces, Check, ChevronRight, Circle, FileCode2, LoaderCircle, Pause, Table2 } from 'lucide-react';
import { BrandMark } from '../../components/Icons';
import { Markdown } from '../../components/Markdown';
import { api } from '../../lib/api';
import { timeLabel, displayValue } from '../../lib/format';
import type { ExecutionEvent, Message, Plan, SessionDetail, TableData, Variable } from '../../lib/types';
import { PlanCard } from './PlanCard';

interface StepGroup { start: ExecutionEvent; end?: ExecutionEvent; children: ExecutionEvent[] }
type Item = { type: 'message'; data: Message; key: string; at: string } | { type: 'event'; data: ExecutionEvent; key: string; at: string } | { type: 'step'; data: StepGroup; key: string; at: string } | { type: 'plan'; data: Plan; key: string; at: string };
const hidden = ['heartbeat', 'token', 'message'];
const failed = ['failed', 'error', 'blocked'];
const stopped = ['cancelled', 'interrupted', 'paused'];
const kindLabels: Record<string, string> = { skill: '已读取的 Skill', reference: '参考资料', sql: 'SQL 查询', code: 'Python 代码', tool: '工具调用', tool_result: '执行结果', variable: '数据变量', error: '错误与修复', issue: '问题台账' };
const detailLabels: Record<string, string> = { skill: 'Skill', version: '版本', hash: '内容指纹', reference: '参考资料', content: '读取内容', sql: 'SQL', attempt: '执行次数', code: 'Python 代码', tool: '工具', args: '调用参数', outputs: '输出变量', stdout: '执行日志', reads: '读取的变量版本', error: '错误详情', variable: '变量', title: '步骤', question: '待补充信息', feedback: '调整意见' };

function groupEvents(events: ExecutionEvent[]): Item[] {
  const steps = new Map<string, StepGroup>(); const items: Item[] = []; let open: StepGroup | null = null;
  for (const event of [...events].sort((a, b) => Number(a.id) - Number(b.id))) {
    if (!event.message || hidden.includes(event.kind || '')) continue;
    const key = `${event.run_id}:${event.node_id}`;
    if (event.kind === 'step') {
      const group = steps.get(key);
      if (event.status === 'running' && (!group || group.end)) { const created: StepGroup = { start: event, children: [] }; steps.set(key, created); open = created; items.push({ type: 'step', data: created, key: `s-${event.id}`, at: event.created_at }); continue; }
      if (group && !group.end) { group.end = event; if (open === group) open = null; continue; }
    } else if (event.kind !== 'run') {
      const group = event.run_id && event.node_id ? steps.get(key) : open;
      if (group && !group.end) { group.children.push(event); continue; }
    }
    items.push({ type: 'event', data: event, key: `e-${event.id}`, at: event.created_at });
  }
  return items;
}

function VariablePreview({ sessionId, variable }: { sessionId: string; variable: Variable }) {
  const [data, setData] = useState<TableData | null>(null); const [error, setError] = useState('');
  useEffect(() => { if (variable.kind !== 'table') return; let active = true; api.preview(sessionId, variable.name, variable.version).then(result => { if (active) setData(result); }).catch(failure => { if (active) setError(failure.message); }); return () => { active = false; }; }, [sessionId, variable.name, variable.version, variable.kind]);
  if (variable.kind !== 'table') return null;
  if (error) return <div className="inline-error">{error}</div>;
  if (!data) return <div className="artifact-loading"><span className="pulse-dot"/>正在读取预览…</div>;
  if (data.available === false) return <div className="inline-error">第 {data.source_version} 版预览生成失败，已标记为不可用：{data.error}</div>;
  const columns = data.columns.map(column => typeof column === 'string' ? column : column.name);
  return <div className="detail-preview"><small>{data.complete ? '完整展示' : '代表性预览'} {data.rows.length} 行 / 共 {(data.total_rows ?? variable.row_count ?? 0).toLocaleString()} 行</small><div className="table-scroll"><table><thead><tr>{columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{data.rows.slice(0, 40).map((row, index) => <tr key={index}>{columns.map(column => <td key={column} title={displayValue(row[column])}>{displayValue(row[column])}</td>)}</tr>)}</tbody></table></div></div>;
}

function DetailValue({ name, value, sessionId, onVariable }: { name: string; value: unknown; sessionId: string; onVariable: (name: string) => void }) {
  if (name === 'variable' && value && typeof value === 'object') { const variable = value as Variable; return <div className="detail-variable"><button onClick={() => onVariable(variable.name)}><Table2 size={12}/>{variable.name}<em>v{variable.version}</em><ChevronRight size={12}/></button><p>{variable.description}{variable.row_count !== null && variable.row_count !== undefined && ` · ${variable.row_count.toLocaleString()} 行`}</p><VariablePreview sessionId={sessionId} variable={variable}/></div>; }
  if (name === 'outputs' && Array.isArray(value)) return <div className="detail-variable-list">{value.map((item, index) => { const label = typeof item === 'string' ? item : (item as Record<string, string>)?.name || (item as Record<string, string>)?.variable_name; return label ? <button key={index} onClick={() => onVariable(label)}><Table2 size={12}/>{label}<ChevronRight size={12}/></button> : <pre key={index}>{JSON.stringify(item, null, 2)}</pre>; })}</div>;
  if (typeof value === 'string') return <pre>{value}</pre>;
  return <pre>{typeof value === 'object' ? JSON.stringify(value, null, 2) : displayValue(value)}</pre>;
}

function Details({ event, sessionId, onVariable }: { event: ExecutionEvent; sessionId: string; onVariable: (name: string) => void }) {
  const entries = Object.entries(event.details || {}).filter(([, value]) => value !== null && value !== undefined && value !== '' && !(Array.isArray(value) && !value.length));
  return <>{entries.map(([key, value]) => <div className="detail-entry" key={key}><h6>{detailLabels[key] || key}</h6><DetailValue name={key} value={value} sessionId={sessionId} onVariable={onVariable}/></div>)}</>;
}

function StepIcon({ status }: { status: string }) {
  return <span className="execution-node">{failed.includes(status) ? <AlertTriangle size={14}/> : status === 'running' ? <LoaderCircle size={14} className="spin"/> : stopped.includes(status) ? <Pause size={12}/> : status === 'completed' || status === 'succeeded' ? <Check size={14}/> : <Circle size={9}/>}</span>;
}

function ExecutionStep({ group, sessionId, onVariable }: { group: StepGroup; sessionId: string; onVariable: (name: string) => void }) {
  const [open, setOpen] = useState(false); const status = group.end?.status || 'running'; const sections = [...group.children, ...(group.end && Object.keys(group.end.details || {}).length ? [group.end] : [])];
  const hasCode = group.children.some(child => ['sql', 'code'].includes(child.kind || ''));
  return <article className={`execution-step ${failed.includes(status) ? 'has-error' : ''} ${status === 'running' ? 'is-running' : ''}`}><StepIcon status={status}/><div className="execution-body"><p className="step-progress">{group.start.message}<time>{timeLabel(group.start.created_at)}</time></p>{group.end && <p className={`step-finding ${failed.includes(status) ? 'blocked' : ''}`}>{group.end.message}</p>}{sections.length > 0 && <><button className="detail-toggle" onClick={() => setOpen(value => !value)}><ChevronRight size={12} className={open ? 'turn-down' : ''}/>{hasCode ? <FileCode2 size={12}/> : <Braces size={12}/>}<span>{[...new Set(group.children.map(child => kindLabels[child.kind || ''] || '执行记录'))].slice(0, 4).join(' · ') || '执行详情'}</span></button>{open && <div className="execution-details">{sections.map(child => <section key={child.id} className={child.kind === 'error' ? 'detail-error' : ''}><h5>{child === group.end ? '本步产出' : kindLabels[child.kind || ''] || '执行记录'}<span>{child === group.end ? '' : child.message}</span></h5><Details event={child} sessionId={sessionId} onVariable={onVariable}/></section>)}</div>}</>}</div></article>;
}

function EventLine({ event, sessionId, onVariable }: { event: ExecutionEvent; sessionId: string; onVariable: (name: string) => void }) {
  const [open, setOpen] = useState(false); const status = event.status || ''; const hasDetails = Object.keys(event.details || {}).length > 0;
  return <article className={`execution-step event-line ${failed.includes(status) || event.kind === 'error' ? 'has-error' : ''}`}><StepIcon status={event.kind === 'error' ? 'failed' : status === 'running' ? 'completed' : status}/><div className="execution-body"><p className="step-progress">{event.message}<time>{timeLabel(event.created_at)}</time></p>{hasDetails && <button className="detail-toggle" onClick={() => setOpen(value => !value)}><ChevronRight size={12} className={open ? 'turn-down' : ''}/><Braces size={12}/><span>{kindLabels[event.kind || ''] || '执行详情'}</span></button>}{open && <div className="execution-details"><section><Details event={event} sessionId={sessionId} onVariable={onVariable}/></section></div>}</div></article>;
}

export function Timeline({ detail, onApprove, onRevise, onVariable }: { detail: SessionDetail; onApprove: (plan: Plan) => Promise<void>; onRevise: (plan: Plan, feedback: string) => Promise<void>; onVariable: (name: string) => void }) {
  const container = useRef<HTMLDivElement>(null); const stick = useRef(true); const [showScroll, setShowScroll] = useState(false); const sessionId = detail.session.id;
  const current = detail.plan || detail.plans?.at(-1);
  const items: Item[] = [
    ...(detail.messages || []).map(data => ({ type: 'message' as const, data, key: `m-${data.id}`, at: data.created_at })),
    ...groupEvents(detail.events || []),
    ...(detail.plans || (current ? [current] : [])).map(data => ({ type: 'plan' as const, data, key: `p-${data.id}-${data.revision}`, at: data.created_at || '' })),
  ];
  items.sort((a, b) => a.at.localeCompare(b.at));
  useEffect(() => { if (stick.current && container.current) container.current.scrollTop = container.current.scrollHeight; }, [detail]);
  return <div className="timeline-wrap"><div className="timeline-scroll" ref={container} onScroll={() => { const el = container.current; if (el) { stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 110; setShowScroll(!stick.current); } }}><div className="timeline-content"><div className="conversation-origin"><span/>分析记录 · 可追溯的每一步<span/></div>{items.map(item => item.type === 'message' ? item.data.role === 'user' ? <article className="user-message" key={item.key}><div><Markdown>{item.data.content}</Markdown></div><time>{timeLabel(item.data.created_at)}</time></article> : <article className="assistant-message" key={item.key}><BrandMark small/><div><div className="message-byline">LINEA<span>产线硅基工程师</span></div><Markdown>{item.data.content}</Markdown></div></article> : item.type === 'step' ? <ExecutionStep key={item.key} group={item.data} sessionId={sessionId} onVariable={onVariable}/> : item.type === 'event' ? <EventLine key={item.key} event={item.data} sessionId={sessionId} onVariable={onVariable}/> : <PlanCard key={item.key} plan={item.data} current={item.data.id === current?.id && item.data.revision === current?.revision} onApprove={onApprove} onRevise={onRevise}/>)}{!items.length && <div className="timeline-empty">会话已就绪，输入现场问题或导入数据即可开始。</div>}</div></div>{showScroll && <button className="scroll-latest" onClick={() => { stick.current = true; container.current?.scrollTo({ top: container.current.scrollHeight, behavior: 'smooth' }); }}><ArrowDown size={13}/>最新进度</button>}</div>;
}
