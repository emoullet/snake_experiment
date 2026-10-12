import assert from 'node:assert/strict';
import test from 'node:test';
import { Box3, BoxGeometry, Group, Mesh, MeshBasicMaterial, PerspectiveCamera, Vector3 } from 'three';
import { CAMERA_VIEWS, cameraDirection, combinedCycleBounds, frameCameras } from '../src/camera.js';
import { MappingAnimator } from '../src/animation.js';

test('six oblique viewpoints keep their elevation and rotate in 60 degree steps', () => {
  const initial = new Vector3(1.8, 1.2, 1.7).normalize();
  for (const [index, id] of CAMERA_VIEWS.entries()) {
    const direction = cameraDirection(id);
    assert.ok(Math.abs(direction.y - initial.y) < 1e-12);
    assert.ok(Math.abs(direction.length() - 1) < 1e-12);
    const previous = cameraDirection(CAMERA_VIEWS[(index + 5) % 6]);
    const flat = direction.clone().setY(0).normalize();
    const previousFlat = previous.clone().setY(0).normalize();
    assert.ok(Math.abs(flat.angleTo(previousFlat) - Math.PI / 3) < 1e-12);
  }
  assert.ok(cameraDirection('view_1').distanceTo(initial) < 1e-12);
  assert.throws(() => cameraDirection('view_7'), /Unknown preview viewpoint/);
});

function movingView(offset, aspect) {
  const robot = new Group();
  robot.position.x = offset;
  const joint = new Group();
  joint.jointValue = [0.2];
  robot.add(joint);
  const arm = new Mesh(new BoxGeometry(2, 0.15, 0.15), new MeshBasicMaterial());
  arm.position.x = 1;
  joint.add(arm);
  robot.joints = { joint_1: joint };
  robot.setJointValue = (_, value) => {
    joint.jointValue[0] = value;
    joint.rotation.y = value;
  };
  robot.setJointValue('joint_1', 0.2);
  return {
    robot, camera: new PerspectiveCamera(35, aspect, 0.01, 100),
    config: { arm_joints: ['joint_1'] },
    endpoints: {
      arm_joints: ['joint_1'], start: { joint_1: 0 },
      positive: { joint_1: Math.PI / 2 }, negative: { joint_1: -Math.PI / 2 },
    },
  };
}

test('combined framing covers both complete motions and restores the current robot poses', () => {
  const views = [movingView(-2, 1.8), movingView(2, 0.55)];
  const before = views.map(view => view.robot.joints.joint_1.quaternion.clone());
  const bounds = combinedCycleBounds(views);
  assert.ok(bounds.min.x < -1.9 && bounds.max.x > 3.9);
  assert.ok(bounds.min.z < -1.9 && bounds.max.z > 1.9);
  for (const [index, view] of views.entries()) {
    assert.deepEqual(view.robot.joints.joint_1.jointValue, [0.2]);
    assert.ok(view.robot.joints.joint_1.quaternion.angleTo(before[index]) < 1e-7);
  }
  for (const id of CAMERA_VIEWS) {
    frameCameras(views, bounds, id);
    assert.ok(views[0].camera.position.equals(views[1].camera.position));
    assert.ok(views[0].camera.quaternion.angleTo(views[1].camera.quaternion) < 1e-7);
    for (const { camera } of views) {
      camera.updateMatrixWorld(true);
      for (const x of [bounds.min.x, bounds.max.x]) {
        for (const y of [bounds.min.y, bounds.max.y]) {
          for (const z of [bounds.min.z, bounds.max.z]) {
            const projected = new Vector3(x, y, z).project(camera);
            assert.ok(Math.abs(projected.x) < 1 && Math.abs(projected.y) < 1);
          }
        }
      }
    }
  }
});

test('changing viewpoint leaves the animation cycle, mapper selection and robot poses intact', () => {
  const animator = Object.create(MappingAnimator.prototype);
  animator.views = [movingView(-2, 1), movingView(2, 1)];
  animator.cameraBounds = combinedCycleBounds(animator.views);
  animator.cameraView = 'view_1';
  animator.cycleStart = 123;
  animator.generation = 4;
  animator.active = true;
  animator.selection = { mode: 'snake', submode: 'b2', snakeHeld: true };
  const selection = animator.selection;
  const bounds = animator.cameraBounds;
  animator.setCameraView('view_4');
  assert.equal(animator.cameraView, 'view_4');
  assert.equal(animator.cycleStart, 123);
  assert.equal(animator.generation, 4);
  assert.equal(animator.active, true);
  assert.equal(animator.selection, selection);
  assert.equal(animator.cameraBounds, bounds);
  assert.equal(animator.views[0].robot.joints.joint_1.jointValue[0], 0.2);
  assert.throws(() => animator.setCameraView('invalid'), /Unknown/);
  assert.equal(animator.cameraView, 'view_4');
  frameCameras(animator.views, new Box3(), 'view_4');
});
