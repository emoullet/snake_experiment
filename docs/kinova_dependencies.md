# Kinova Gen3 dependencies (ROS 2 Jazzy)

The official [ros2_kortex repository](https://github.com/Kinovarobotics/ros2_kortex)
is included as the `dependencies/ros2_kortex` Git submodule. Its pinned revision
is `462dab9aa4732d733be55e1846530dd920c7c7d3` (Jazzy branch, 2026-09-08).
Normal `git submodule update --init --recursive` restores that revision;
do not use `--remote` for reproducible experiment deployments.

The selected packages are `kortex_api`, `kortex_driver`, `kortex_description`,
and `kortex_bringup`. MoveIt packages are not build targets for this experiment.
The compatible Robotiq description is built from the pinned
`dependencies/ros2_robotiq_gripper` submodule at
`a74d007d8f2f06dc6a503ad21038ba869d4999a6`. The Jazzy binary description uses
older macro arguments and fails to expand this Kortex revision's gripper.
Only `robotiq_description` is needed from this repository for the Gen3 internal
bus configuration. Other ROS dependencies, including PickNik controllers, are
installed with rosdep from the Jazzy package repository. The upstream `.repos`
files are not imported: they would also introduce source overlays of shared
ROS control packages already provided by Jazzy.

The upstream build downloads official Kinova API archives (headers at 2.8.0
and the driver library at 2.5.0), with checksums defined in the pinned CMake
files. Initial compilation requires network access to Kinova Artifactory.
This revision supports Linux x86_64.

## Install and build

Use Bash on Ubuntu 24.04 with ROS 2 Jazzy, starting in a fresh terminal.
These commands install only the Kinova portion; the root README covers the
full experiment build and the optional Python web virtual environment.
Replace `/path/to/snake_experiment` with the location of your clone. The build
workspace follows the layout in the root README; adapt `snake_ws` if you use
a different workspace.

```bash
cd /path/to/snake_experiment
source /opt/ros/jazzy/setup.bash
snake_src="$(git rev-parse --show-toplevel)"
git submodule update --init --recursive dependencies/ros2_kortex dependencies/ros2_robotiq_gripper
snake_ws="$(dirname "$snake_src")/workspaces/snake_experiment"
mkdir -p "$snake_ws"
cd "$snake_ws"

mapfile -t kinova_packages < <(
  colcon list --base-paths "$snake_src/dependencies/ros2_kortex" \
    "$snake_src/dependencies/ros2_robotiq_gripper" \
    --packages-up-to kortex_bringup --paths-only
)
rosdep install --from-paths "${kinova_packages[@]}" \
  --ignore-src --rosdistro jazzy -y
colcon build --base-paths "$snake_src/dependencies/ros2_kortex" \
  "$snake_src/dependencies/ros2_robotiq_gripper" \
  --packages-up-to kortex_bringup --symlink-install --parallel-workers 2 \
  --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
```

`rosdep install` requires sudo and may ask for your password. If using the web
venv option, follow the root README instead so the full experiment uses the
same interpreter. No existing service needs to be restarted for installation.
Source the workspace in each new terminal before using the new packages.

## Verify dependencies and mock hardware

After the build, in the same terminal:

```bash
rosdep check --from-paths "${kinova_packages[@]}" \
  --ignore-src --rosdistro jazzy
ros2 pkg prefix kortex_api
ros2 pkg prefix kortex_driver
ros2 pkg prefix kortex_description
ros2 pkg prefix kortex_bringup
ros2 launch kortex_bringup gen3.launch.py --show-args
ros2 launch kortex_bringup gen3.launch.py \
  robot_ip:=192.168.1.10 dof:=7 gripper:=robotiq_2f_85 \
  use_fake_hardware:=true launch_rviz:=true
```

Keep `use_fake_hardware:=true` for this check. This starts standalone mock
hardware, not the experiment or Gazebo. Avoid running another robot stack at
the same time. In a second Bash terminal:

```bash
cd /path/to/snake_experiment
snake_src="$(git rev-parse --show-toplevel)"
snake_ws="$(dirname "$snake_src")/workspaces/snake_experiment"
cd "$snake_ws"
source /opt/ros/jazzy/setup.bash
source install/setup.bash
ros2 control list_controllers
ros2 topic echo /joint_states --once
```

Manually check that RViz displays the seven-axis Gen3 with the Robotiq 2F-85,
that the launched controllers are active, and that `/joint_states` contains
`joint_1` through `joint_7` with finite positions. Stop the standalone launch
with Ctrl+C. No web-page refresh is needed at this stage.

## Validation performed during step 1

- All four packages compiled successfully against the local ROS 2 Jazzy in
  `/tmp/snake-kinova-step1`; only upstream CMake timestamp-policy warnings were
  reported. This temporary workspace is not the normal experiment workspace.
- All four installed packages were found with `ros2 pkg prefix`, and
  `gen3.launch.py --show-args` loaded successfully (using a writable temporary
  ROS log directory in the agent sandbox).
- `rosdep check` identified six missing system packages:
  `ros-jazzy-velocity-controllers`, `ros-jazzy-robotiq-description`,
  `ros-jazzy-parallel-gripper-controller`,
  `ros-jazzy-picknik-reset-fault-controller`,
  `ros-jazzy-picknik-twist-controller`, and `ros-jazzy-gripper-controllers`.
- Their installation could not finish because sudo requires an interactive
  password. Runtime validation with the Robotiq model, RViz, and mock hardware
  remains to be performed after installation. No physical robot was started.

The calibration and session interfaces select Kinova with `stack_robot:=kinova`
and accept `stack_robot_ip`. The integration checks below cover profile
selection and fake-hardware lifecycle; they do not exercise physical robot
commands or browser rendering. See the
[interface guide](../src/snake_experiment_ui/README.md#select-the-robot).

### Robot selection integration checks

During step 2, the interface package and compatible `robotiq_description` were
built in `/tmp/snake-kinova-step2`. Calibration and session were tested
separately on isolated ROS domains and HTTP ports, with `stack_robot:=kinova`,
a custom IP, and fake hardware. Both returned the selected robot/IP in their
API state, started and stopped their owned stack, and published `/joint_states`
and `/ee_pose` in `base_link`. All three experiment controllers activated and
no joystick mapper was started. Session used a synthetic temporary calibration;
no pose-target or joystick commands were sent. Physical operation and browser
visual verification were not tested.

The Python suite finished with 148 passing tests and one pre-existing failure:
`test_cartesian_manager_launch_matches_submodule` compares the old Explorer
launch snapshot with a newer dependency launch. Neither launch was modified by
step 2. The ten selection/lifecycle/layout checks passed when excluding that
comparison. Both edited JavaScript files passed Node's syntax check.
