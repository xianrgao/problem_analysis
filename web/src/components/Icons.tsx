import { Activity } from 'lucide-react';
export function BrandMark({ small = false }: { small?: boolean }) { return <span className={`brand-mark ${small ? 'small' : ''}`}><Activity size={small ? 17 : 22} strokeWidth={1.8}/></span>; }
