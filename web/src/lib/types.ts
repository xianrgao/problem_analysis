export type SessionStatus = 'idle' | 'running' | 'queued' | 'awaiting_approval' | 'completed' | 'failed' | 'cancelled' | 'paused';
export interface Session { id: string; title: string; status: SessionStatus | string; pinned: boolean; archived: boolean; created_at: string; updated_at: string }
export interface Message { id: string; role: 'user' | 'assistant' | 'system'; content: string; created_at: string; turn?: number }
export interface PlanNode { id: string; title: string; description?: string; expected_output?: string; progress_message?: string; status?: string; depends_on?: string[]; skill?: string; kind?: string }
export interface Plan { id: string; session_id: string; revision: number; title: string; status: string; nodes: PlanNode[]; created_at?: string; summary?: string; stage?: string }
export interface ExecutionEvent { id?: string | number; session_id: string; turn?: number; run_id?: string; node_id?: string; kind?: string; status?: string; message?: string; created_at: string; details?: Record<string, unknown> }
export interface Variable { name: string; description: string; kind: string; version: number; row_count?: number | null; created_at?: string; columns?: string[]; metadata?: Record<string, unknown>; [key: string]: unknown }
export interface TableData { columns: (string | { name: string; type?: string })[]; rows: Record<string, unknown>[]; total?: number; total_rows?: number; row_count?: number; page?: number; version?: number; data?: unknown; available?: boolean; complete?: boolean; error?: string; source_version?: number }
export interface SessionDetail { session: Session; messages: Message[]; plan?: Plan | null; plans: Plan[]; events: ExecutionEvent[]; variables: Variable[]; runs?: unknown[] }
export interface Issue { id: string; title: string; description: string; status: string; owner?: string; due_date?: string; session_ids?: string[]; hypotheses?: string[]; evidence?: string[]; actions?: string[]; verification?: string; created_at?: string; updated_at?: string; history?: unknown[] }
export interface Skill { id: string; name: string; description: string; version?: string; tools?: string[]; category?: string }
export interface Settings { mode: string; model: { model: string; base_url: string; api_key_set?: boolean; timeout?: number; api_key?: string; driver?: string }; max_parallel_sessions?: number; connectors?: Record<string, { driver: string }>; rules?: Record<string, unknown> }
export interface Health { status?: string; mode?: string; version?: string; [key: string]: unknown }
export interface ChartData { data: unknown[]; layout?: Record<string, unknown> }
