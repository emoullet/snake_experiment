import { Vector3 } from 'three';

const LINEAR = ['linear_x', 'linear_y', 'linear_z'];
const ANGULAR = ['angular_x', 'angular_y', 'angular_z'];

/** Keep the participant demonstration moving when live mapper state is unavailable. */
export function participantMappingSelection(config, state, lastKnownSubmode = null) {
  const mappings = config.mapper[state?.mode];
  if (!mappings) return null;
  const hasSubmode = name => Object.prototype.hasOwnProperty.call(mappings, name);
  const submodeReported = hasSubmode(state.local_mode);
  const submode = submodeReported ? state.local_mode
    : hasSubmode(lastKnownSubmode) ? lastKnownSubmode : Object.keys(mappings)[0];
  if (!submode) return null;
  const snakeHeldKnown = state.mode !== 'snake' || typeof state.snake_button_held === 'boolean';
  return {
    mode: state.mode,
    submode,
    snakeHeld: state.mode === 'snake' && snakeHeldKnown ? state.snake_button_held : false,
    submodeReported,
    snakeHeldKnown,
  };
}

/** Reproduce the mapper axis assignment and cartesian_manager's frame/shaper order. */
export function commandForAxis(config, mode, submode, axisIndex, sign, snakeHeld, eeQuaternion) {
  if (![0, 1].includes(axisIndex) || ![-1, 1].includes(sign)) {
    throw new Error('Invalid preview joystick axis.');
  }
  const mapping = config.mapper[mode]?.[submode];
  if (!mapping) throw new Error('Unknown preview mapper sub-mode.');
  const component = name => {
    const spec = mapping.axes[name];
    return spec.index === axisIndex ? sign * spec.scale : 0;
  };
  const linear = new Vector3(...LINEAR.map(component));
  const angular = new Vector3(...ANGULAR.map(component));
  // InputManager rotates angular values in the configured EE frame. Linear stays in base.
  if (mapping.angular_frame === config.frames.ee) angular.applyQuaternion(eeQuaternion);
  if (mode === 'snake' && snakeHeld) {
    // SnakeShaper: gain * tool-z x linear + angular, using the current EE pose.
    const toolZ = new Vector3(0, 0, 1).applyQuaternion(eeQuaternion);
    angular.add(toolZ.cross(linear.clone()).multiplyScalar(config.snake_gain));
  }
  return { linear, angular };
}

export function axisLabels(config, axisIndex) {
  const physical = axisIndex === 0 ? config.physical_axis_signs.right : config.physical_axis_signs.up;
  if (physical === null) return ['+', '−'];
  const positive = axisIndex === 0 ? 'Right' : 'Up';
  const negative = axisIndex === 0 ? 'Left' : 'Down';
  return physical === 1 ? [positive, negative] : [negative, positive];
}

/** Joystick and arm share this phase; the stick shows commanded input, not IK reachability. */
export function joystickDisplacement(config, axisIndex, phase, radius = 32) {
  const sign = axisIndex === 0 ? config.physical_axis_signs.right : config.physical_axis_signs.up;
  const input = Math.sin(2 * Math.PI * phase);
  const direction = sign === -1 ? -1 : 1;
  const amount = Math.abs(input) < 1e-12 ? 0 : input * radius * direction;
  return axisIndex === 0 ? { x: amount, y: 0, input } : { x: 0, y: amount === 0 ? 0 : -amount, input };
}

/** Select a fresh server pose at every loop boundary, never a stale cached pose. */
export function cycleBasePose(config, state) {
  const names = config.arm_joints || Array.from({ length: 6 }, (_, index) => `joint_${index + 1}`);
  const live = state?.robot === (config.robot || 'explorer_poc2') && state.status === 'live'
    && names.every(name => Number.isFinite(state.joints?.[name]));
  const pose = { ...config.demo_pose };
  if (live) {
    for (const name of names) pose[name] = state.joints[name];
    const gripper = config.gripper_joint || 'right_finger_joint';
    if (Number.isFinite(state.joints[gripper])) pose[gripper] = state.joints[gripper];
  }
  return {
    pose,
    source: live ? 'Live pose' : 'Demo pose',
  };
}
