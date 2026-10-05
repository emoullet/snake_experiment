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

Open the addresses in the table above. Calibration uses simulation by default;
the session interface selects hardware unless `stack_use_simulation` is set.
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
or pass `calibration_file:=/absolute/path/to/latest_calib.json`:

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

Robot-specific joystick frames, gripper commands, and the participant 3D view
still require adaptation before a full Kinova session can be validated. The
current Kinova manager profile references `effector_frame`; verify its transform
before calibration or movement. The participant preview still displays Explorer.
See the [dependency guide](../../docs/kinova_dependencies.md) for the pinned
Robotiq description required by Kortex.

## Session workflow

1. **Calibration:** activate Baseline or Snake, capture the seven poses, and
   save them. The current file is `calibrations/latest_calib.json`.
2. **System check-up:** verify both modes, ROS diagnostics, and motions to
   `target_1`, `target_2`, `target_3`, and `starting_point`.
3. **Enrollment:** choose a folder, then create or resume a participant. A
   partial session can be resumed without overwriting its saved environment.
4. **Presentation:** open <http://127.0.0.1:8081/participant> on the participant
   screen, show the presentation video, then confirm it in the operator interface.
5. **Experiment:** complete the six discovery, training, and recording blocks
   in order. Acquisitions are saved as MCAP files; interrupted attempts can be
   retried or recorded as deviations.

The supplied success thresholds (5 mm and 5 degrees) are provisional development
values. The supplied discovery instructions are also placeholders and must be
replaced before collecting participant data.

## Files and settings

Relative paths below are resolved from the directory **where `ros2 launch` is
run**. With the commands above, this is the build workspace.

| Item | Path or setting |
| --- | --- |
| Current calibration | `calibrations/latest_calib.json`; the previous version moves to `calibrations/calib_archives/`. |
| Sessions | `sessions_root` (default: launch directory); each participant has a folder and `experiment_progress.json`. |
| Calibration used by sessions | `calibration_file` (default: `calibrations/latest_calib.json` in the launch directory). |
| Diagnostics and sequence | `config/system_checkup.yaml` and `config/experiment.yaml`. |
| Participant videos | `config/participant_interface.yaml`; MP4 paths may be absolute or relative to this file. |

Pass another path to ROS as `name:=value`, for example
`calibration_file:=/path/to/latest_calib.json`. List all launch arguments with:

```bash
ros2 launch snake_experiment_ui session_interface.launch.py --show-args
ros2 launch snake_experiment_ui target_calibration.launch.py --show-args
```

The <http://127.0.0.1:8081/participant/3d-preview> page shows a read-only 3D
preview of the Explorer POC2 arm and gripper. It follows fresh `/joint_states`
data and sends no ROS commands.

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
pnpm run build
```

Commit the generated bundles alongside the changed source files.
