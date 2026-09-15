# Snake experiment operator interfaces

This ROS 2 package implements the independent operator interfaces for the Snake
experiment. Panel A controls
the experiment stack and the selected joystick mapper as separate processes,
records seven end-effector poses from `/ee_pose`, and writes a versioned JSON
calibration file. The session interface implements Panel B (LOT 2) and reserves
the sequential Panel C, D, and E workflow.

Experiment-owned bringup resources are kept under `bringup/cartesian_manager`
and `bringup/joystick_mapper`. Mapper configurations are installed under the
same relative path, while mapper launch files are installed only once in the
package's top-level `launch` directory. The cartesian-manager launch snapshot
mirrors the currently pinned submodule version but remains source-only to avoid
a duplicate `explorer.launch.py` in ROS 2 launch discovery. The panel launch
files in `launch/` remain the executable entry points used by the interfaces.

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

## Panel B: system check-up

Build the same package and launch the second interface independently:

```bash
ros2 launch snake_experiment_ui session_interface.launch.py \
  repository_root:="$(git rev-parse --show-toplevel)"
```

Open <http://127.0.0.1:8081>. Hardware is selected by default; use
`stack_use_simulation:=true` for simulation. Panel B owns only the processes it
starts and refuses to adopt or stop a stack or joystick mapper launched from a
different terminal or interface.

The check-up requires both Baseline and Snake in either order. It verifies the
ROS graph, active controllers, topic types and rates, message validity, mapper
parameters, joystick activity, and an operator confirmation. Baseline loads
`b1`, `b2`, and `b3`; Snake loads only `b1` and `b2`. The current go-to controls
are shown as unavailable and do not block validation.

Diagnostic expectations live in `config/system_checkup.yaml`. Commit
constraints are optional: Git provenance is always recorded, while a mismatch
blocks validation only when `expected_revisions` contains an expected commit.
The Baseline and Snake mapper profiles live in
`bringup/joystick_mapper/config`; these are their only source copies and are
installed with the package.
The completed report remains in backend memory for the future Panel C, so it is
lost if the session-interface process restarts before Panel C persists it.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `host` | `127.0.0.1` | HTTP bind address. |
| `port` | `8081` | HTTP port. |
| `stack_use_simulation` | `false` | Use simulation instead of robot hardware. |
| `diagnostic_profile` | packaged profile | Override the check-up YAML. |
| `measurement_window_sec` | `2.0` | Rolling topic-rate measurement window. |
| `repository_root` | process working directory | Repository used for Git provenance. |
| `mode_startup_timeout_sec` | `5.0` | Mapper startup timeout. |
| `mode_shutdown_timeout_sec` | `5.0` | Mapper shutdown timeout. |
| `stack_startup_timeout_sec` | `30.0` | Stack startup timeout. |
| `stack_shutdown_timeout_sec` | `10.0` | Stack shutdown timeout. |

## JSON contract

Files use `schema_version: 1` and contain the reference frame, active mode,
source topic, UTC timestamps, and the seven named position/quaternion records.
All seven records must use the same non-empty frame before saving is enabled.
