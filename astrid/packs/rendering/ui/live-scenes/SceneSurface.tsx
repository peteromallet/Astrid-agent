import { createElement, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  SceneFrameHost,
  sceneDocument,
  validateScenePackage,
  verifyScenePackageIntegrity,
  type ValidatedScenePackage,
  type SceneFrame,
  type SceneStatus,
} from './runtime';
import { sceneTrace, traceId } from './diagnostics';

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function suppliedRevision(value: unknown): string {
  return typeof value === 'object' && value !== null && 'revision' in value && typeof value.revision === 'string'
    ? value.revision
    : '';
}

type SceneRequest = { pkg?: ValidatedScenePackage; error?: string; revision: string };
type SceneDocument = {
  request: SceneRequest;
  pkg: ValidatedScenePackage;
  instance: string;
  html: string;
};

// Each keyed document owns its transport. Moving a candidate to the displayed
// role must not navigate its iframe or restart initialization.
function SceneInstance({ document, component, frame, visible, width, height, onStatus }: {
  document: SceneDocument; component: object; frame?: SceneFrame; visible: boolean;
  width: number; height: number; onStatus: (document: SceneDocument, status: SceneStatus) => void;
}) {
  const iframe = useRef<HTMLIFrameElement>(null);
  const host = useRef<SceneFrameHost>();
  const callback = useRef(onStatus); callback.current = onStatus;
  const desiredFrame = useRef(frame); desiredFrame.current = frame;
  const diagnostic = document.pkg.package.html.includes('<!-- astrid-l1b-diagnostic -->') || document.pkg.__l1bTrace === true;
  useEffect(() => {
    // Resolve the WindowProxy on each retry from this generation's element,
    // never from another candidate's ref.
    const frameElement = iframe.current;
    let active = true;
    const effectOwner = diagnostic ? {
      component: traceId(component, 'component'), source: traceId(document.pkg, 'source'),
      document: document.instance, revision: document.pkg.package.revision,
      effect: traceId({}, 'effect'), host: crypto.randomUUID(), listener: crypto.randomUUID(),
      iframe: frameElement && traceId(frameElement, 'iframe'),
    } : {};
    const effectTrace = (stage: string, detail: Record<string, unknown> = {}) => sceneTrace(diagnostic, effectOwner, stage, detail);
    effectTrace('parent-effect-setup', { sourceTime: desiredFrame.current?.sourceTime, connected: frameElement?.isConnected });
    const instance = new SceneFrameHost(document.pkg, document.instance,
      message => {
        const frameWindow = frameElement?.contentWindow;
        const detail = { message, connected: frameElement?.isConnected, endpoint: frameWindow && diagnostic ? traceId(frameWindow, 'window') : null };
        if (!frameWindow) { effectTrace('parent-send-skipped', detail); return; }
        try { frameWindow.postMessage(message, '*'); effectTrace('parent-send-posted', detail); }
        catch (error) { effectTrace('parent-send-threw', { ...detail, error: String(error) }); throw error; }
      },
      value => {
        effectTrace('parent-status', value);
        if (active && value.phase !== 'disposed') callback.current(document, value);
      }, 30000, diagnostic ? effectTrace : undefined);
    host.current = instance;
    const receive = (event: MessageEvent) => {
      const frameWindow = frameElement?.contentWindow;
      const matches = !!frameWindow && event.source === frameWindow;
      effectTrace('parent-message-received', { message: event.data, sourceMatches: matches, connected: frameElement?.isConnected, endpoint: frameWindow && diagnostic ? traceId(frameWindow, 'window') : null });
      if (matches && event.data?.protocol === 'astrid.scene/trace') { effectTrace('child-record', { record: event.data.record }); return; }
      if (matches && event.data && typeof event.data === 'object') instance.receive(event.data);
      else effectTrace('parent-message-rejected', { reason: 'endpoint/source/payload' });
    };
    window.addEventListener('message', receive);
    effectTrace('parent-listener-installed');
    instance.initialize();
    if (desiredFrame.current) instance.request(desiredFrame.current);
    // StrictMode can replay effects while the document survives. Stop this
    // transport only; removal/navigation's pagehide owns scene disposal.
    return () => {
      active = false;
      effectTrace('parent-effect-cleanup'); instance.dispose(false);
      window.removeEventListener('message', receive); effectTrace('parent-listener-removed');
      if (host.current === instance) host.current = undefined;
    };
  }, [document, component, diagnostic]);
  useLayoutEffect(() => {
    if (desiredFrame.current) host.current?.request(desiredFrame.current);
  }, [frame?.sourceTime, frame?.width, frame?.height]);
  return createElement('iframe', {
    ref: iframe, title: `Live scene ${document.pkg.package.revision}`, sandbox: 'allow-scripts',
    'data-scene-instance': document.instance, 'data-scene-role': visible ? 'displayed' : 'candidate',
    onLoad: () => {
      sceneTrace(diagnostic, { component: traceId(component, 'component'), document: document.instance, revision: document.pkg.package.revision }, 'parent-iframe-load');
      host.current?.initialize();
    },
    srcDoc: document.html,
    // Hidden candidates still have a real viewport for initialization/rendering.
    style: { position: 'absolute', inset: 0, width, height, border: 0, display: 'block', visibility: visible ? 'visible' : 'hidden', pointerEvents: 'none' },
  });
}

export function SceneSurface({ source, sourceTime, width, height, onStatus, showStatus = true }: {
  source: unknown; sourceTime: number; width: number; height: number;
  onStatus?: (status: SceneStatus) => void; showStatus?: boolean;
}) {
  const requested = useMemo<SceneRequest>(() => {
    try {
      const pkg = validateScenePackage(source);
      return { pkg, revision: pkg.package.revision };
    } catch (error) { return { error: messageOf(error), revision: suppliedRevision(source) }; }
  }, [source]);
  // Fence completion immediately when props change, including before effects
  // clean up an obsolete integrity check or host.
  const desired = useRef(requested); desired.current = requested;
  const frame = useRef({ sourceTime, width, height }); frame.current = { sourceTime, width, height };
  const component = useRef({});
  const callback = useRef(onStatus); callback.current = onStatus;
  const [status, setStatus] = useState<SceneStatus>(() => ({
    phase: requested.error ? 'error' : 'loading', revision: requested.revision,
    ...(requested.error ? { error: requested.error } : {}),
  }));
  const [displayed, setDisplayed] = useState<SceneDocument>();
  const [candidate, setCandidate] = useState<SceneDocument>();
  const [displayedStatus, setDisplayedStatus] = useState<SceneStatus>();
  useEffect(() => {
    let active = true;
    const report = (value: SceneStatus) => {
      if (!active || desired.current !== requested) return;
      setStatus(value); callback.current?.(value);
    };
    setCandidate(undefined);
    if (!requested.pkg) {
      report({ phase: 'error', revision: requested.revision, error: requested.error ?? 'Invalid scene package' });
    } else {
      const pkg = requested.pkg;
      report({ phase: 'loading', revision: requested.revision });
      verifyScenePackageIntegrity(pkg).then(() => {
        if (!active || desired.current !== requested) return;
        const instance = crypto.randomUUID();
        setCandidate({ request: requested, pkg, instance, html: sceneDocument(pkg, instance) });
      }).catch(error => report({ phase: 'error', revision: requested.revision, error: messageOf(error) }));
    }
    return () => { active = false; };
  }, [requested]);
  const receiveStatus = (document: SceneDocument, value: SceneStatus) => {
    if (document.request !== desired.current) return;
    if (value.phase === 'ready') {
      // SceneFrameHost reports the complete acknowledged frame. Recheck against
      // props as well as its request fence, since props may precede its effect.
      const ready = value as SceneStatus & SceneFrame;
      const current = frame.current;
      if (ready.sourceTime !== current.sourceTime || ready.width !== current.width || ready.height !== current.height) return;
      setDisplayed(document); setDisplayedStatus(value);
      setCandidate(previous => previous === document ? undefined : previous);
    } else if (value.phase === 'error') {
      setCandidate(previous => previous === document ? undefined : previous);
    }
    setStatus(value); callback.current?.(value);
  };
  const currentCandidate = candidate?.request === requested ? candidate : undefined;
  const documents = [displayed, currentCandidate].filter((document): document is SceneDocument => !!document);
  const diagnostic = requested.pkg?.package.html.includes('<!-- astrid-l1b-diagnostic -->') || requested.pkg?.__l1bTrace === true;
  return createElement('div', {
    'data-testid': 'astrid-live-scene-entry', 'data-phase': status.phase,
    'data-desired-time': sourceTime.toFixed(3), 'data-source-time': displayedStatus?.sourceTime?.toFixed(3),
    'data-revision': requested.revision, 'data-desired-revision': requested.revision, 'data-displayed-revision': displayedStatus?.revision,
    ...(diagnostic ? { 'data-document': (currentCandidate ?? displayed)?.instance, 'data-request-id': status.requestId, 'data-exact-time': status.sourceTime, 'data-component': traceId(component.current, 'component') } : {}),
    style: { position: 'relative', width, height, overflow: 'hidden', background: '#111' },
  }, ...documents.map(document => createElement(SceneInstance, {
    key: document.instance, document, component: component.current,
    frame: document.request === requested ? frame.current : undefined,
    visible: document === displayed, width, height, onStatus: receiveStatus,
  })), showStatus ? createElement('div', {
    style: { position: 'absolute', left: 8, top: 8, background: '#000b', color: 'white', font: '12px monospace', padding: 6 },
  }, `${status.phase} · displayed ${displayedStatus?.revision ?? '…'} · requested ${requested.revision} · source ${displayedStatus?.sourceTime?.toFixed(3) ?? '…'}s · requested ${sourceTime.toFixed(3)}s${status.error ? ` · ${status.error}` : ''}`) : null);
}
