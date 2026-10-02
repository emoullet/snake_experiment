import * as THREE from 'three';
import { ColladaLoader } from 'three/addons/loaders/ColladaLoader.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import URDFLoader from 'urdf-loader';
import { axisLabels, cycleBasePose, joystickDisplacement } from './mapping.js';
import { cyclePose, setArmPose, solveAxisEndpoints } from './kinematics.js';

const PREFIX = '/participant/3d-preview';
const ROBOT_MATERIAL = new THREE.MeshStandardMaterial({
  color: 0x315578, metalness: 0.02, roughness: 0.82, side: THREE.DoubleSide,
});

function setText(element, value) {
  if (element && element.textContent !== value) element.textContent = value;
}

function frameRobot(view) {
  const bounds = new THREE.Box3().setFromObject(view.robot);
  if (bounds.isEmpty()) return;
  const center = bounds.getCenter(new THREE.Vector3());
  const radius = Math.max(bounds.getSize(new THREE.Vector3()).length() * 0.7, 0.45);
  view.camera.position.copy(center).addScaledVector(view.direction.clone().normalize(), radius * 2.4);
  view.camera.lookAt(center);
  view.camera.near = Math.max(radius / 100, 0.01);
  view.camera.far = Math.max(radius * 20, 20);
  view.camera.updateProjectionMatrix();
}

function createJoystick(view) {
  const holder = view.root.querySelector('.mapping-joystick');
  holder.innerHTML = `<svg viewBox="0 0 128 128" aria-hidden="true" focusable="false">
    <circle class="joystick-base" cx="64" cy="64" r="53" />
    <path class="joystick-cross" d="M64 20v88M20 64h88" />
    <circle class="joystick-neutral" cx="64" cy="64" r="5" />
    <g data-joystick-stick><circle class="joystick-stick-shadow" cx="64" cy="64" r="19" /><circle class="joystick-stick" cx="64" cy="64" r="15" /></g>
  </svg><span class="joystick-input" data-joystick-input>Neutral</span>`;
  view.stick = holder.querySelector('[data-joystick-stick]');
  view.inputLabel = holder.querySelector('[data-joystick-input]');
}

function createView(root, axisIndex, direction) {
  const container = root.querySelector('.mapping-canvas');
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xe8f2ff);
  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 100);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  container.appendChild(renderer.domElement);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x9aacc5, 0.85));
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(2, 3, 4);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.35);
  fill.position.set(-3, 1, -2);
  scene.add(fill);
  const view = {
    root, container, scene, camera, renderer, direction, axisIndex, robot: null,
    source: root.querySelector('[data-mapping-source]'),
    directionLabel: root.querySelector('[data-mapping-direction]'),
    limit: root.querySelector('[data-mapping-limit]'),
    expectedMeshes: 0, completedMeshes: 0, endpoints: null,
  };
  createJoystick(view);
  const resize = () => {
    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(container);
  resize();
  return view;
}

async function loadModel(view) {
  const manager = new THREE.LoadingManager();
  const loader = new URDFLoader(manager);
  loader.parseCollision = false;
  const finishMesh = (done, object, error) => {
    done(object, error);
    view.completedMeshes += 1;
    if (view.completedMeshes === view.expectedMeshes) {
      requestAnimationFrame(() => { if (view.robot) frameRobot(view); });
    }
  };
  loader.loadMeshCb = (path, loadingManager, done) => {
    if (!path.startsWith(`${PREFIX}/assets/`)) {
      finishMesh(done, null, new Error('Unexpected robot asset URL.'));
      return;
    }
    if (path.endsWith('.stl')) {
      new STLLoader(loadingManager).load(path, geometry => {
        finishMesh(done, new THREE.Mesh(geometry, ROBOT_MATERIAL));
      }, undefined, error => finishMesh(done, null, error));
      return;
    }
    new ColladaLoader(loadingManager).load(path, result => {
      const importedLights = [];
      result.scene.traverse(part => {
        if (part.isMesh) part.material = ROBOT_MATERIAL;
        if (part.isLight) importedLights.push(part);
      });
      for (const light of importedLights) light.parent.remove(light);
      finishMesh(done, result.scene);
    }, undefined, error => finishMesh(done, null, error));
  };
  const response = await fetch(`${PREFIX}/model.urdf`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Robot model: HTTP ${response.status}`);
  const xml = await response.text();
  view.expectedMeshes = (xml.match(/<mesh\b/g) || []).length;
  view.robot = loader.parse(xml, '');
  view.robot.rotation.x = -Math.PI / 2;
  view.scene.add(view.robot);
  frameRobot(view);
}

export class MappingAnimator {
  constructor(roots, onState = () => {}, onError = () => {}) {
    this.views = roots.map((root, axis) => createView(root, axis,
      axis === 0 ? new THREE.Vector3(1.8, 1.2, 1.7) : new THREE.Vector3(-1.5, 1.2, 1.8)));
    this.onState = onState;
    this.onError = onError;
    this.config = null;
    this.selection = null;
    this.active = false;
    this.cycleStart = null;
    this.pending = false;
    this.generation = 0;
    this.frame = null;
  }

  async initialize() {
    const response = await fetch(`${PREFIX}/api/config`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`Mapping configuration: HTTP ${response.status}`);
    this.config = await response.json();
    await Promise.all(this.views.map(loadModel));
    if (this.active && this.selection) void this.refreshCycle();
    return this.config;
  }

  setSelection(mode, submode, snakeHeld = false) {
    const valid = this.config && this.config.mapper[mode]?.[submode]
      && (mode !== 'snake' || snakeHeld !== null);
    const next = valid ? { mode, submode, snakeHeld: mode === 'snake' && snakeHeld } : null;
    if (JSON.stringify(next) === JSON.stringify(this.selection)) {
      if (!next) {
        for (const view of this.views) {
          setText(view.source, 'Unknown');
          if (view.source.classList.contains('pill')) view.source.className = 'pill pill--neutral';
        }
      }
      return;
    }
    this.selection = next;
    for (const view of this.views) {
      const [positive, negative] = axisLabels(this.config, view.axisIndex);
      setText(view.directionLabel, `Axis ${view.axisIndex} · ${positive} → neutral → ${negative} → neutral`);
    }
    this.neutralize();
    if (!next) {
      for (const view of this.views) {
        setText(view.source, 'Unknown');
        if (view.source.classList.contains('pill')) view.source.className = 'pill pill--neutral';
        setText(view.limit, '');
      }
    }
    if (next && this.active && this.config) void this.refreshCycle();
  }

  neutralize() {
    this.generation += 1;
    this.cycleStart = null;
    this.pending = false;
    for (const view of this.views) {
      if (view.robot && view.endpoints) setArmPose(view.robot, view.endpoints.start);
      view.stick.setAttribute('transform', 'translate(0 0)');
      setText(view.inputLabel, 'Neutral');
    }
  }

  start() {
    if (this.active) return;
    this.active = true;
    const tick = now => {
      if (!this.active) return;
      if (this.cycleStart !== null && this.config) {
        const phase = (now - this.cycleStart) / (this.config.animation.loop_sec * 1000);
        if (phase >= 1) {
          if (!this.pending) void this.refreshCycle();
        } else {
          for (const view of this.views) {
            if (view.endpoints) setArmPose(view.robot, cyclePose(view.endpoints, phase));
            const stick = joystickDisplacement(this.config, view.axisIndex, phase);
            view.stick.setAttribute('transform', `translate(${stick.x} ${stick.y})`);
            setText(view.inputLabel, stick.input > 0.02 ? '+ input' : stick.input < -0.02 ? '− input' : 'Neutral');
          }
        }
      }
      for (const view of this.views) view.renderer.render(view.scene, view.camera);
      this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
    if (this.selection && this.config) void this.refreshCycle();
  }

  stop() {
    this.active = false;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.neutralize();
  }

  async refreshCycle() {
    if (!this.active || !this.selection || !this.config || this.views.some(view => !view.robot)) return;
    const generation = ++this.generation;
    this.pending = true;
    this.cycleStart = null;
    for (const view of this.views) {
      if (view.endpoints) setArmPose(view.robot, view.endpoints.start);
      view.stick.setAttribute('transform', 'translate(0 0)');
    }
    let state = null;
    try {
      const response = await fetch(`${PREFIX}/api/state`, { cache: 'no-store' });
      if (response.ok) state = await response.json();
    } catch { /* Explicit demo fallback below. */ }
    if (generation !== this.generation || !this.active) return;
    const { pose, source } = cycleBasePose(this.config, state);
    this.onState(state, source);
    try {
      for (const view of this.views) {
        view.endpoints = solveAxisEndpoints(view.robot, pose, this.config,
          this.selection.mode, this.selection.submode, view.axisIndex, this.selection.snakeHeld);
        setText(view.source, source);
        if (view.source.classList.contains('pill')) {
          view.source.className = source === 'Live pose' ? 'pill pill--active' : 'pill pill--warning';
        }
        const fraction = view.endpoints.fraction;
        setText(view.limit, fraction === 1 ? '' : fraction === 0
          ? 'No feasible movement from this pose; the view remains neutral.'
          : `Movement reduced to ${Math.round(fraction * 100)}% due to reachability or joint limits.`);
      }
      this.cycleStart = performance.now();
    } catch (error) {
      this.cycleStart = null;
      this.onError(error);
    } finally {
      this.pending = false;
    }
  }
}
