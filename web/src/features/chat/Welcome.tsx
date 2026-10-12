import { ArrowUpRight, ChartNoAxesCombined, Database, GitBranch, ScanLine } from 'lucide-react';
import { BrandMark } from '../../components/Icons';
const examples = [
  { icon: ScanLine, tag: '问题调查', title: '从异常出发，寻找问题规律', description: '班次 · 批次 · 设备 · 变化点', prompt: '分析 A区 ST20 工站最近两周压装力 NC 增多的原因，检查开班、停线滞留、批次及设备变化点。' },
  { icon: Database, tag: '自然语言取数', title: '把现场问题转化为可用数据', description: 'MOS 查询 · 数据整理 · 全程追溯', prompt: '查询 A区 ST20 工站最近两周的 NC 明细，包含 TG号、任务、参数、检测值和发生时间。' },
  { icon: ChartNoAxesCombined, tag: '曲线与数据', title: '让参数分布与曲线说明问题', description: '过程曲线 · 分组对比 · 趋势', prompt: '对比 ST20 工站合格件和不良件的压装力—位移曲线，提取峰值和异常区段。' },
  { icon: GitBranch, tag: '持续跟踪', title: '将证据连接到改善与验证', description: '问题台账 · 措施 · 验证闭环', prompt: '梳理当前调查需要记录的证据和验证项目，并为产线压装力异常建立问题跟踪记录。' },
];
export function Welcome({ onPrompt, mode }: { onPrompt: (prompt: string) => void; mode: string }) { return <div className="welcome"><div className="welcome-eyebrow"><span/>以证据推进每一次工程判断</div><BrandMark/><h1>让产线问题，<br/><span>有迹可循。</span></h1><p className="welcome-description">从现场异常到数据规律，再到验证与改善。<br/>描述当前问题，开始一次可追溯的工程分析。</p><div className="suggestion-grid">{examples.map(example => <button className="suggestion-card" key={example.tag} onClick={() => onPrompt(example.prompt)}><div className="suggestion-top"><example.icon size={19} strokeWidth={1.6}/><span>{example.tag}</span><ArrowUpRight size={15}/></div><b>{example.title}</b><small>{example.description}</small></button>)}</div><div className="welcome-footnote"><span className="mini-dot"/>{mode === 'demo' ? '当前连接模拟产线 · 所有数据均为本地生成的示例数据' : '当前使用模型 API · 查询结果由已配置的数据连接提供'}</div></div>; }
