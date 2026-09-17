# Snake experiment operator interfaces

This ROS 2 package implements the independent operator interfaces for the Snake
experiment. Panel A controls
the experiment stack and the selected joystick mapper as separate processes,
records seven end-effector poses from `/ee_pose`, and writes a versioned JSON
calibration file. The session interface implements Panel B (LOT 2) and Panel C
(LOT 3), then reserves the sequential Panel D and E workflow.

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
The completed report remains in backend memory until Panel C creates or resumes
a participant. Panel C then copies it into the participant's check-up history
and marks it as the active report.

| Parameter | Default | Purpose |
| --- | --- | --- |
| `host` | `127.0.0.1` | HTTP bind address. |
| `port` | `8081` | HTTP port. |
| `stack_use_simulation` | `false` | Use simulation instead of robot hardware. |
| `diagnostic_profile` | packaged profile | Override the check-up YAML. |
| `experiment_profile` | packaged profile | Override the versioned block-sequence YAML. |
| `measurement_window_sec` | `2.0` | Rolling topic-rate measurement window. |
| `repository_root` | process working directory | Repository used for Git provenance. |
| `sessions_root` | process working directory | Root boundary exposed by the server-side folder browser. |
| `calibration_file` | `<cwd>/calibrations/latest_calib.json` | Calibration copied into each participant environment. |
| `mode_startup_timeout_sec` | `5.0` | Mapper startup timeout. |
| `mode_shutdown_timeout_sec` | `5.0` | Mapper shutdown timeout. |
| `stack_startup_timeout_sec` | `30.0` | Stack startup timeout. |
| `stack_shutdown_timeout_sec` | `10.0` | Stack shutdown timeout. |
| `rosbag_startup_timeout_sec` | `5.0` | Recorder startup timeout. |
| `rosbag_shutdown_timeout_sec` | `10.0` | Recorder shutdown timeout. |

## Panel C: enrolment and session resume

After Panel B validation, select an experiment folder below `sessions_root`.
The interface creates `experiment_state.csv` when absent, using the columns
`session_date`, `pseudonym`, `gathered_consent`, `handedness`,
`joystick_experience`, `visual_or_motor_impairment`, `starting_time`,
`experimental_plan`, and `state`.

New participants receive a collision-safe six-character pseudonym and the least
represented experimental plan. Their folder contains the current calibration,
the packaged bringup and experiment-profile snapshots, the Panel B report
history, a Git provenance record, and a SHA-256 manifest. Partial sessions can
be resumed; any bringup, profile, or calibration difference requires explicit
operator acknowledgement and never overwrites the saved environment.

Cancelling a participant created during the current Panel C visit removes its
new folder and CSV row. Cancelling a resumed participant only clears the UI
selection. Closing the browser or stopping the interface preserves partial
sessions. Launching prepares durable experiment progress and advances to Panel D
without starting the stack or a mapper.

## Panel D: experiment sequence

Panel D resolves `mode_1` and `mode_2` from the participant's counterbalanced
plan and enforces the six blocks in `config/experiment.yaml`: both discovery
blocks, then training and recording for mode 1, followed by training and
recording for mode 2. Starting a block starts the owned stack when needed and
activates only that block's mapper. Ending a block requires confirmation;
stopping and returning marks it interrupted so the same block folder can be
resumed.

Progress is atomically stored in `experiment_progress.json`, with block metadata
in `<mode>_<phase>/block.json`. Backend shutdown interrupts an active block,
while a browser disconnect alone does not. The final block stops the mapper and
stack but deliberately leaves the participant CSV state as `partial`. Panels E,
F, and G currently expose the block context and lifecycle controls; their
discovery, training, and recording business logic belongs to later lots.

## Panel E: discovery

Discovery opens with the owned stack active and control disabled. The operator
reads the standardised instruction text from the participant's snapshotted
`experiment.yaml`, then activates the mapper and MCAP recorder together. Each
activation writes a new `rosbag_001`, `rosbag_002`, and so on below the block
folder; existing recordings are never overwritten. Deactivation closes the
segment and verifies that `/joy`, `/ee_pose`, and `/joint_states` each contain
messages before discovery can be completed.

`Restart stack` is available from Panels E, F, and G. It restarts only processes
owned by this interface and restores the previous control state. During
discovery, an active recording is closed before restart and restoration creates
a new numbered segment. The packaged instruction is intentionally marked as a
placeholder and must be replaced before participant data collection.

## Panel F: training

Training expands the configured target sequence into globally numbered trials
for every cycle. The mapper remains active throughout the block. Before each
attempt, the interface requires a fresh `/ee_pose` within the configured start
thresholds of `target_out_<n>` and an explicit `Participant ready` action.

Each acquisition is stored as an MCAP `attempt_###` below its trial folder. A
trial succeeds when the end-effector remains within both the linear and angular
thresholds of the destination `target_<n>` for the configured dwell time and
all required topics contain messages. Manual stops, process failures, missing
data, and stack restarts invalidate the attempt; the operator must retry or
continue with a recorded protocol deviation. Informational incidents may be
added without invalidating an attempt.

The packaged 5 mm and 5 degree thresholds are explicitly provisional
development values. Automatic Go-to controls remain visible but disabled until
a dedicated and validated ROS motion interface is available.

## JSON contract

Files use `schema_version: 1` and contain the reference frame, active mode,
source topic, UTC timestamps, and the seven named position/quaternion records.
All seven records must use the same non-empty frame before saving is enabled.
