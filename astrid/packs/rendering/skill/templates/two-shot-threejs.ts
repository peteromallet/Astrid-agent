import * as THREE from 'three';

/**
 * Minimal live-scene authoring template.
 *
 * Bundle this file for the package's self-contained HTML entry. The host owns
 * the clock and calls the three lifecycle methods below; this source does not
 * start a render loop, audio clock, or input handler.
 */

type SceneFrame = { sourceTime: number; width: number; height: number };

type SceneApi = {
  initialize(): Promise<void>;
  render(frame: SceneFrame): Promise<void>;
  dispose(): void;
};

type CameraCut = {
  name: 'wide' | 'close';
  start: number;
  from: THREE.Vector3;
  to: THREE.Vector3;
  lookAt: THREE.Vector3;
};

type World = {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  subject: THREE.Group;
  label: HTMLElement;
  geometries: THREE.BufferGeometry[];
  materials: THREE.Material[];
};

const DURATION = 4;
const CUT_TIME = 2;
// Keep independently editable events on independent source-time controls,
// even when their defaults coincide. An agent can then retime the camera
// without unintentionally moving the world-color transition.
const BACKGROUND_CHANGE_TIME = 2;

function createWorld(): World {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.shadowMap.enabled = true;
  document.body.style.margin = '0';
  document.body.style.overflow = 'hidden';
  document.body.style.background = '#08111d';
  document.body.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#08111d');
  const camera = new THREE.PerspectiveCamera(45, 16 / 9, 0.1, 100);
  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];
  const material = <T extends THREE.Material>(value: T): T => {
    materials.push(value);
    return value;
  };
  const geometry = <T extends THREE.BufferGeometry>(value: T): T => {
    geometries.push(value);
    return value;
  };

  // One reusable set: the floor, three columns, and a warm key light are
  // created once and are shared by both authored camera cuts.
  const floor = new THREE.Mesh(
    geometry(new THREE.PlaneGeometry(14, 10)),
    material(new THREE.MeshStandardMaterial({ color: '#17324b', roughness: 0.82 })),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  scene.add(floor);
  for (const x of [-3.2, 0, 3.2]) {
    const column = new THREE.Mesh(
      geometry(new THREE.CylinderGeometry(0.35, 0.45, 2.8, 20)),
      material(new THREE.MeshStandardMaterial({ color: '#24506a', roughness: 0.5 })),
    );
    column.position.set(x, 1.4, -1.8);
    column.castShadow = true;
    column.receiveShadow = true;
    scene.add(column);
  }
  scene.add(new THREE.HemisphereLight('#b8ddff', '#102033', 1.8));
  const key = new THREE.DirectionalLight('#ffd9a3', 3.2);
  key.position.set(-3, 6, 4);
  key.castShadow = true;
  scene.add(key);

  // One reusable animated subject. Its pose is reset by evaluateAction at
  // every source time, so direct and backward seeks are deterministic.
  const subject = new THREE.Group();
  const body = new THREE.Mesh(
    geometry(new THREE.CapsuleGeometry(0.38, 0.95, 8, 16)),
    material(new THREE.MeshStandardMaterial({ color: '#ff9f68', roughness: 0.42 })),
  );
  body.castShadow = true;
  body.position.y = 1.05;
  const head = new THREE.Mesh(
    geometry(new THREE.SphereGeometry(0.36, 20, 12)),
    material(new THREE.MeshStandardMaterial({ color: '#ffd1a6', roughness: 0.6 })),
  );
  head.castShadow = true;
  head.position.y = 1.9;
  subject.add(body, head);
  scene.add(subject);

  const label = document.createElement('div');
  label.style.cssText = 'position:fixed;left:16px;top:14px;color:#fff;font:600 16px monospace;letter-spacing:.04em;text-shadow:0 2px 5px #000';
  document.body.appendChild(label);

  return { renderer, scene, camera, subject, label, geometries, materials };
}

// Named cuts are authoring organization only. They are not a host-managed
// shot schema or IDs, and both cuts intentionally evaluate the same world.
const cameraCuts: Record<CameraCut['name'], CameraCut> = {
  wide: {
    name: 'wide',
    start: 0,
    from: new THREE.Vector3(4.5, 3.1, 7.5),
    to: new THREE.Vector3(3.2, 2.6, 6.2),
    lookAt: new THREE.Vector3(0, 1.1, 0),
  },
  close: {
    name: 'close',
    start: CUT_TIME,
    from: new THREE.Vector3(-3.1, 2.2, 4.2),
    to: new THREE.Vector3(-2.2, 1.8, 3.3),
    lookAt: new THREE.Vector3(0, 1.35, 0),
  },
};

function evaluateAction(world: World, sourceTime: number): CameraCut {
  const t = THREE.MathUtils.clamp(sourceTime, 0, DURATION);
  const cut = t < CUT_TIME ? cameraCuts.wide : cameraCuts.close;
  const shotTime = cut === cameraCuts.wide ? t : t - cut.start;
  const shotProgress = THREE.MathUtils.clamp(shotTime / (DURATION - cut.start), 0, 1);

  // This action is a pure function of source time; no elapsed-time or RAF
  // state is consulted.
  world.subject.position.set(Math.sin(t * 1.4) * 1.1, 0, Math.cos(t * 1.1) * 0.25);
  world.subject.rotation.set(0, Math.sin(t * 0.8) * 0.45, Math.sin(t * 2.2) * 0.08);
  world.camera.position.copy(cut.from).lerp(cut.to, shotProgress);
  world.camera.lookAt(cut.lookAt);
  world.scene.background = new THREE.Color(t < BACKGROUND_CHANGE_TIME ? '#08111d' : '#21142b');
  world.label.textContent = `${cut.name.toUpperCase()} CUT · source ${t.toFixed(2)}s`;
  return cut;
}

let world: World | undefined;
let disposed = false;

const sceneApi: SceneApi = {
  async initialize() {
    if (world && !disposed) return;
    disposed = false;
    world = createWorld();
    // There are no remote assets in this template. Still wait for the host
    // document's font readiness before reporting a usable final surface.
    await document.fonts.ready;
  },

  async render({ sourceTime, width, height }: SceneFrame) {
    if (disposed || !world) throw new Error('Two-shot scene is disposed');
    if (![sourceTime, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {
      throw new Error('Invalid two-shot frame request');
    }
    world.renderer.setSize(width, height, false);
    world.camera.aspect = width / height;
    world.camera.updateProjectionMatrix();
    evaluateAction(world, sourceTime);
    world.renderer.render(world.scene, world.camera);
    world.renderer.getContext().finish();
  },

  dispose() {
    if (disposed) return;
    disposed = true;
    const current = world;
    world = undefined;
    if (!current) return;
    for (const geometry of current.geometries) geometry.dispose();
    for (const material of current.materials) material.dispose();
    current.renderer.dispose();
    current.renderer.forceContextLoss();
    current.renderer.domElement.remove();
    current.label.remove();
  },
};

window.astridScene = sceneApi;

declare global {
  interface Window {
    astridScene: SceneApi;
  }
}
