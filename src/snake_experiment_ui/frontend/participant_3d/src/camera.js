import { Box3, Vector3 } from 'three';
import { cyclePose, setArmPose } from './kinematics.js';

export const CAMERA_VIEWS = Object.freeze(Array.from({ length: 6 }, (_, index) => `view_${index + 1}`));

export function cameraDirection(viewId) {
  const index = CAMERA_VIEWS.indexOf(viewId);
  if (index < 0) throw new Error(`Unknown preview viewpoint: ${viewId}`);
  return new Vector3(1.8, 1.2, 1.7).applyAxisAngle(new Vector3(0, 1, 0), index * Math.PI / 3).normalize();
}

/** Include both complete cycles without changing the pose currently being rendered. */
export function combinedCycleBounds(views) {
  const bounds = new Box3();
  for (const view of views) {
    if (!view.robot) continue;
    if (!view.endpoints || !view.config) {
      bounds.union(new Box3().setFromObject(view.robot));
      continue;
    }
    const saved = Object.entries(view.robot.joints).map(([name, joint]) => ({
      name, joint, values: joint.jointValue?.slice(),
      position: joint.position.clone(), quaternion: joint.quaternion.clone(),
    }));
    try {
      for (let sample = 0; sample <= 8; sample += 1) {
        setArmPose(view.robot, cyclePose(view.endpoints, sample / 8), view.config);
        bounds.union(new Box3().setFromObject(view.robot));
      }
    } finally {
      for (const item of saved) {
        if (item.values) view.robot.setJointValue(item.name, ...item.values);
        item.joint.position.copy(item.position);
        item.joint.quaternion.copy(item.quaternion);
      }
      view.robot.updateMatrixWorld(true);
    }
  }
  return bounds;
}

/** A common center and distance preserve the same viewpoint in both canvases. */
export function frameCameras(views, bounds, viewId) {
  const direction = cameraDirection(viewId);
  if (bounds.isEmpty() || !views.length) return;
  const center = bounds.getCenter(new Vector3());
  const radius = Math.max(bounds.getSize(new Vector3()).length() * 0.7, 0.45);
  const aspect = Math.min(1, ...views.map(view => view.camera.aspect));
  const position = center.clone().addScaledVector(direction, radius * 2.4 / aspect);
  for (const view of views) {
    view.camera.position.copy(position);
    view.camera.lookAt(center);
    view.camera.near = Math.max(radius / 100, 0.01);
    view.camera.far = Math.max(radius * 20, 20);
    view.camera.updateProjectionMatrix();
  }
}
