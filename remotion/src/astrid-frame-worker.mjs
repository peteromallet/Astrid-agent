import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, unlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import net from 'node:net';
import readline from 'node:readline';
import { openBrowser, renderFrames, selectComposition } from '@remotion/renderer';

let browser = null;
let bundleDir = null;
let projectDir = null;

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
  browser = await openBrowser('chrome', {
    browserExecutable: request.browserExecutable ?? null,
    chromiumOptions: { headless: true },
    logLevel: 'error',
  });
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

async function renderRequest(request) {
  if (!browser || projectDir !== resolve(request.projectDir)) {
    await startSession(request);
  } else {
    // Effect assets are staged under an invocation-specific public prefix.
    // Rebundle the current public tree while retaining the Chromium browser;
    // otherwise a warm owner serves the first request's static assets only.
    await bundleProject(request);
  }
  // TMPDIR and the optional schema path are invocation-scoped. The browser
  // and bundle persist, but Remotion's transient media cache must follow the
  // current request rather than the first request's deleted temp directory.
  Object.assign(process.env, request.environment ?? {});
  const props = JSON.parse(readFileSync(request.propsPath, 'utf8'));
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
  await renderRequest(request);
  return { ok: true };
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
