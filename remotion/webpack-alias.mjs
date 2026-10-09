import path from 'node:path';
import fs from 'node:fs';

// The Remotion project directory is the working directory: npm scripts, the
// `remotion` CLI (which bundles remotion.config.ts as CJS, so import.meta is
// unavailable there) and the render backend (cwd=project_dir) all run from it.
// Resolve paths at call time from the cwd, never at module load.
const projectDirOf = () => process.cwd();
const RENDERING_PACK_ELEMENTS_DIR_NAME = 'astrid/packs/rendering/elements';
const MANIFEST_NAMES = ['pack.yaml', 'pack.yml', 'pack.json'];
// Folder spellings a pack may use for the built-in element kinds
// (astrid/core/pack/registry.py). The generator imports by canonical kind.
const CANONICAL_KIND = {
  effect: 'effects',
  effects: 'effects',
  animation: 'animations',
  animations: 'animations',
  transition: 'transitions',
  transitions: 'transitions',
};

const readText = (file) => {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
};

// Pack id and declared elements folder, read the way astrid/core/pack reads
// them (`content.elements`, default `elements`). pack.yaml is scanned line by
// line for the two keys that matter, so the bundler needs no YAML parser.
export const readPackManifest = (packRoot) => {
  for (const name of MANIFEST_NAMES) {
    const text = readText(path.join(packRoot, name));
    if (text === null) continue;
    if (name === 'pack.json') {
      try {
        const json = JSON.parse(text);
        return {id: json.id, elements: json.content?.elements};
      } catch {
        return {};
      }
    }
    const id = text.match(/^id:\s*([A-Za-z0-9_-]+)/m)?.[1];
    let elements;
    let inContent = false;
    for (const line of text.split(/\r?\n/)) {
      if (/^\S/.test(line)) {
        inContent = /^content:\s*$/.test(line);
      } else if (inContent) {
        const match = line.match(/^\s+elements:\s*['"]?([^'"#]*?)['"]?\s*(#.*)?$/);
        if (match && match[1]) elements = match[1];
      }
    }
    return {id, elements};
  }
  return {};
};

const isDirectory = (file) => {
  try {
    return fs.statSync(file).isDirectory();
  } catch {
    return false;
  }
};

const isPackRoot = (dir) => MANIFEST_NAMES.some((name) => fs.existsSync(path.join(dir, name)));

// Pack roots under a collection root, or the root itself when it is one pack.
const packRootsIn = (root, {skipLocal}) => {
  if (isPackRoot(root)) return [root];
  let names = [];
  try {
    names = fs.readdirSync(root).sort();
  } catch {
    return [];
  }
  return names
    .filter((name) => !name.startsWith('.') && !(skipLocal && name === 'local'))
    .map((name) => path.join(root, name))
    .filter((dir) => isDirectory(dir) && isPackRoot(dir));
};

// Alias every pack element kind folder the registry generator can import:
// `@pack-<pack id>-elements-<kind>` -> `<elements root>/<kind folder>`.
const collectPackElementAliases = (packRoots) => {
  const aliases = {};
  for (const packRoot of packRoots) {
    const manifest = readPackManifest(packRoot);
    const packId = manifest.id || path.basename(packRoot);
    const elementsRoot = path.resolve(packRoot, manifest.elements || 'elements');
    let names = [];
    try {
      names = fs.readdirSync(elementsRoot).sort();
    } catch {
      continue;
    }
    for (const name of names) {
      const folder = path.join(elementsRoot, name);
      if (name.startsWith('.') || name.startsWith('_') || !isDirectory(folder)) continue;
      const key = `@pack-${packId}-elements-${CANONICAL_KIND[name] ?? name}`;
      if (!(key in aliases)) aliases[key] = folder;
    }
  }
  return aliases;
};

// Every in-tree pack with an elements root (discovered from astrid/packs the
// way the registry discovers source packs), then the ASTRID_PACKS_PATH roots.
// External roots win on a shared key, so an SDK invocation's packs bundle from
// that root for this render only.
export const packElementAliases = ({
  env = process.env,
  astridDir = path.resolve(projectDirOf(), '..'),
} = {}) => {
  const inTree = packRootsIn(path.resolve(astridDir, 'astrid/packs'), {skipLocal: false});
  const external = [];
  for (const rawRoot of (env.ASTRID_PACKS_PATH ?? '').split(path.delimiter)) {
    if (rawRoot) external.push(...packRootsIn(path.resolve(rawRoot), {skipLocal: true}));
  }
  return {...collectPackElementAliases(inTree), ...collectPackElementAliases(external)};
};

// The @theme and @workspace aliases; pack element aliases come from
// packElementAliases() so no pack id is hard-coded here.
const primitiveAliases = (projectDir) => {
  const activeThemeDir = path.resolve(projectDir, '_active_theme');
  const renderingElementsDir = path.resolve(projectDir, '..', RENDERING_PACK_ELEMENTS_DIR_NAME);
  return {
    '@theme-elements-effects': path.resolve(activeThemeDir, 'elements/effects'),
    '@theme-effects': path.resolve(activeThemeDir, 'effects'),
    '@theme-elements-animations': path.resolve(activeThemeDir, 'elements/animations'),
    '@theme-animations': path.resolve(activeThemeDir, 'animations'),
    '@theme-elements-transitions': path.resolve(activeThemeDir, 'elements/transitions'),
    '@theme-transitions': path.resolve(activeThemeDir, 'transitions'),
    '@workspace-animations': path.resolve(renderingElementsDir, 'animations'),
    '@workspace-effects': path.resolve(renderingElementsDir, 'effects'),
    '@workspace-transitions': path.resolve(renderingElementsDir, 'transitions'),
  };
};

export const remotionAliases = ({env = process.env} = {}) => {
  const projectDir = projectDirOf();
  return {
    ...primitiveAliases(projectDir),
    ...packElementAliases({env, astridDir: path.resolve(projectDir, '..')}),
  };
};

// Workspace-level effects/animations/transitions/themes/* live above the
// Remotion project, so their nearest node_modules walks up past the
// tools/remotion install. Add the Remotion project's node_modules to
// resolve.modules so they can `import` npm packages like
// @remotion/layout-utils that ship with this project.
export const applyRemotionPrimitiveAliases = (currentConfiguration) => ({
  ...currentConfiguration,
  resolve: {
    ...currentConfiguration.resolve,
    alias: {
      ...currentConfiguration.resolve?.alias,
      ...remotionAliases(),
    },
    modules: [
      ...(currentConfiguration.resolve?.modules ?? ['node_modules']),
      path.resolve(projectDirOf(), 'node_modules'),
    ],
  },
});

export const applyWorkspaceEffectsAlias = applyRemotionPrimitiveAliases;
