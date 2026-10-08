# Snake experiment interfaces

The `snake_experiment_ui` package provides two independent web interfaces:

| Interface | Purpose | Address |
| --- | --- | --- |
| Calibration | Start the stack and a control mode, capture seven poses from `/ee_pose`, and save the calibration. | <http://127.0.0.1:8080> |
| Session | Check the system, enroll a participant, and run the experiment sequence. | <http://127.0.0.1:8081> |

## Start the interfaces

[Build the repository in a separate workspace first](../../README.md). In
**each terminal**, start from the repository root and source that workspace:

```bash
source /opt/ros/jazzy/setup.bash
snake_src="$(git rev-parse --show-toplevel)"
cd "$(dirname "$snake_src")/workspaces/snake_experiment"
if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi
source install/setup.bash
```

In the first terminal, start calibration:

```bash
ros2 launch snake_experiment_ui target_calibration.launch.py
```

In the second terminal, start the session interface in simulation:

```bash
ros2 launch snake_experiment_ui session_interface.launch.py \
  repository_root:="$snake_src" stack_use_simulation:=true
```

Open the addresses in the table above. Both interfaces select hardware by
default; pass `stack_use_simulation:=true` to use simulation.
Each interface controls only the processes it started. Avoid starting the
robot stack from both interfaces at the same time. The servers bind to
`127.0.0.1` and have no authentication.

## Select the robot

Both interfaces accept `stack_robot:=explorer|kinova` (default: `explorer`)
and `stack_robot_ip:=192.168.1.10`. The IP must be a valid IPv4 address for
Kinova and is ignored for Explorer. Selection is fixed when the interface
starts; relaunch it to select another robot or IP. The operator page displays
the robot, simulation/hardware mode, and the Kinova IP.

After building and sourcing the workspace as above, launch calibration for
the seven-axis Kinova Gen3 with Robotiq 2F-85 in fake hardware:

```bash
ros2 launch snake_experiment_ui target_calibration.launch.py \
  stack_robot:=kinova stack_robot_ip:=192.168.1.10 stack_use_simulation:=true
```

Stop its stack from the page and stop the interface with Ctrl+C before testing
the session interface separately. The session check-up requires an existing
calibration file before it will start the stack; use the file saved by calibration
or pass `calibration_file:=/absolute/path/to/latest_calib_kinova.json`:

```bash
ros2 launch snake_experiment_ui session_interface.launch.py \
  repository_root:="$snake_src" stack_robot:=kinova \
  stack_robot_ip:=192.168.1.10 stack_use_simulation:=true
```

Open port 8080 for calibration or 8081 for session. Verify the displayed
selection, then use the stack Start/Stop button. The Kinova stack starts the
robot controllers, cartesian manager, and joystick input; mapper launch remains
owned by the Baseline/Snake buttons. `stack_use_simulation:=true` uses fake
hardware, not Gazebo. `false` selects physical hardware; the session interface
defaults to `false`, so explicitly pass `true` for simulated checks.
RViz opens automatically when the Kinova stack starts in simulation, using
`config/kinova.rviz` to display the arm and gripper in `base_link`. Stopping
the stack closes its RViz process. The physical Kinova stack does not launch RViz.

Each mapper launch selects its YAML file with `robot:=explorer|kinova`; the
interfaces supply the robot automatically. The four profiles are
`joystick_2d_baseline_explorer.yaml`, `joystick_2d_snake_explorer.yaml`,
`joystick_2d_baseline_kinova.yaml`, and `joystick_2d_snake_kinova.yaml` in
`bringup/joystick_mapper/config/`. They replace the old filenames without a robot
suffix. `config_file` remains available for an explicit custom profile; there
are no angular-frame parameter overrides after loading the YAML.

Kinova profiles use `end_effector_link` for b2/b3; Explorer profiles retain
`ft_frame`. The Kinova manager also uses `end_effector_link`. The default session
check-up selects `system_checkup_kinova.yaml` for Kinova and `system_checkup.yaml`
for Explorer, so it verifies the matching mapper profile. An explicit
`diagnostic_profile` still takes precedence. Gripper commands and a complete
Kinova experiment still require validation.
See the [dependency guide](../../docs/kinova_dependencies.md) for the pinned
Robotiq description required by Kortex.

## Session workflow

1. **Calibration:** activate Baseline or Snake, capture the seven poses, and
   save them. The current file is `calibrations/latest_calib_explorer.json` or
   `calibrations/latest_calib_kinova.json`, selected by `stack_robot`.
2. **System check-up:** verify both modes, ROS diagnostics, and motions to
   `target_1`, `target_2`, `target_3`, and `starting_point`.
3. **Enrollment:** choose a folder, then create or resume a participant. A
   partial session can be resumed without overwriting its saved environment.
4. **Presentation:** open <http://127.0.0.1:8081/participant> on the participant
   screen, show the presentation video, then confirm it in the operator interface.
5. **Experiment:** complete the six discovery, training, and recording blocks
   in order. Acquisitions are saved as MCAP files; interrupted attempts can be
   retried or recorded as deviations.

Calibrations record their source robot (`explorer` or `kinova`), model, and
end-effector frame under `robot` in their current calibration file. The check-up, enrolment,
session resume, and experiment preparation require the same robot and tool frame.
A robot mismatch is blocking even when an environment mismatch is acknowledged.
Older calibrations without this metadata have an unknown origin and must be
recreated with the selected robot. Simulation and hardware may share a calibration
for the same robot; the existing reference-frame checks still apply.

Each new participant's `manifest.json` records the launch-selected configuration
under `robot`: robot ID/model/label, `use_simulation`, Kinova IP (null for
Explorer), and paths to the snapshotted Baseline/Snake joystick profiles.
`robot_history` records that configuration with the check-up ID and timestamp
at creation and each resume. A configuration change uses the existing
environment-mismatch acknowledgement before resuming and preserves the original
`robot` record. Older manifests without robot metadata are treated as unknown;
acknowledging their first resume records the current configuration in the history
without assigning a robot retrospectively to the original session.

The supplied success thresholds (5 mm and 5 degrees) are provisional development
values. The supplied discovery instructions are also placeholders and must be
replaced before collecting participant data.

## Files and settings

Relative paths below are resolved from the directory **where `ros2 launch` is
run**. With the commands above, this is the build workspace.

| Item | Path or setting |
| --- | --- |
| Current calibration | `calibrations/latest_calib_explorer.json` or `calibrations/latest_calib_kinova.json`, selected by `stack_robot`; previous versions move to `calibrations/calib_archives/` with the robot in their filename. |
| Sessions | `sessions_root` (default: launch directory); each participant has a folder and `experiment_progress.json`. |
| Calibration used by sessions | `calibration_file` (default: the selected robot’s current calibration in the launch directory). |
| Diagnostics and sequence | `config/system_checkup.yaml` (Explorer), `config/system_checkup_kinova.yaml` (Kinova), and `config/experiment.yaml`. |
| Participant videos | `config/participant_interface.yaml`; MP4 paths may be absolute or relative to this file. |

Saving one robot’s calibration does not replace or archive the other robot’s
current file. The old shared `calibrations/latest_calib.json` is not used
automatically. If it contains valid robot metadata, it may be copied to the
matching new filename or passed explicitly through `calibration_file`.
Participant snapshots keep `calibration/latest_calib.json` inside each participant
folder; their robot compatibility checks still apply.

Pass another path to ROS as `name:=value`, for example
`calibration_file:=/path/to/latest_calib.json`. List all launch arguments with:

```bash
ros2 launch snake_experiment_ui session_interface.launch.py --show-args
ros2 launch snake_experiment_ui target_calibration.launch.py --show-args
```

The <http://127.0.0.1:8081/participant/3d-preview> page shows a read-only 3D
preview of the robot selected by `stack_robot`, as do the animations embedded
in the participant page. Explorer has six arm joints; Kinova Gen3 has seven
and a Robotiq 2F-85 gripper. The model is expanded from the installed description
packages without starting robot processes. Changing robots requires relaunching
the interface.

The animations follow fresh `/joint_states` only when all arm joints are valid.
Otherwise, they use the existing Explorer demo pose or the `home` pose from the
installed Kinova `cartesian_manager` profile. A missing gripper joint leaves the
arm live and displays an open demo gripper. Animations use the selected robot's
Baseline/Snake mappings and an illustrative damped inverse kinematics solver;
they send no ROS commands and do not reproduce the controller exactly.

## Missing Python module

If launching an interface reports a missing `uvicorn` or `fastapi` module,
follow the matching [installation option](../../README.md#install-and-build).
Check which interpreter the installed calibration node uses:

```bash
head -n 1 "$(ros2 pkg prefix snake_experiment_ui)/lib/snake_experiment_ui/target_calibration_node"
```

Run the import check for your option in the same shell as `ros2 launch`. If its
interpreter differs from the one used to install the packages, rebuild in a
fresh workspace using that option, then relaunch. `colcon build` does not
install Python dependencies.

## Develop the 3D preview

JavaScript bundles are included in the package, so a normal ROS build does not
require Node. After changing `frontend/participant_3d/src/`, rebuild the
bundles from the repository root:

```bash
cd src/snake_experiment_ui/frontend/participant_3d
pnpm install --frozen-lockfile
pnpm test
pnpm run build
```

Commit the generated bundles alongside the changed source files.

After rebuilding the bundles, rebuild the ROS package and relaunch the session
interface (stop the previous instance first). For the configured workspace:

```bash
cd /home/emoullet/dev/robot_experiments/workspaces/snake_experiment_a286641
source /opt/ros/jazzy/setup.bash
colcon build --base-paths /home/emoullet/dev/robot_experiments/snake_experiment/src --packages-select snake_experiment_ui --symlink-install
source install/setup.bash
ros2 launch snake_experiment_ui session_interface.launch.py \
  repository_root:=/home/emoullet/dev/robot_experiments/snake_experiment \
  stack_robot:=kinova stack_use_simulation:=true
```

Use `stack_robot:=explorer` to check Explorer separately. Refresh
<http://127.0.0.1:8081/participant> and
<http://127.0.0.1:8081/participant/3d-preview> with Ctrl+Shift+R. In participant
view C and on the dedicated page, check Baseline b1/b2/b3 and Snake with the
button released/held, the complete meshes, labels, framing, and return to
neutral. Check both demo fallback without joint states and live poses with
fresh joint states. Run only one robot stack at a time.
