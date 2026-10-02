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
