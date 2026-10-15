/** Display-only inverse kinematics helpers for schematic robot animations. */

import { Matrix4, Quaternion, Vector3 } from 'three';
import { commandForAxis } from './mapping.js';

export const ARM_JOINTS = Array.from({ length: 6 }, (_, index) => `joint_${index + 1}`);
const armJoints = config => config.arm_joints || ARM_JOINTS;
function boundedValue(joint, value) {
  return joint.jointType === 'revolute'
    ? Math.min(joint.limit.upper, Math.max(joint.limit.lower, value)) : value;
}
const POSITION_TOLERANCE = 0.003;
const ANGLE_TOLERANCE = 0.012;

export function setArmPose(robot, pose, config = {}) {
  for (const name of armJoints(config)) robot.setJointValue(name, pose[name]);
  const gripper = config.gripper_joint || 'right_finger_joint';
  if (Number.isFinite(pose[gripper])) robot.setJointValue(gripper, pose[gripper]);
  robot.updateMatrixWorld(true);
}

export function clampToLimits(robot, pose, config = {}) {
  const result = { ...pose };
  for (const name of armJoints(config)) {
    const joint = robot.joints[name];
    if (!joint) throw new Error(`Robot model is missing ${name}.`);
    result[name] = boundedValue(joint, result[name]);
  }
  return result;
}

export function effectorPose(robot, config = {}) {
  const base = robot.links[config.frames?.base || 'base_link'];
  const tool = robot.links[config.frames?.ee || 'ft_frame'];
  if (!base || !tool) throw new Error('The robot model is missing its base or end-effector frame.');
  robot.updateMatrixWorld(true);
  const relative = new Matrix4().copy(base.matrixWorld).invert().multiply(tool.matrixWorld);
  const position = new Vector3();
  const rotation = new Quaternion();
  relative.decompose(position, rotation, new Vector3());
  return { position, rotation: rotation.normalize() };
}

export function rotationVector(quaternion) {
  const q = quaternion.clone().normalize();
  if (q.w < 0) q.set(-q.x, -q.y, -q.z, -q.w);
  const vector = new Vector3(q.x, q.y, q.z);
  const length = vector.length();
  return length < 1e-12 ? vector.multiplyScalar(2) : vector.multiplyScalar(2 * Math.atan2(length, q.w) / length);
}

function poseError(current, goal) {
  const position = goal.position.clone().sub(current.position);
  const rotation = rotationVector(goal.rotation.clone().multiply(current.rotation.clone().invert()));
  return [position.x, position.y, position.z, rotation.x, rotation.y, rotation.z];
}

function solveLinear(matrix, vector) {
  const size = vector.length;
  const rows = matrix.map((row, index) => [...row, vector[index]]);
  for (let column = 0; column < size; column += 1) {
    let pivot = column;
    for (let row = column + 1; row < size; row += 1) {
      if (Math.abs(rows[row][column]) > Math.abs(rows[pivot][column])) pivot = row;
    }
    if (Math.abs(rows[pivot][column]) < 1e-12) return null;
    [rows[column], rows[pivot]] = [rows[pivot], rows[column]];
    const divisor = rows[column][column];
    for (let cell = column; cell <= size; cell += 1) rows[column][cell] /= divisor;
    for (let row = 0; row < size; row += 1) {
      if (row === column) continue;
      const factor = rows[row][column];
      for (let cell = column; cell <= size; cell += 1) rows[row][cell] -= factor * rows[column][cell];
    }
  }
  return rows.map(row => row[size]);
}

function dampedStep(jacobian, error) {
  const dampingSquared = 0.04 ** 2;
  const count = jacobian[0].length;
  const normal = Array.from({ length: 6 }, (_, row) =>
    Array.from({ length: 6 }, (_, column) =>
      jacobian[row].reduce((sum, value, joint) => sum + value * jacobian[column][joint],
        row === column ? dampingSquared : 0)));
  const taskStep = solveLinear(normal, error);
  return taskStep && Array.from({ length: count }, (_, joint) =>
    taskStep.reduce((sum, value, row) => sum + jacobian[row][joint] * value, 0));
}

function solveGoal(robot, start, goal, config) {
  const names = armJoints(config);
  const joints = clampToLimits(robot, start, config);
  for (let iteration = 0; iteration < 48; iteration += 1) {
    setArmPose(robot, joints, config);
    const current = effectorPose(robot, config);
    const error = poseError(current, goal);
    if (Math.hypot(...error.slice(0, 3)) < POSITION_TOLERANCE
        && Math.hypot(...error.slice(3)) < ANGLE_TOLERANCE) {
      return { joints: { ...joints }, error };
    }
    const jacobian = Array.from({ length: 6 }, () => Array(names.length).fill(0));
    for (let jointIndex = 0; jointIndex < names.length; jointIndex += 1) {
      const name = names[jointIndex];
      const old = joints[name];
      const joint = robot.joints[name];
      const displacement = joint.jointType !== 'revolute' || old + 1e-4 <= joint.limit.upper ? 1e-4 : -1e-4;
      joints[name] = old + displacement;
      setArmPose(robot, joints, config);
      const shifted = effectorPose(robot, config);
      joints[name] = old;
      const delta = poseError(current, shifted);
      for (let row = 0; row < 6; row += 1) jacobian[row][jointIndex] = delta[row] / displacement;
    }
    const step = dampedStep(jacobian, error);
    if (!step || step.some(value => !Number.isFinite(value))) break;
    let moved = false;
    for (let index = 0; index < names.length; index += 1) {
      const name = names[index];
      const joint = robot.joints[name];
      const next = boundedValue(joint,
        joints[name] + Math.max(-0.12, Math.min(0.12, step[index])));
      moved ||= Math.abs(next - joints[name]) > 1e-8;
      joints[name] = next;
    }
    if (!moved) break;
  }
  return null;
}

function goalForCommand(start, command, config, fraction) {
  const linear = command.linear.clone();
  const angular = command.angular.clone();
  // Keep the small pedagogical amplitude bounded even when Snake's gain is large.
  linear.multiplyScalar(config.animation.linear_mm / 1000 * fraction / Math.max(1, linear.length()));
  angular.multiplyScalar(config.animation.angular_deg * Math.PI / 180 * fraction / Math.max(1, angular.length()));
  return {
    position: start.position.clone().add(linear),
    rotation: new Quaternion().setFromAxisAngle(
      angular.length() > 0 ? angular.clone().normalize() : new Vector3(0, 0, 1),
      angular.length(),
    ).multiply(start.rotation),
  };
}

export function solveAxisEndpoints(robot, startPose, config, mode, submode, axisIndex, snakeHeld) {
  const base = clampToLimits(robot, startPose, config);
  setArmPose(robot, base, config);
  const initial = effectorPose(robot, config);
  const result = { arm_joints: armJoints(config), start: base, positive: base, negative: base, fraction: 0 };
  for (const fraction of [1, 0.75, 0.5, 0.25, 0.125, 0.0625]) {
    const endpoints = [];
    for (const sign of [1, -1]) {
      const command = commandForAxis(config, mode, submode, axisIndex, sign, snakeHeld, initial.rotation);
      const goal = goalForCommand(initial, command, config, fraction);
      const solved = solveGoal(robot, base, goal, config);
      if (!solved) break;
      endpoints.push(solved.joints);
    }
    if (endpoints.length === 2) {
      result.positive = endpoints[0];
      result.negative = endpoints[1];
      result.fraction = fraction;
      break;
    }
  }
  setArmPose(robot, base, config);
  return result;
}

export function cyclePose(endpoints, phase) {
  const sinusoid = Math.sin(2 * Math.PI * phase);
  const fraction = Math.abs(sinusoid) < 1e-12 ? 0 : sinusoid;
  const target = fraction >= 0 ? endpoints.positive : endpoints.negative;
  const amount = Math.abs(fraction);
  if (amount === 0) return { ...endpoints.start };
  if (Math.abs(amount - 1) < 1e-12) return { ...target };
  const pose = { ...endpoints.start };
  for (const name of endpoints.arm_joints || ARM_JOINTS) {
    pose[name] = endpoints.start[name] + (target[name] - endpoints.start[name]) * amount;
  }
  return pose;
}
