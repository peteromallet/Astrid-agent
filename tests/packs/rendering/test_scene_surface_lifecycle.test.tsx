// @vitest-environment jsdom
import {StrictMode} from 'react';
import {render,cleanup,fireEvent,act,waitFor} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {SceneSurface} from '../../../astrid/packs/rendering/ui/live-scenes/SceneSurface';
import * as sceneRuntime from '../../../astrid/packs/rendering/ui/live-scenes/runtime';

async function sha256(bytes: Uint8Array): Promise<string> {
  const hash = await globalThis.crypto.subtle.digest('SHA-256', new Uint8Array(bytes));
  return `sha256:${Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')}`;
}

async function makeSource({html='<html><head></head><body></body></html>', duration=100, trace=false}: {
  html?: string; duration?: number; trace?: boolean;
} = {}) {
  const entryBytes = new TextEncoder().encode(html);
  const entry = {
    object_id: 'entry-object',
    digest: await sha256(entryBytes),
    media_type: 'text/html',
    size: entryBytes.byteLength,
    filename: 'scene.html',
  };
  const manifest = {formatVersion:1 as const,entry:'scene.html',duration,authoredFps:30};
  const packageBody = JSON.stringify({manifest,entry,assets:[]});
  const revision = await sha256(new TextEncoder().encode(packageBody));
  return {
    revision,
    source: {objectId:'package-object',revision},
    packageBody,
    html,
    ...(trace ? {__l1bTrace:true} : {}),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((ok, fail) => { resolve=ok; reject=fail; });
  return {promise,resolve,reject};
}

function ownedChildren() {
  const children=new Map<HTMLIFrameElement,{postMessage:ReturnType<typeof vi.fn>}>();
  vi.spyOn(HTMLIFrameElement.prototype,'contentWindow','get').mockImplementation(function(this:HTMLIFrameElement) {
    if (!children.has(this)) children.set(this,{postMessage:vi.fn()});
    return children.get(this)! as unknown as Window;
  });
  const messages=(iframe:HTMLIFrameElement):Record<string,unknown>[]=>children.get(iframe)?.postMessage.mock.calls.map(([message])=>message) ?? [];
  const mounted=async(container:HTMLElement,selector='iframe')=>{
    let iframe!:HTMLIFrameElement;
    await waitFor(()=>{
      iframe=container.querySelector<HTMLIFrameElement>(selector)!;
      expect(iframe).not.toBeNull();
      expect(messages(iframe).some(message=>message.kind==='initialize')).toBe(true);
    });
    return iframe;
  };
  const ack=(iframe:HTMLIFrameElement,message:Record<string,unknown>)=>act(()=>{
    const identity=messages(iframe)[0];
    window.dispatchEvent(new MessageEvent('message',{source:children.get(iframe)! as unknown as Window,data:{...identity,...message}}));
  });
  const initialize=(iframe:HTMLIFrameElement)=>ack(iframe,{kind:'initialized'});
  const ready=(iframe:HTMLIFrameElement)=>ack(iframe,{...messages(iframe).filter(message=>message.kind==='render').at(-1),kind:'frame'});
  return {children,messages,mounted,ack,initialize,ready};
}


afterEach(()=>{cleanup();vi.restoreAllMocks();vi.clearAllTimers();vi.useRealTimers();});
function transition(kind: 'pagehide' | 'pageshow', persisted: boolean) {
  act(()=>window.dispatchEvent(new PageTransitionEvent(kind,{persisted})));
}
describe('BFCache transport recovery',()=>{
  it('retires suspended transport, rejects old frames and restores a fresh document at the latest frame',async()=>{
    const source=await makeSource(), t=ownedChildren(), onStatus=vi.fn();
    const show=(time:number,width=640)=><SceneSurface source={source} sourceTime={time} width={width} height={360} onStatus={onStatus}/>;
    const view=render(show(2));
    const old=await t.mounted(view.container);t.initialize(old);t.ready(old);
    expect(view.container.firstChild).toHaveAttribute('data-source-time','2.000');
    transition('pagehide',true);
    const sent=t.messages(old).length, reports=onStatus.mock.calls.length;
    view.rerender(show(5,800));t.ack(old,{kind:'error',error:'obsolete child'});t.ready(old);
    expect(t.messages(old)).toHaveLength(sent);expect(onStatus).toHaveBeenCalledTimes(reports);
    transition('pageshow',true);
    expect(view.container.firstChild).toHaveAttribute('data-phase','loading');
    expect(view.container.firstChild).not.toHaveAttribute('data-source-time');
    const fresh=await t.mounted(view.container);
    expect(fresh).not.toBe(old);expect(fresh.getAttribute('data-scene-instance')).not.toBe(old.getAttribute('data-scene-instance'));
    t.initialize(fresh);
    expect(t.messages(fresh).at(-1)).toMatchObject({kind:'render',sourceTime:5,width:800,height:360});
    t.ready(old);expect(view.container.firstChild).toHaveAttribute('data-phase','rendering');
    t.ready(fresh);expect(view.container.firstChild).toHaveAttribute('data-source-time','5.000');
    expect(onStatus).toHaveBeenLastCalledWith(expect.objectContaining({phase:'ready',sourceTime:5,width:800}));
    view.unmount();const final=t.messages(fresh).length;
    transition('pageshow',true);expect(t.messages(fresh)).toHaveLength(final);
  });
  it('fences integrity work completed while suspended or after a restore generation',async()=>{
    const source=await makeSource(), gates=[deferred<void>(),deferred<void>()];
    const verify=vi.spyOn(sceneRuntime,'verifyScenePackageIntegrity').mockImplementationOnce(()=>gates[0].promise).mockImplementationOnce(()=>gates[1].promise);
    const view=render(<SceneSurface source={source} sourceTime={3} width={10} height={10}/>);
    expect(verify).toHaveBeenCalledTimes(1);
    transition('pagehide',true);
    await act(async()=>gates[0].resolve());expect(view.container.querySelector('iframe')).toBeNull();
    transition('pageshow',true);expect(verify).toHaveBeenCalledTimes(2);
    transition('pagehide',true);transition('pageshow',true);
    await act(async()=>gates[1].resolve());
    await waitFor(()=>expect(verify).toHaveBeenCalledTimes(3));
    // The current generation alone may mount a document.
    await waitFor(()=>expect(view.container.querySelectorAll('iframe')).toHaveLength(1));
    expect(view.container.querySelector('iframe')).toHaveAttribute('data-scene-role','candidate');
  });
  it('does not reset a live document for nonpersisted page transitions',async()=>{
    const source=await makeSource(),t=ownedChildren();
    const view=render(<SceneSurface source={source} sourceTime={1} width={10} height={10}/>);
    const owned=await t.mounted(view.container);t.initialize(owned);t.ready(owned);
    transition('pageshow',false);
    expect(view.container.querySelector('iframe')).toBe(owned);
    expect(view.container.firstChild).toHaveAttribute('data-phase','ready');
  });
});
