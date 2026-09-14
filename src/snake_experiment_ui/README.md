# Snake experiment calibration interface

This ROS 2 package implements Panel A of the experimenter interface. It controls
the experiment stack and the selected joystick mapper as separate processes,
records seven end-effector poses from `/ee_pose`, and writes a versioned JSON
calibration file.

## Install, build, and run

Use the repository's [isolated workspace procedure](../../README.md#build-in-an-isolated-workspace).
The source paths must remain absolute after changing into the external build
directory. From the Snake repository root, in a shell where only the system ROS
installation has been sourced:

```bash
source /opt/ros/jazzy/setup.bash

snake_src="$(git rev-parse --show-toplevel)"
snake_revision="$(git -C "$snake_src" rev-parse --short HEAD)"
snake_build_root="${SNAKE_BUILD_ROOT:-$(dirname "$snake_src")/workspaces}"
snake_ws="$snake_build_root/snake_experiment_${snake_revision}"

mkdir -p "$snake_ws"
cd "$snake_ws"

mapfile -t snake_packages < <(
  colcon list \
    --base-paths "$snake_src/dependencies" "$snake_src/src" \
    --packages-up-to snake_experiment_ui joystick_mapper \
    --paths-only
)

rosdep install \
  --from-paths "${snake_packages[@]}" \
  --ignore-src \
  --rosdistro jazzy

colcon build \
  --base-paths "$snake_src/dependencies" "$snake_src/src" \
  --packages-up-to snake_experiment_ui joystick_mapper \
  --symlink-install

source "$snake_ws/install/setup.bash"
ros2 launch snake_experiment_ui panel_a.launch.py
```

Open <http://127.0.0.1:8080>. The stack button starts `explorer.launch.py`
without a joystick mapper. The Baseline and Snake buttons independently start
only their corresponding joystick mapper and publish the selected geometric
mode. Stack launches use simulation by default; setting
`stack_use_simulation:=false` selects the hardware path.

Calibration files are stored relative to the directory from which the panel
process is launched. The current calibration is written to
`calibrations/latest_calib.json`. When it is replaced, the previous file is
moved to `calibrations/calib_archives/` with a unique UTC-stamped name.

## Parameters

| Parameter | Default | Purpose |
| --- | --- | --- |
| `host` | `127.0.0.1` | HTTP bind address. |
| `port` | `8080` | HTTP port. |
| `pose_topic` | `/ee_pose` | `PoseStamped` source to record. |
| `pose_max_age_sec` | `1.0` | Maximum accepted pose age. |
| `mode_startup_timeout_sec` | `5.0` | Mapper startup timeout. |
| `mode_shutdown_timeout_sec` | `5.0` | Mapper shutdown timeout. |
| `stack_use_simulation` | `true` | Start simulation instead of robot hardware. |
| `stack_startup_timeout_sec` | `15.0` | Stack startup timeout. |
| `stack_shutdown_timeout_sec` | `10.0` | Stack shutdown timeout. |

The server binds to loopback by default and has no authentication. Do not expose
it on another interface without an appropriate network access policy.

## JSON contract

Files use `schema_version: 1` and contain the reference frame, active mode,
source topic, UTC timestamps, and the seven named position/quaternion records.
All seven records must use the same non-empty frame before saving is enabled.
