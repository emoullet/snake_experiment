import { Matrix4, Quaternion, Vector3 } from 'three';
import { commandForAxis } from './mapping.js';

export const ARM_JOINTS = Array.from({ length: 6 }, (_, index) => `joint_${index + 1}`);
const POSITION_TOLERANCE = 0.003;
const ANGLE_TOLERANCE = 0.012;

export function setArmPose(robot, pose) {
  for (const name of ARM_JOINTS) robot.setJointValue(name, pose[name]);
  if (Number.isFinite(pose.right_finger_joint)) {
    robot.setJointValue('right_finger_joint', pose.right_finger_joint);
  }
  robot.updateMatrixWorld(true);
}

export function clampToLimits(robot, pose) {
  const result = { ...pose };
  for (const name of ARM_JOINTS) {
    const joint = robot.joints[name];
    if (!joint) throw new Error(`Robot model is missing ${name}.`);
    if (joint.jointType === 'revolute') {
      result[name] = Math.min(joint.limit.upper, Math.max(joint.limit.lower, result[name]));
    }
  }
  return result;
}

export function effectorPose(robot) {
  const base = robot.links.base_link;
  const tool = robot.links.ft_frame;
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
  const normal = Array.from({ length: 6 }, (_, row) =>
    Array.from({ length: 6 }, (_, column) =>
      jacobian.reduce((sum, values) => sum + values[row] * values[column], row === column ? dampingSquared : 0)));
  const rhs = Array.from({ length: 6 }, (_, column) =>
    jacobian.reduce((sum, values, row) => sum + values[column] * error[row], 0));
  return solveLinear(normal, rhs);
}

function solveGoal(robot, start, goal) {
  const joints = clampToLimits(robot, start);
  for (let iteration = 0; iteration < 48; iteration += 1) {
    setArmPose(robot, joints);
    const current = effectorPose(robot);
    const error = poseError(current, goal);
    if (Math.hypot(...error.slice(0, 3)) < POSITION_TOLERANCE
        && Math.hypot(...error.slice(3)) < ANGLE_TOLERANCE) {
      return { joints: { ...joints }, error };
    }
    const jacobian = Array.from({ length: 6 }, () => Array(6).fill(0));
    for (let jointIndex = 0; jointIndex < 6; jointIndex += 1) {
      const name = ARM_JOINTS[jointIndex];
      const old = joints[name];
      const joint = robot.joints[name];
      const displacement = old + 1e-4 <= joint.limit.upper ? 1e-4 : -1e-4;
      joints[name] = old + displacement;
      setArmPose(robot, joints);
      const shifted = effectorPose(robot);
      joints[name] = old;
      const delta = poseError(current, shifted);
      for (let row = 0; row < 6; row += 1) jacobian[row][jointIndex] = delta[row] / displacement;
    }
    const step = dampedStep(jacobian, error);
    if (!step || step.some(value => !Number.isFinite(value))) break;
    let moved = false;
    for (let index = 0; index < 6; index += 1) {
      const name = ARM_JOINTS[index];
      const joint = robot.joints[name];
      const next = Math.min(joint.limit.upper, Math.max(joint.limit.lower,
        joints[name] + Math.max(-0.12, Math.min(0.12, step[index]))));
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
  const base = clampToLimits(robot, startPose);
  setArmPose(robot, base);
  const initial = effectorPose(robot);
  const result = { start: base, positive: base, negative: base, fraction: 0 };
  for (const fraction of [1, 0.75, 0.5, 0.25, 0.125, 0.0625]) {
    const endpoints = [];
    for (const sign of [1, -1]) {
      const command = commandForAxis(config, mode, submode, axisIndex, sign, snakeHeld, initial.rotation);
      const goal = goalForCommand(initial, command, config, fraction);
      const solved = solveGoal(robot, base, goal);
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
  setArmPose(robot, base);
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
  for (const name of ARM_JOINTS) {
    pose[name] = endpoints.start[name] + (target[name] - endpoints.start[name]) * amount;
  }
  return pose;
}
