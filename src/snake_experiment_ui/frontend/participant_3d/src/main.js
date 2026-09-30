import * as THREE from 'three';
import { ColladaLoader } from 'three/addons/loaders/ColladaLoader.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import URDFLoader from 'urdf-loader';

const MODEL_URL = '/participant/3d-preview/model.urdf';
const STATE_URL = '/participant/3d-preview/api/state';
const SOCKET_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/participant/3d-preview/ws`;
const ROBOT_MATERIAL = new THREE.MeshStandardMaterial({
  color: 0x315578, metalness: 0.02, roughness: 0.82, side: THREE.DoubleSide,
});
const connection = document.getElementById('preview-connection');
const message = document.getElementById('preview-message');
const views = [];
let latestPose = null;
let modelCount = 0;
let reconnectTimer = null;

function setConnection(status) {
  connection.textContent = status;
  connection.className = `pill ${status === 'Live pose' ? 'pill--active' : 'pill--neutral'}`;
}

function applyPose(view, joints) {
  if (!view.robot || !joints) return;
  for (const [name, position] of Object.entries(joints)) {
    if (view.robot.joints[name] && Number.isFinite(position)) {
      view.robot.setJointValue(name, position);
    }
  }
}

function updateState(state) {
  if (state.robot !== 'explorer_poc2') return;
  if (state.status === 'live' && state.joints) {
    latestPose = state.joints;
    for (const view of views) applyPose(view, latestPose);
    setConnection('Live pose');
    message.textContent = state.gripper_available
      ? 'Arm and gripper follow the current joint states.'
      : 'Arm follows the current joint states; gripper state is unavailable.';
  } else {
    setConnection(state.status === 'stale' ? 'Pose stale' : 'Waiting for joints');
    message.textContent = state.status === 'stale'
      ? 'Joint states are stale. The last known model pose is held.'
      : 'Waiting for Explorer POC2 joint states. The model is displayed at its URDF pose.';
  }
}

function frameRobot(view) {
  const bounds = new THREE.Box3().setFromObject(view.robot);
  if (bounds.isEmpty()) return;
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const radius = Math.max(size.length() * 0.7, 0.45);
  const direction = view.direction.clone().normalize();
  view.camera.position.copy(center).addScaledVector(direction, radius * 2.4);
  view.camera.lookAt(center);
  view.camera.near = Math.max(radius / 100, 0.01);
  view.camera.far = Math.max(radius * 20, 20);
  view.camera.updateProjectionMatrix();
}

function createView(containerId, direction) {
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
    container, scene, camera, renderer, direction, robot: null,
    expectedMeshes: 0, completedMeshes: 0,
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
  renderer.setAnimationLoop(() => renderer.render(scene, camera));
  return view;
}

function loadModel(view) {
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
    if (!path.startsWith('/participant/3d-preview/assets/')) {
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
  fetch(MODEL_URL, { cache: 'no-store' }).then(response => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.text();
  }).then(xml => {
    view.expectedMeshes = (xml.match(/<mesh\b/g) || []).length;
    const robot = loader.parse(xml, '');
    view.robot = robot;
    robot.rotation.x = -Math.PI / 2;
    view.scene.add(robot);
    applyPose(view, latestPose);
    frameRobot(view);
    modelCount += 1;
    if (modelCount === views.length && !latestPose) {
      message.textContent = 'Model ready. Waiting for Explorer POC2 joint states.';
    }
  }).catch(error => {
    message.textContent = `The Explorer POC2 model could not be loaded: ${error.message || error}`;
    setConnection('Model unavailable');
  });
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

try {
  const horizontal = createView('preview-horizontal', new THREE.Vector3(1.8, 1.2, 1.7));
  const vertical = createView('preview-vertical', new THREE.Vector3(-1.5, 1.2, 1.8));
  loadModel(horizontal);
  loadModel(vertical);
  fetch(STATE_URL, { cache: 'no-store' }).then(response => response.json()).then(updateState).catch(() => {});
  connect();
} catch (error) {
  message.textContent = `3D preview is unavailable in this browser: ${error.message || error}`;
  setConnection('Unavailable');
}
