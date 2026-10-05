import assert from 'node:assert/strict';
import test from 'node:test';
import { Euler, Group, Quaternion, Vector3 } from 'three';
import { axisLabels, commandForAxis, cycleBasePose, joystickDisplacement } from '../src/mapping.js';
import { ARM_JOINTS, cyclePose, effectorPose, rotationVector, setArmPose, solveAxisEndpoints } from '../src/kinematics.js';

const component = (index, scale = 1) => ({ index, scale });
const axes = (x, y, z, rx, ry, rz) => ({
  linear_x: component(x), linear_y: component(y), linear_z: component(z),
  angular_x: component(rx), angular_y: component(ry), angular_z: component(rz),
});
const config = {
  animation: { linear_mm: 40, angular_deg: 7, loop_sec: 4 },
  frames: { base: 'base_link', ee: 'ft_frame' },
  physical_axis_signs: { right: null, up: null },
  snake_gain: 3,
  demo_pose: {
    joint_1: 0, joint_2: 1.71, joint_3: 2.5,
    joint_4: 0, joint_5: -0.17, joint_6: 0, right_finger_joint: 0,
  },
  mapper: {
    baseline: {
      b1: { angular_frame: 'base_link', axes: axes(0, 1, -1, -1, -1, -1) },
      b2: { angular_frame: 'ft_frame', axes: axes(-1, -1, 0, -1, -1, 1) },
      b3: { angular_frame: 'ft_frame', axes: axes(-1, -1, -1, 0, 1, -1) },
    },
    snake: {
      b1: { angular_frame: 'base_link', axes: axes(0, 1, -1, -1, -1, -1) },
      b2: { angular_frame: 'ft_frame', axes: axes(-1, -1, 0, -1, -1, 1) },
    },
  },
};

function robotFixture(limit = 2.5) {
  const robot = new Group();
  const base = new Group();
  robot.add(base);
  let parent = base;
  const axesByJoint = ['z', 'y', 'y', 'x', 'y', 'x'];
  const positions = [[0, 0, 0], [0, 0, 0.2], [0.25, 0, 0], [0.2, 0, 0], [0.1, 0, 0], [0.1, 0, 0]];
  robot.joints = {};
  for (const [index, name] of ARM_JOINTS.entries()) {
    const joint = new Group();
    joint.position.set(...positions[index]);
    joint.jointType = 'revolute';
    joint.limit = { lower: -limit, upper: limit };
    joint.axis = axesByJoint[index];
    parent.add(joint);
    robot.joints[name] = joint;
    parent = joint;
  }
  const tool = new Group();
  tool.position.x = 0.12;
  parent.add(tool);
  robot.links = { base_link: base, ft_frame: tool };
  robot.setJointValue = (name, value) => {
    const joint = robot.joints[name];
    if (!joint) return;
    joint.rotation[joint.axis] = Math.min(joint.limit.upper, Math.max(joint.limit.lower, value));
  };
  return robot;
}

// Explorer POC2 base_link -> ft_frame geometry from the installed visual URDF.
function explorerFixture() {
  const robot = new Group();
  const base = new Group();
  robot.add(base);
  const joints = [
    [[0, 0, 0.0555], [0, 0, 0], [-2.97, 2.97]],
    [[0, 0.0265, 0.058952], [Math.PI / 2, -Math.PI / 2, 0], [-2.14, 2.14]],
    [[0.413, 0, 0], [Math.PI, 0, 0], [-2.97, 2.97]],
    [[0.1885, 0, -0.008], [Math.PI / 2, Math.PI, Math.PI / 2], [-2.97, 2.97]],
    [[0, 0.0285, 0.1], [Math.PI / 2, 0, Math.PI], [-1.8, 1.8]],
    [[0, 0.1555, -0.027], [Math.PI / 2, Math.PI, Math.PI], [-2.97, 2.97]],
  ];
  let parent = base;
  robot.joints = {};
  for (const [index, [position, rpy, limits]] of joints.entries()) {
    const joint = new Group();
    joint.position.set(...position);
    joint.originRotation = new Quaternion().setFromEuler(new Euler(...rpy, 'ZYX'));
    joint.quaternion.copy(joint.originRotation);
    joint.jointType = 'revolute';
    joint.limit = { lower: limits[0], upper: limits[1] };
    parent.add(joint);
    robot.joints[`joint_${index + 1}`] = joint;
    parent = joint;
  }
  const tool = new Group();
  tool.position.set(0.011, 0, 0.15);
  parent.add(tool);
  robot.links = { base_link: base, ft_frame: tool };
  robot.setJointValue = (name, value) => {
    const joint = robot.joints[name];
    if (joint) joint.quaternion.copy(joint.originRotation).multiply(
      new Quaternion().setFromAxisAngle(new Vector3(0, 0, 1), value),
    );
  };
  return robot;
}

const samplePose = {
  joint_1: 0.3, joint_2: -0.5, joint_3: 0.6,
  joint_4: 0.1, joint_5: 0.2, joint_6: -0.1, right_finger_joint: 0.4,
};

test('all five mode/sub-mode combinations use both axes with symmetric signs', () => {
  const identity = new Quaternion();
  assert.equal(Object.keys(config.mapper.baseline).length + Object.keys(config.mapper.snake).length, 5);
  for (const [mode, mappings] of Object.entries(config.mapper)) {
    for (const submode of Object.keys(mappings)) {
      for (const axis of [0, 1]) {
        const positive = commandForAxis(config, mode, submode, axis, 1, false, identity);
        const negative = commandForAxis(config, mode, submode, axis, -1, false, identity);
        assert.ok(positive.linear.clone().add(negative.linear).length() < 1e-10);
        assert.ok(positive.angular.clone().add(negative.angular).length() < 1e-10);
        assert.ok(positive.linear.length() + positive.angular.length() > 0);
      }
    }
  }
});

test('effector frame rotates angular only, and Snake coupling follows tool z cross linear', () => {
  const rotated = new Quaternion().setFromAxisAngle(new Vector3(1, 0, 0), Math.PI / 2);
  const b2 = commandForAxis(config, 'baseline', 'b2', 1, 1, false, rotated);
  assert.ok(b2.angular.distanceTo(new Vector3(0, -1, 0)) < 1e-10);
  const b2Linear = commandForAxis(config, 'baseline', 'b2', 0, 1, false, rotated);
  assert.ok(b2Linear.linear.distanceTo(new Vector3(0, 0, 1)) < 1e-10);
  const released = commandForAxis(config, 'snake', 'b1', 0, 1, false, new Quaternion());
  const held = commandForAxis(config, 'snake', 'b1', 0, 1, true, new Quaternion());
  assert.equal(released.angular.length(), 0);
  assert.ok(held.angular.distanceTo(new Vector3(0, 3, 0)) < 1e-10);
  assert.ok(held.linear.equals(released.linear));
  const lowerGain = commandForAxis({ ...config, snake_gain: 1 }, 'snake', 'b1', 0, 1, true, new Quaternion());
  assert.ok(lowerGain.angular.distanceTo(new Vector3(0, 1, 0)) < 1e-10);
  // A pure angular command is unaffected by Snake's cross product.
  const angularReleased = commandForAxis(config, 'snake', 'b2', 1, 1, false, rotated);
  const angularHeld = commandForAxis(config, 'snake', 'b2', 1, 1, true, rotated);
  assert.ok(angularHeld.angular.equals(angularReleased.angular));
});

test('physical directions remain unknown until explicitly configured', () => {
  assert.deepEqual(axisLabels(config, 0), ['+', '−']);
  assert.deepEqual(axisLabels(config, 1), ['+', '−']);
  const known = { ...config, physical_axis_signs: { right: -1, up: 1 } };
  assert.deepEqual(axisLabels(known, 0), ['Left', 'Right']);
  assert.deepEqual(axisLabels(known, 1), ['Up', 'Down']);
});

test('schematic sticks share the arm cycle, with right/up positive and exact neutral', () => {
  const known = { ...config, physical_axis_signs: { right: 1, up: 1 } };
  assert.deepEqual(axisLabels(known, 0), ['Right', 'Left']);
  assert.deepEqual(axisLabels(known, 1), ['Up', 'Down']);
  for (const phase of [0, 0.5, 1]) {
    assert.deepEqual(joystickDisplacement(known, 0, phase), { x: 0, y: 0, input: Math.sin(2 * Math.PI * phase) });
    assert.equal(joystickDisplacement(known, 1, phase).y, 0);
  }
  assert.equal(joystickDisplacement(known, 0, 0.25).x, 32);
  assert.equal(joystickDisplacement(known, 0, 0.75).x, -32);
  assert.equal(joystickDisplacement(known, 1, 0.25).y, -32);
  assert.equal(joystickDisplacement(known, 1, 0.75).y, 32);
  const reversed = { ...known, physical_axis_signs: { right: -1, up: -1 } };
  assert.equal(joystickDisplacement(reversed, 0, 0.25).x, -32);
  assert.equal(joystickDisplacement(reversed, 1, 0.25).y, 32);
});

test('each loop uses a new complete live pose or an explicitly labeled demo pose', () => {
  const first = cycleBasePose(config, { robot: 'explorer_poc2', status: 'live', joints: samplePose });
  assert.equal(first.source, 'Live pose');
  assert.equal(first.pose.joint_1, samplePose.joint_1);
  const next = cycleBasePose(config, {
    robot: 'explorer_poc2', status: 'live',
    joints: { ...samplePose, joint_1: samplePose.joint_1 + 0.2 },
  });
  assert.equal(next.pose.joint_1, samplePose.joint_1 + 0.2);
  const stale = cycleBasePose(config, { robot: 'explorer_poc2', status: 'stale', joints: samplePose });
  assert.equal(stale.source, 'Demo pose');
  assert.equal(stale.pose.joint_1, config.demo_pose.joint_1);
  assert.equal(cycleBasePose(config, null).source, 'Demo pose');
  assert.equal(cycleBasePose(config, { robot: 'explorer_poc2', status: 'live', joints: { joint_1: 0 } }).source, 'Demo pose');
});

test('IK endpoints stay within joint limits and exact neutral returns at half/full cycle', () => {
  const robot = robotFixture();
  const endpoints = solveAxisEndpoints(robot, samplePose, config, 'baseline', 'b1', 1, false);
  assert.ok(endpoints.fraction > 0);
  assert.notDeepEqual(endpoints.positive, endpoints.negative);
  for (const pose of [endpoints.positive, endpoints.negative]) {
    for (const name of ARM_JOINTS) assert.ok(Math.abs(pose[name]) <= 2.5);
  }
  assert.deepEqual(cyclePose(endpoints, 0), endpoints.start);
  assert.deepEqual(cyclePose(endpoints, 0.5), endpoints.start);
  assert.deepEqual(cyclePose(endpoints, 1), endpoints.start);
  assert.deepEqual(cyclePose(endpoints, 0.25), endpoints.positive);
  assert.deepEqual(cyclePose(endpoints, 0.75), endpoints.negative);
  assert.equal(cyclePose(endpoints, 0.25).right_finger_joint, samplePose.right_finger_joint);
});

test('cycle can start from a newly captured live pose and reduces impossible amplitudes', () => {
  const robot = robotFixture();
  const first = solveAxisEndpoints(robot, samplePose, config, 'baseline', 'b1', 1, false);
  const newPose = { ...samplePose, joint_1: samplePose.joint_1 + 0.2 };
  const second = solveAxisEndpoints(robot, newPose, config, 'baseline', 'b1', 1, false);
  assert.equal(second.start.joint_1, newPose.joint_1);
  assert.notDeepEqual(first.start, second.start);
  const constrained = robotFixture(0.15);
  const neutral = Object.fromEntries(ARM_JOINTS.map(name => [name, 0]));
  const large = { ...config, animation: { ...config.animation, linear_mm: 180 } };
  const reduced = solveAxisEndpoints(constrained, neutral, large, 'baseline', 'b1', 1, false);
  assert.ok(reduced.fraction < 1);
  for (const pose of [reduced.positive, reduced.negative]) {
    for (const name of ARM_JOINTS) assert.ok(Math.abs(pose[name]) <= 0.15 + 1e-10);
  }
});

test('Explorer POC2 demo pose yields feasible motion for every configured axis', () => {
  for (const [mode, mappings] of Object.entries(config.mapper)) {
    for (const submode of Object.keys(mappings)) {
      for (const axis of [0, 1]) {
        const robot = explorerFixture();
        const endpoints = solveAxisEndpoints(robot, config.demo_pose, config, mode, submode, axis, mode === 'snake');
        assert.ok(endpoints.fraction > 0, `${mode}/${submode}/axis${axis} should animate`);
        setArmPose(robot, endpoints.start);
        const neutral = effectorPose(robot);
        const command = commandForAxis(config, mode, submode, axis, 1, mode === 'snake', neutral.rotation);
        setArmPose(robot, endpoints.positive);
        const moved = effectorPose(robot);
        if (command.linear.length() > 0) {
          assert.ok(moved.position.clone().sub(neutral.position).dot(command.linear) > 0, `${mode}/${submode}/axis${axis} linear direction`);
        }
        if (command.angular.length() > 0) {
          const rotation = rotationVector(moved.rotation.clone().multiply(neutral.rotation.clone().invert()));
          assert.ok(rotation.dot(command.angular) > 0, `${mode}/${submode}/axis${axis} angular direction`);
        }
        for (const pose of [endpoints.positive, endpoints.negative]) {
          for (const name of ARM_JOINTS) {
            assert.ok(pose[name] >= robot.joints[name].limit.lower - 1e-10);
            assert.ok(pose[name] <= robot.joints[name].limit.upper + 1e-10);
          }
        }
      }
    }
  }
});

// Geometry and limits from the pinned Gen3 7-DoF visual URDF.
function kinovaFixture() {
  const robot = new Group();
  const base = new Group(); robot.add(base);
  let parent = base;
  const geometry = [
    [[0, 0, .15643], Math.PI, [-2*Math.PI, 2*Math.PI]],
    [[0, .005375, -.12838], Math.PI/2, [-2.24, 2.24]],
    [[0, -.21038, -.006375], -Math.PI/2, [-2*Math.PI, 2*Math.PI]],
    [[0, .006375, -.21038], Math.PI/2, [-2.57, 2.57]],
    [[0, -.20843, -.006375], -Math.PI/2, [-2*Math.PI, 2*Math.PI]],
    [[0, .00017505, -.10593], Math.PI/2, [-2.09, 2.09]],
    [[0, -.10593, -.00017505], -Math.PI/2, [-2*Math.PI, 2*Math.PI]],
  ];
  robot.joints = {};
  geometry.forEach(([position, roll, limits], i) => {
    const joint = new Group(); joint.position.set(...position);
    joint.originRotation = new Quaternion().setFromEuler(new Euler(roll, 0, 0, 'ZYX'));
    joint.quaternion.copy(joint.originRotation);
    joint.jointType = 'revolute'; joint.limit = { lower: limits[0], upper: limits[1] };
    parent.add(joint); parent = joint; robot.joints[`joint_${i+1}`] = joint;
  });
  const tool = new Group(); tool.position.z = -.061525;
  tool.rotation.x = Math.PI; parent.add(tool);
  robot.links = { base_link: base, end_effector_link: tool };
  robot.setJointValue = (name, value) => {
    const joint = robot.joints[name];
    if (joint) joint.quaternion.copy(joint.originRotation).multiply(
      new Quaternion().setFromAxisAngle(new Vector3(0, 0, 1), value));
  };
  return robot;
}
const kinovaConfig = {
  ...config, robot: 'kinova_gen3', arm_joints: Array.from({length: 7}, (_, i) => `joint_${i+1}`),
  gripper_joint: 'robotiq_85_left_knuckle_joint', frames: { base: 'base_link', ee: 'end_effector_link' },
  demo_pose: {joint_1: 0, joint_2: .2617, joint_3: 3.1415, joint_4: -2.26,
    joint_5: 0, joint_6: .96, joint_7: 1.57, robotiq_85_left_knuckle_joint: 0},
  mapper: Object.fromEntries(Object.entries(config.mapper).map(([mode, mappings]) => [mode,
    Object.fromEntries(Object.entries(mappings).map(([name, mapping]) => [name, {
      ...mapping, angular_frame: mapping.angular_frame === 'ft_frame' ? 'end_effector_link' : mapping.angular_frame,
    }]))])),
};
test('Kinova live pose requires all seven axes and falls back to home with missing or wrong states', () => {
  const live = {robot: 'kinova_gen3', status: 'live', joints: {...kinovaConfig.demo_pose, joint_7: 2.1}};
  assert.equal(cycleBasePose(kinovaConfig, live).pose.joint_7, 2.1);
  delete live.joints.robotiq_85_left_knuckle_joint;
  assert.equal(cycleBasePose(kinovaConfig, live).source, 'Live pose');
  assert.equal(cycleBasePose(kinovaConfig, live).pose.robotiq_85_left_knuckle_joint, 0);
  for (const state of [{...live, status: 'stale'}, {...live, robot: 'explorer_poc2'},
    {...live, joints: {...live.joints, joint_7: NaN}}]) {
    assert.deepEqual(cycleBasePose(kinovaConfig, state).pose, kinovaConfig.demo_pose);
    assert.equal(cycleBasePose(kinovaConfig, state).source, 'Demo pose');
  }
});
test('Kinova seven-axis IK animates every mapping and Snake state, participates in redundancy and returns neutral', () => {
  let seventhMoved = false;
  for (const [mode, mappings] of Object.entries(kinovaConfig.mapper)) {
    for (const submode of Object.keys(mappings)) for (const axis of [0, 1]) {
      for (const held of mode === 'snake' ? [false, true] : [false]) {
        const robot = kinovaFixture();
        const endpoints = solveAxisEndpoints(robot, kinovaConfig.demo_pose, kinovaConfig, mode, submode, axis, held);
        assert.ok(endpoints.fraction > 0, `${mode}/${submode}/${axis}/${held}`);
        seventhMoved ||= Math.abs(endpoints.positive.joint_7 - endpoints.start.joint_7) > 1e-5;
        for (const pose of [endpoints.positive, endpoints.negative]) for (const name of kinovaConfig.arm_joints) {
          assert.ok(Number.isFinite(pose[name]));
          assert.ok(pose[name] >= robot.joints[name].limit.lower && pose[name] <= robot.joints[name].limit.upper);
        }
        assert.deepEqual(cyclePose(endpoints, .5), endpoints.start);
        assert.deepEqual(cyclePose(endpoints, 1), endpoints.start);
        setArmPose(robot, endpoints.start, kinovaConfig);
        const neutral = effectorPose(robot, kinovaConfig);
        const command = commandForAxis(kinovaConfig, mode, submode, axis, 1, held, neutral.rotation);
        setArmPose(robot, endpoints.positive, kinovaConfig);
        const moved = effectorPose(robot, kinovaConfig);
        if (command.linear.length()) assert.ok(moved.position.clone().sub(neutral.position).dot(command.linear) > 0);
        if (command.angular.length()) assert.ok(rotationVector(moved.rotation.clone().multiply(neutral.rotation.clone().invert())).dot(command.angular) > 0);
      }
    }
  }
  assert.ok(seventhMoved);
});
test('continuous joints stay near their unwrapped seed; unreachable Kinova targets remain neutral', () => {
  const robot = kinovaFixture();
  robot.joints.joint_7.jointType = 'continuous';
  const start = {...kinovaConfig.demo_pose, joint_7: 4*Math.PI + 1.57};
  const result = solveAxisEndpoints(robot, start, kinovaConfig, 'baseline', 'b2', 1, false);
  assert.equal(result.start.joint_7, start.joint_7);
  assert.ok(Math.abs(result.positive.joint_7 - start.joint_7) < 1);
  const locked = kinovaFixture();
  for (const name of kinovaConfig.arm_joints) locked.joints[name].limit = {
    lower: kinovaConfig.demo_pose[name], upper: kinovaConfig.demo_pose[name],
  };
  const impossible = solveAxisEndpoints(locked, kinovaConfig.demo_pose, {...kinovaConfig, animation: {...kinovaConfig.animation, linear_mm: 200}}, 'baseline', 'b1', 0, false);
  assert.equal(impossible.fraction, 0);
  assert.deepEqual(impossible.positive, impossible.start);
});
