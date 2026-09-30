import * as THREE from 'three';
import { ColladaLoader } from 'three/addons/loaders/ColladaLoader.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import URDFLoader from 'urdf-loader';
import { axisLabels, cycleBasePose } from './mapping.js';
import { cyclePose, setArmPose, solveAxisEndpoints } from './kinematics.js';

const PREFIX = '/participant/3d-preview';
const SOCKET_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${PREFIX}/ws`;
const ROBOT_MATERIAL = new THREE.MeshStandardMaterial({
  color: 0x315578, metalness: 0.02, roughness: 0.82, side: THREE.DoubleSide,
});
const connection = document.getElementById('preview-connection');
const message = document.getElementById('preview-message');
const modeSelect = document.getElementById('preview-mode');
const submodeSelect = document.getElementById('preview-submode');
const snakeControl = document.getElementById('preview-snake-control');
const snakeHeld = document.getElementById('preview-snake-held');
const views = [];
let config = null;
let cycleStart = null;
let cyclePending = false;
let cycleGeneration = 0;
let reconnectTimer = null;

function setConnection(status) {
  connection.textContent = status;
  connection.className = `pill ${status === 'Live pose' ? 'pill--active' : 'pill--neutral'}`;
}

function updateState(state) {
  if (state.robot !== 'explorer_poc2') return;
  if (state.status === 'live') {
    setConnection('Live pose');
    message.textContent = state.gripper_available
      ? 'Using the latest arm and gripper pose at each animation loop.'
      : 'Using the latest arm pose; the gripper uses its demo opening.';
  } else {
    setConnection('Demo pose');
    message.textContent = state.status === 'stale'
      ? 'Joint states are stale. The next loop will use the demo pose.'
      : 'Joint states are unavailable. The animation uses the demo pose.';
  }
}

function frameRobot(view) {
  const bounds = new THREE.Box3().setFromObject(view.robot);
  if (bounds.isEmpty()) return;
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const radius = Math.max(size.length() * 0.7, 0.45);
  view.camera.position.copy(center).addScaledVector(view.direction.clone().normalize(), radius * 2.4);
  view.camera.lookAt(center);
  view.camera.near = Math.max(radius / 100, 0.01);
  view.camera.far = Math.max(radius * 20, 20);
  view.camera.updateProjectionMatrix();
}

function createView(containerId, axisIndex, direction) {
  const container = document.getElementById(containerId);
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
    container, scene, camera, renderer, direction, axisIndex, robot: null,
    source: document.getElementById(`preview-${axisIndex === 0 ? 'horizontal' : 'vertical'}-source`),
    directionLabel: document.getElementById(`preview-${axisIndex === 0 ? 'horizontal' : 'vertical'}-direction`),
    limit: document.getElementById(`preview-${axisIndex === 0 ? 'horizontal' : 'vertical'}-limit`),
    expectedMeshes: 0, completedMeshes: 0, endpoints: null,
  };
  const resize = () => {
    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(container);
  resize();
  views.push(view);
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
  const robot = loader.parse(xml, '');
  view.robot = robot;
  robot.rotation.x = -Math.PI / 2;
  view.scene.add(robot);
  frameRobot(view);
}

function updateControls() {
  const names = Object.keys(config.mapper[modeSelect.value]);
  const previous = submodeSelect.value;
  submodeSelect.replaceChildren(...names.map(name => new Option(name, name)));
  submodeSelect.value = names.includes(previous) ? previous : names[0];
  snakeControl.hidden = modeSelect.value !== 'snake';
  for (const view of views) {
    const [positive, negative] = axisLabels(config, view.axisIndex);
    view.directionLabel.textContent = `Axis ${view.axisIndex} · ${positive} → neutral → ${negative} → neutral`;
  }
}

async function refreshCycle() {
  if (!config || views.some(view => !view.robot)) return;
  const generation = ++cycleGeneration;
  cyclePending = true;
  cycleStart = null;
  for (const view of views) {
    if (view.endpoints) setArmPose(view.robot, view.endpoints.start);
  }
  let state = null;
  try {
    const response = await fetch(`${PREFIX}/api/state`, { cache: 'no-store' });
    if (response.ok) state = await response.json();
  } catch { /* Use the explicit demo pose below. */ }
  if (generation !== cycleGeneration) return;
  if (state) updateState(state);
  else {
    setConnection('Demo pose');
    message.textContent = 'Joint state could not be read. The animation uses the demo pose.';
  }
  const { pose, source } = cycleBasePose(config, state);
  try {
    for (const view of views) {
      view.endpoints = solveAxisEndpoints(
        view.robot, pose, config, modeSelect.value, submodeSelect.value,
        view.axisIndex, modeSelect.value === 'snake' && snakeHeld.checked,
      );
      view.source.textContent = source;
      const fraction = view.endpoints.fraction;
      view.limit.textContent = fraction === 1 ? '' : fraction === 0
        ? 'No feasible movement from this pose; the view remains neutral.'
        : `Movement reduced to ${Math.round(fraction * 100)}% due to reachability or joint limits.`;
    }
    cycleStart = performance.now();
  } catch (error) {
    message.textContent = `Mapping animation is unavailable: ${error.message || error}`;
    cycleStart = null;
  } finally {
    cyclePending = false;
  }
}

function tick(now) {
  if (cycleStart !== null && config) {
    const phase = (now - cycleStart) / (config.animation.loop_sec * 1000);
    if (phase >= 1) {
      if (!cyclePending) void refreshCycle();
    } else {
      for (const view of views) {
        if (view.endpoints) setArmPose(view.robot, cyclePose(view.endpoints, phase));
      }
    }
  }
  for (const view of views) view.renderer.render(view.scene, view.camera);
  requestAnimationFrame(tick);
}

function connect() {
  const socket = new WebSocket(SOCKET_URL);
  socket.onmessage = event => {
    try { updateState(JSON.parse(event.data)); } catch { /* Ignore malformed state. */ }
  };
  socket.onclose = () => {
    setConnection('Disconnected');
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 1500);
  };
  socket.onerror = () => socket.close();
}

async function start() {
  createView('preview-horizontal', 0, new THREE.Vector3(1.8, 1.2, 1.7));
  createView('preview-vertical', 1, new THREE.Vector3(-1.5, 1.2, 1.8));
  requestAnimationFrame(tick);
  const response = await fetch(`${PREFIX}/api/config`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Mapping configuration: HTTP ${response.status}`);
  config = await response.json();
  await Promise.all(views.map(loadModel));
  updateControls();
  modeSelect.disabled = false;
  submodeSelect.disabled = false;
  snakeHeld.disabled = false;
  modeSelect.addEventListener('change', () => { updateControls(); void refreshCycle(); });
  submodeSelect.addEventListener('change', () => { void refreshCycle(); });
  snakeHeld.addEventListener('change', () => { void refreshCycle(); });
  connect();
  await refreshCycle();
}

start().catch(error => {
  message.textContent = `3D preview is unavailable: ${error.message || error}`;
  setConnection('Unavailable');
});
