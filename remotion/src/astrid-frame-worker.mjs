import {
  chmodSync, copyFileSync, existsSync, linkSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, unlinkSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import net from 'node:net';
import readline from 'node:readline';
import { ensureBrowser, openBrowser, renderFrames, selectComposition } from '@remotion/renderer';

let browser = null;
let bundleDir = null;
let projectDir = null;
// Lines for the caller's findings (a cold-browser retry); sent with the next reply.
let notes = [];

// Remotion's openBrowser gives Chrome a fixed 25 s to connect. The first launch
// after a host restart or reboot can miss it while macOS verifies and pages in
// the binary. Retry once: warm the binary first (`--version`, bounded), then
// connect again, so a cold start costs one wait instead of a failed capture.
const CONNECT_TIMEOUT = /Timed out after \d+ ms while trying to connect to the browser/;
export const WARM_TIMEOUT_MS = 90_000;

async function connectBrowser(request, open = openBrowser, warm = warmBrowser) {
  const options = {
    browserExecutable: request.browserExecutable ?? null,
    chromiumOptions: { headless: true },
    logLevel: 'error',
  };
  const started = Date.now();
  try {
    return await open('chrome', options);
  } catch (error) {
    if (!CONNECT_TIMEOUT.test(error instanceof Error ? error.message : String(error))) throw error;
    const first = Math.round((Date.now() - started) / 1000);
    await warm(options.browserExecutable);
    const retried = Date.now();
    let connected;
    try {
      connected = await open('chrome', options);
    } catch (retryError) {
      const reason = retryError instanceof Error ? retryError.message : String(retryError);
      throw new Error(`Chrome did not connect (cold browser): ${first} s, then warmed and retried once: ${reason.split('\n')[0]}`);
    }
    notes.push(`CAPTURE  cold browser: Chrome missed the ${first} s connect limit; warmed it and retried once, connected in ${Math.round((Date.now() - retried) / 1000)} s`);
    return connected;
  }
}

async function warmBrowser(browserExecutable) {
  try {
    const status = await ensureBrowser({ browserExecutable, logLevel: 'error' });
    if (status && status.path) spawnSync(status.path, ['--version'], { stdio: 'ignore', timeout: WARM_TIMEOUT_MS });
  } catch {
    // Warming is best effort; the retry still runs.
  }
}

export const _test = { connectBrowser, takeNotes: () => notes.splice(0), linkOverlay, unlinkOverlay };

function reply(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

async function closeSession() {
  if (browser) {
    try {
      await browser.close();
    } catch {
      // The parent process owns the final process-group fallback.
    }
  }
  browser = null;
  if (bundleDir) rmSync(bundleDir, { recursive: true, force: true });
  bundleDir = null;
  projectDir = null;
}

function removeLaunchdJob(socketPath) {
  if (process.platform !== 'darwin') return;
  const name = socketPath.split('/').pop() ?? '';
  if (!name.startsWith('owner-') || !name.endsWith('.sock')) return;
  const label = `astrid-rfo-${name.slice('owner-'.length, -'.sock'.length)}`;
  spawnSync('/bin/launchctl', ['remove', label], { stdio: 'ignore' });
}

async function startSession(request) {
  await closeSession();
  projectDir = resolve(request.projectDir);
  if (!existsSync(projectDir)) throw new Error(`Remotion project does not exist: ${projectDir}`);
  await bundleProject(request);
  browser = await connectBrowser(request);
}

async function bundleProject(request) {
  if (bundleDir) rmSync(bundleDir, { recursive: true, force: true });
  const workerRoot = resolve(process.env.ASTRID_FRAME_WORKER_ROOT ?? tmpdir());
  // The owner is deliberately process-independent from GenericPackHost.  The
  // host may therefore clean up a root it created after launching us while we
  // are still retained for the next request; recreate the bounded root at the
  // point of use rather than assuming the launcher's directory still exists.
  mkdirSync(workerRoot, { recursive: true, mode: 0o700 });
  bundleDir = mkdtempSync(join(workerRoot, 'astrid-remotion-frame-bundle-'));
  const environment = { ...process.env, ...(request.environment ?? {}) };
  const bundled = spawnSync(
    request.nodeExecutable,
    [request.remotionCli, 'bundle', 'src/index.ts', '--out-dir', bundleDir, '--bundle-cache=false'],
    { cwd: projectDir, env: environment, encoding: 'utf8', stdio: ['ignore', 'ignore', 'pipe'] },
  );
  if (bundled.status !== 0) {
    rmSync(bundleDir, { recursive: true, force: true });
    bundleDir = null;
    const details = String(bundled.stderr ?? '').trim().slice(-4000);
    throw new Error(`Remotion frame worker bundle failed${details ? `: ${details}` : ''}`);
  }
}

// A request may bring its effect assets as a private overlay: a directory that
// mirrors public/ (astrid-effects/<hash>/...). The owner then keeps the bundle it
// made once (an owner serves one renderer identity, so its sources are fixed) and
// links the overlay into the bundle's public/ for this request only. A warm
// capture then costs the render alone, and nothing is written to the shared
// project, so a capture never has to wait for a full render's lock.
export function linkOverlay(overlayDir, publicDir) {
  const created = [];
  const walk = (src, dst) => {
    if (!existsSync(dst)) {
      mkdirSync(dst, { recursive: true });
      created.push(dst);
    }
    for (const entry of readdirSync(src, { withFileTypes: true })) {
      const from = join(src, entry.name);
      const to = join(dst, entry.name);
      if (entry.isDirectory()) {
        walk(from, to);
      } else if (entry.isFile() && !existsSync(to)) {
        try {
          linkSync(from, to);
        } catch {
          copyFileSync(from, to);
        }
        created.push(to);
      }
    }
  };
  walk(overlayDir, publicDir);
  return created;
}

export function unlinkOverlay(created) {
  for (const path of [...created].reverse()) rmSync(path, { recursive: true, force: true });
}

async function renderRequest(request) {
  const overlay = request.publicOverlay ? resolve(request.publicOverlay) : null;
  const started = Date.now();
  let bundled = false;
  if (!browser || projectDir !== resolve(request.projectDir)) {
    await startSession(request);
    bundled = true;
  } else if (!overlay) {
    // Older callers stage effect assets into the project's public/: rebundle
    // so a warm owner does not serve the first request's static assets only.
    await bundleProject(request);
    bundled = true;
  }
  const linked = overlay && existsSync(overlay) ? linkOverlay(overlay, join(bundleDir, 'public')) : null;
  try {
    return await renderBundled(request, { bundled, setupMs: Date.now() - started });
  } finally {
    if (linked) unlinkOverlay(linked);
  }
}

async function renderBundled(request, timing) {
  // TMPDIR and the optional schema path are invocation-scoped. The browser
  // and bundle persist, but Remotion's transient media cache must follow the
  // current request rather than the first request's deleted temp directory.
  Object.assign(process.env, request.environment ?? {});
  const props = JSON.parse(readFileSync(request.propsPath, 'utf8'));
  const selected = Date.now();
  const composition = await selectComposition({
    serveUrl: bundleDir,
    id: request.compositionId,
    inputProps: props,
    port: Number(request.port),
    puppeteerInstance: browser,
    logLevel: 'error',
  });
  await renderFrames({
    serveUrl: bundleDir,
    // Keep the authored composition dimensions.  The Python adapter
    // downscales the completed PNGs after rendering; overriding these values
    // here would clip manual-layout clips against the thumbnail dimensions.
    composition,
    inputProps: props,
    port: Number(request.port),
    frames: request.frames.map(Number),
    outputDir: resolve(request.outputDir),
    imageFormat: 'png',
    imageSequencePattern: 'frame-[frame].[ext]',
    puppeteerInstance: browser,
    concurrency: 1,
    logLevel: 'error',
  });
  return { ...timing, renderMs: Date.now() - selected };
}

async function handleRequest(request) {
  if (request.type === 'release') {
    await closeSession();
    return { ok: true, released: true, exit: Boolean(request.force) };
  }
  if (request.type === 'shutdown') {
    await closeSession();
    return { ok: true, shutdown: true, exit: true };
  }
  if (request.type !== 'render') throw new Error(`unknown request type: ${String(request.type)}`);
  const timing = await renderRequest(request);
  return { ok: true, notes: notes.splice(0), timing };
}

function idleSeconds() {
  // Keep the browser warm through an agent's look-edit-look loop (minutes), not
  // just one request: a cold start costs 30-50 s, a warm capture ~0.3 s a frame.
  const configured = Number(process.env.ASTRID_TIMELINE_FRAME_IDLE_SECONDS ?? 300);
  return Number.isFinite(configured) ? Math.max(0, configured) : 300;
}

async function runServer(socketPath) {
  try { unlinkSync(socketPath); } catch { /* stale socket */ }
  let idleTimer = null;
  let closing = false;
  let server;
  const closeServer = async () => {
    if (closing) return;
    closing = true;
    if (idleTimer) clearTimeout(idleTimer);
    await closeSession();
    try { unlinkSync(socketPath); } catch { /* already removed */ }
    removeLaunchdJob(socketPath);
    server.close(() => process.exit(0));
  };
  const armIdle = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(closeServer, idleSeconds() * 1000);
    idleTimer.unref?.();
  };
  const disarmIdle = () => {
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = null;
  };
  let queue = Promise.resolve();
  server = net.createServer((connection) => {
    let buffer = '';
    let handled = false;
    connection.on('data', (chunk) => {
      buffer += chunk.toString('utf8');
      if (handled || !buffer.includes('\n')) return;
      handled = true;
      disarmIdle();
      const line = buffer.split('\n', 1)[0];
      let request;
      try {
        request = JSON.parse(line);
      } catch (error) {
        connection.end(JSON.stringify({ ok: false, error: String(error) }) + '\n');
        return;
      }
      queue = queue.then(async () => {
        try {
          const response = await handleRequest(request);
          connection.end(JSON.stringify({ ok: true, ...response }) + '\n');
          armIdle();
          if (response.exit) await closeServer();
        } catch (error) {
          await closeSession();
          connection.end(JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) }) + '\n');
          armIdle();
        }
      });
    });
  });
  server.on('error', async () => { await closeSession(); process.exit(1); });
  server.listen(socketPath, () => {
    chmodSync(socketPath, 0o600);
    armIdle();
  });
}

if (process.argv[2] === '--server') {
  const socketPath = resolve(process.argv[3] ?? '');
  if (!socketPath) throw new Error('frame worker server requires a socket path');
  await runServer(socketPath);
} else {
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of input) {
    if (!line.trim()) continue;
    let request;
    try {
      request = JSON.parse(line);
      const response = await handleRequest(request);
      reply(response);
      if (response.exit) process.exit(0);
    } catch (error) {
      await closeSession();
      reply({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  }

  await closeSession();
}
