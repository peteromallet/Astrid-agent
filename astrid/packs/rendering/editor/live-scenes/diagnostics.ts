/** Local L1b evidence only. The ledger survives component cleanup and HMR. */
export type TraceOwner = Record<string, unknown>;
type Ledger = { records: unknown[]; ids: WeakMap<object, string> };
function ledger(): Ledger {
  const root = globalThis as typeof globalThis & { __astridL1bLedger?: Ledger };
  return root.__astridL1bLedger ??= { records: [], ids: new WeakMap() };
}
export function traceId(value: object, label: string): string {
  const state = ledger();
  let id = state.ids.get(value);
  if (!id) { id = `${label}:${crypto.randomUUID()}`; state.ids.set(value, id); }
  return id;
}
export function sceneTrace(enabled: boolean, owner: TraceOwner, stage: string, detail: unknown = {}) {
  if (!enabled) return;
  const record = { time: Date.now(), stage, owner: { ...owner }, detail };
  ledger().records.push(record);
  if (typeof document !== 'undefined') {
    let node = document.getElementById('astrid-l1b-trace-ledger');
    if (!node) { node = document.createElement('script'); node.id = 'astrid-l1b-trace-ledger'; node.setAttribute('type', 'application/json'); document.body.appendChild(node); }
    node.textContent = JSON.stringify(ledger().records);
  }
  console.warn('[L1b scene trace] ' + JSON.stringify(record));
}
