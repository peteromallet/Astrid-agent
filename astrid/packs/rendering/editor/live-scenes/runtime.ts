export type ProjectObjectMetadata = {
  readonly object_id: string;
  readonly digest: string;
  readonly media_type: string;
  readonly size: number;
  readonly filename?: string;
};
export type SceneManifest = {
  readonly formatVersion: 1;
  readonly entry: string;
  readonly duration: number;
  readonly authoredFps: number;
};

/** The only inline-source cache envelope. packageBody is the exact UTF-8 JSON
 * text of the immutable package object; html is a derived entry mirror. */
export type ScenePackageEnvelope = {
  readonly revision: string;
  readonly source: { readonly objectId: string; readonly revision: string };
  readonly packageBody: string;
  readonly html: string;
};
/** A frozen structural view produced by validateScenePackage. It does not add
 * another serialized package object to the envelope. */
export type ScenePackage = ScenePackageEnvelope;
export type ValidatedScenePackage = {
  readonly package: ScenePackage;
  readonly manifest: SceneManifest;
  readonly entry: ProjectObjectMetadata;
  readonly assets: readonly ProjectObjectMetadata[];
  readonly __l1bTrace?: boolean;
};
export type SceneFrame = { sourceTime: number; width: number; height: number };
export type SceneStatus = {
  phase: 'loading' | 'rendering' | 'ready' | 'error' | 'disposed';
  revision: string;
  requestId?: number;
  sourceTime?: number;
  error?: string;
};

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;
const ENTRY_NAME = /^[\w./-]+\.html$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function requireMetadata(value: unknown, label: string): ProjectObjectMetadata {
  if (!isRecord(value)
    || typeof value.object_id !== 'string' || !value.object_id
    || typeof value.digest !== 'string' || !SHA256_DIGEST.test(value.digest)
    || typeof value.media_type !== 'string' || !value.media_type
    || typeof value.size !== 'number' || !Number.isSafeInteger(value.size) || value.size < 0
    || (value.filename !== undefined && (typeof value.filename !== 'string' || !value.filename))) {
    throw Error(`Invalid ${label} project object metadata`);
  }
  return Object.freeze({
    object_id: value.object_id,
    digest: value.digest,
    media_type: value.media_type,
    size: value.size,
    ...(value.filename === undefined ? {} : { filename: value.filename }),
  });
}

function parsePackageBody(packageBody: string): {
  manifest: SceneManifest;
  entry: ProjectObjectMetadata;
  assets: readonly ProjectObjectMetadata[];
} {
  let body: unknown;
  try { body = JSON.parse(packageBody); }
  catch { throw Error('Scene packageBody is not valid JSON'); }
  if (!isRecord(body)) throw Error('Scene packageBody must be an object');
  const manifest = body.manifest;
  if (!isRecord(manifest) || manifest.formatVersion !== 1) throw Error('Unsupported scene package format');
  if (typeof manifest.entry !== 'string' || !ENTRY_NAME.test(manifest.entry)
    || manifest.entry.split('/').includes('..')) throw Error('Invalid prepared HTML entry');
  if (typeof manifest.duration !== 'number' || !Number.isFinite(manifest.duration) || manifest.duration <= 0
    || typeof manifest.authoredFps !== 'number' || !Number.isFinite(manifest.authoredFps) || manifest.authoredFps <= 0) {
    throw Error('Invalid scene timing');
  }
  if (!Array.isArray(body.assets)) throw Error('Scene package assets must be an array');
  const entry = requireMetadata(body.entry, 'entry');
  const assets = Object.freeze(body.assets.map((asset, index) => requireMetadata(asset, `asset[${index}]`)));
  return {
    manifest: Object.freeze({
      formatVersion: 1,
      entry: manifest.entry,
      duration: manifest.duration,
      authoredFps: manifest.authoredFps,
    }),
    entry,
    assets,
  };
}

export function validateScenePackage(value: unknown): ValidatedScenePackage {
  if (!isRecord(value)
    || typeof value.revision !== 'string' || !SHA256_DIGEST.test(value.revision)
    || !isRecord(value.source)
    || typeof value.source.objectId !== 'string' || !value.source.objectId
    || typeof value.source.revision !== 'string' || !SHA256_DIGEST.test(value.source.revision)
    || typeof value.packageBody !== 'string'
    || typeof value.html !== 'string' || !value.html.trim()) {
    throw Error('Missing scene source/revision');
  }
  const parsed = parsePackageBody(value.packageBody);
  return Object.freeze({
    package: Object.freeze({
      revision: value.revision,
      source: Object.freeze({ objectId: value.source.objectId, revision: value.source.revision }),
      packageBody: value.packageBody,
      html: value.html,
    }),
    ...parsed,
    ...(value.__l1bTrace === true ? { __l1bTrace: true } : {}),
  });
}

function sha256Text(value: string): Promise<string> {
  return crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)).then(hash => (
    `sha256:${Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')}`
  ));
}

export async function verifyScenePackageIntegrity(source: ValidatedScenePackage): Promise<void> {
  if (source.package.revision !== source.package.source.revision) throw Error('Scene package/source revision mismatch');
  const packageDigest = await sha256Text(source.package.packageBody);
  if (source.package.revision !== packageDigest) throw Error('Scene packageBody digest mismatch');
  const entryDigest = await sha256Text(source.package.html);
  const entrySize = new TextEncoder().encode(source.package.html).byteLength;
  if (source.entry.digest !== entryDigest) throw Error('Scene entry digest mismatch');
  if (source.entry.size !== entrySize) throw Error('Scene entry size mismatch');
}

/** The host installs transport only. Scene code supplies initialize/render/dispose.
 * Opaque-origin sandbox + a no-network CSP prevents access to the parent's
 * credentials. This is deliberately not a general untrusted-JavaScript sandbox. */
export function sceneDocument(source: ValidatedScenePackage, instance: string): string {
  const identity = JSON.stringify({ protocol: 'astrid.scene/1', instance, revision: source.package.revision }).replace(/</g, '\\u003c');
  const diagnostic = source.package.html.includes('<!-- astrid-l1b-diagnostic -->') || source.__l1bTrace === true;
  // Local proof instrumentation only: count native method invocations from
  // construction onward without changing the method's behavior or source hash.
  let entryHtml = source.package.html;
  if (diagnostic) entryHtml = entryHtml.replace(
    "const renderer=new T.WebGLRenderer({antialias:true,powerPreference:'high-performance',preserveDrawingBuffer:true});",
    `const renderer=new T.WebGLRenderer({antialias:true,powerPreference:'high-performance',preserveDrawingBuffer:true});
    window.__l1bNativeRenderCalls=[];
    const nativeRender=renderer.render;
    renderer.render=function(...args){const record={...${identity},time:Date.now(),stage:'native-render-call',requestId:window.__l1bHostRequestId,count:window.__l1bNativeRenderCalls.length+1,sceneName:args[0]?.name,stack:new Error().stack};window.__l1bNativeRenderCalls.push(record);console.warn('[L1b scene trace] '+JSON.stringify(record));return nativeRender.apply(this,args);};`,
  );
  const bridge = `<script>(()=>{
    const identity=${identity}; let disposed=false; let chain=Promise.resolve();
    const diagnostic=${diagnostic};
    const trace=(stage,detail={})=>{if(diagnostic){const record={owner:{...identity,document:identity.instance},time:Date.now(),stage,detail};(window.__l1bChildTrace??=[]).push(record);console.warn('[L1b scene trace] '+JSON.stringify(record));parent.postMessage({protocol:'astrid.scene/trace',record},'*');}};
    trace('child-bridge-boot');
    let snapshot='';
    const inspect=()=>{const state={boot:window.__mapleBoot,assets:window.__assetsReady,journey:window.__journeyReady,sceneError:window.__sceneError,fontStatus:document.fonts.status,nativeDrawCalls:window.MapleHollow?.renderer.info.render.calls,wrappedNativeCalls:window.__l1bNativeRenderCalls?.length,images:Array.from(document.images).map(i=>({complete:i.complete,naturalWidth:i.naturalWidth}))};const next=JSON.stringify(state);if(next!==snapshot){snapshot=next;trace('child-readiness-state',state);}};
    const diagnosticTimer=diagnostic?setInterval(inspect,1000):undefined;
    if(diagnostic){inspect();setTimeout(()=>clearInterval(diagnosticTimer),30000);}
    const send=(value)=>{trace('child-post',value);parent.postMessage({...identity,...value},'*');};
    const initialized=Promise.resolve().then(async()=>{
      trace('child-initialize-start');
      if(!window.astridScene)throw Error('Entry must define window.astridScene');
      await window.astridScene.initialize();
      trace('child-maple-initialize-complete');
      await document.fonts.ready;
      trace('child-fonts-ready');
      await Promise.all(Array.from(document.images).map(img=>img.decode()));
      trace('child-images-ready');
    });
    initialized.catch(()=>{});
    addEventListener('message',event=>{
      const m=event.data;
      trace('child-message-received',{kind:m?.kind,requestId:m?.requestId,sourceTime:m?.sourceTime,receivedInstance:m?.instance,receivedRevision:m?.revision,sourceIsParent:event.source===parent});
      const reason=event.source!==parent?'source':!m?'payload':m.protocol!==identity.protocol?'protocol':m.instance!==identity.instance?'instance':m.revision!==identity.revision?'revision':null;
      if(reason){trace('child-message-rejected',{reason,message:m});return;}
      trace('child-message-accepted',{message:m});
      if(m.kind==='initialize'){
        initialized.then(()=>{if(!disposed)send({kind:'initialized'});}).catch(e=>{if(!disposed)send({kind:'error',error:String(e.message||e)});});
        return;
      }
      if(m.kind==='dispose'){disposed=true;chain.finally(()=>window.astridScene?.dispose());return;}
      if(m.kind!=='render'||disposed)return;
      chain=chain.then(async()=>{
        await initialized;if(disposed)return;
        if(diagnostic)window.__l1bHostRequestId=m.requestId;
        trace('child-requested-draw-entry',{requestId:m.requestId,sourceTime:m.sourceTime});
        await window.astridScene.render({sourceTime:m.sourceTime,width:m.width,height:m.height});
        trace('child-gl-complete',{requestId:m.requestId,sourceTime:m.sourceTime,nativeDrawCalls:window.MapleHollow?.renderer.info.render.calls});
        if(!disposed)send({kind:'frame',requestId:m.requestId,sourceTime:m.sourceTime});
      }).catch(e=>{if(!disposed)send({kind:'error',requestId:m.requestId,error:String(e.message||e)});});
    });
    trace('child-listener-installed');
    addEventListener('pagehide',()=>{trace('child-pagehide');disposed=true;window.astridScene?.dispose();});
  })();</script>`;
  const csp = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; connect-src 'none'; media-src 'none'">`;
  return entryHtml.replace(/<head([^>]*)>/i, `<head$1>${csp}`).replace(/<\/body>/i, `${bridge}</body>`);
}

/** One bounded transport per clip instance. Preview coalesces obsolete frames;
 * exports wait for their exact frame via the same callback. */
export class SceneFrameHost {
  private serial = 0;
  private initialized = false;
  private disposed = false;
  private failed = false;
  private pending?: SceneFrame;
  private active?: SceneFrame & { requestId: number };
  private latest?: SceneFrame;
  private timer?: ReturnType<typeof setTimeout>;
  private initializeRetry?: ReturnType<typeof setInterval>;
  private retryCount = 0;
  constructor(
    readonly source: ValidatedScenePackage,
    readonly instance: string,
    private readonly send: (message: Record<string, unknown>) => void,
    private readonly report: (status: SceneStatus) => void,
    private readonly timeoutMs = 30000,
    private readonly trace?: (stage: string, detail: Record<string, unknown>) => void,
  ) { this.report({ phase: 'loading', revision: source.package.revision }); this.arm(); }
  initialize() {
    if (this.disposed || this.failed || this.initialized) return;
    // Own the interval before sending: a synchronous acknowledgement may
    // clear it. Repeated hellos never restart the constructor's deadline.
    if (this.initializeRetry === undefined) { this.trace?.('host-retry-start', { interval: 100 }); this.initializeRetry = setInterval(() => { this.trace?.('host-retry-tick', { count: ++this.retryCount }); this.initialize(); }, 100); }
    this.send({ protocol: 'astrid.scene/1', instance: this.instance, revision: this.source.package.revision, kind: 'initialize' });
  }
  private stopInitializeRetry(reason: string) {
    this.trace?.('host-retry-stop', { reason, count: this.retryCount, armed: this.initializeRetry !== undefined });
    clearInterval(this.initializeRetry);
    this.initializeRetry = undefined;
  }
  private arm() {
    clearTimeout(this.timer);
    const deadline = Date.now() + this.timeoutMs;
    this.trace?.('host-timeout-arm', { deadline, timeoutMs: this.timeoutMs, active: this.active });
    this.timer = setTimeout(() => { this.trace?.('host-timeout-fire', { deadline, active: this.active }); this.fail(this.active ? `Scene frame ${this.active.sourceTime}s timed out` : 'Scene initialization timed out'); }, this.timeoutMs);
  }
  private fail(error: string) {
    this.failed = true;
    this.stopInitializeRetry('failure');
    this.trace?.('host-terminal-failure', { error });
    clearTimeout(this.timer);
    this.report({ phase: 'error', revision: this.source.package.revision, error });
  }
  request(frame: SceneFrame) {
    if (this.disposed || this.failed) return;
    if (![frame.sourceTime, frame.width, frame.height].every(Number.isFinite)
      || frame.sourceTime < 0 || frame.sourceTime > this.source.manifest.duration
      || frame.width <= 0 || frame.height <= 0) { this.fail('Invalid requested scene frame'); return; }
    this.latest = frame;
    this.pending = frame;
    this.flush();
  }
  private flush() {
    if (!this.initialized || this.active || !this.pending || this.disposed) return;
    this.active = { ...this.pending, requestId: ++this.serial };
    this.pending = undefined;
    this.report({ phase: 'rendering', revision: this.source.package.revision, ...this.active });
    this.arm();
    this.send({ protocol: 'astrid.scene/1', instance: this.instance, revision: this.source.package.revision, kind: 'render', ...this.active });
  }
  receive(message: Record<string, unknown>) {
    const reason = this.disposed ? 'disposed' : this.failed ? 'failed' : message.protocol !== 'astrid.scene/1' ? 'protocol' : message.instance !== this.instance ? 'instance' : message.revision !== this.source.package.revision ? 'revision' : null;
    if (reason) { this.trace?.('host-message-rejected', { reason, message }); return; }
    if (message.kind === 'initialized') { if (this.initialized) { this.trace?.('host-message-rejected', { reason: 'duplicate-initialized', message }); return; } this.trace?.('host-message-accepted', { message }); this.initialized = true; this.stopInitializeRetry('initialized'); clearTimeout(this.timer); this.trace?.('host-timeout-clear', { reason: 'initialized' }); this.flush(); return; }
    if (message.kind === 'error') {
      if (message.requestId !== undefined && message.requestId !== this.active?.requestId) return;
      this.fail(String(message.error)); this.active = undefined; return;
    }
    if (message.kind !== 'frame' || !this.active || message.requestId !== this.active.requestId || message.sourceTime !== this.active.sourceTime) { this.trace?.('host-message-rejected', { reason: 'kind/request/time', message, active: this.active }); return; }
    this.trace?.('host-message-accepted', { message });
    clearTimeout(this.timer);
    this.trace?.('host-timeout-clear', { reason: 'frame' });
    const frame = this.active;
    this.active = undefined;
    if (this.latest?.sourceTime === frame.sourceTime && this.latest.width === frame.width && this.latest.height === frame.height) {
      this.report({ phase: 'ready', revision: this.source.package.revision, ...frame });
    }
    this.flush();
  }
  dispose(disposeChild = true) {
    if (this.disposed) return;
    this.disposed = true; this.stopInitializeRetry('dispose'); clearTimeout(this.timer); this.trace?.('host-timeout-clear', { reason: 'dispose' });
    if (disposeChild) this.send({ protocol: 'astrid.scene/1', instance: this.instance, revision: this.source.package.revision, kind: 'dispose' });
    this.report({ phase: 'disposed', revision: this.source.package.revision });
  }
}
