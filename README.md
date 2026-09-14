# Snake experiment

Independent preparation repository for the Snake evaluation, hosted privately
under [`emoullet/exp_snake`](https://github.com/emoullet/exp_snake).
It is referenced by `emoullet/robot_experiments` as the `snake` submodule.

**Status: decisions documented, dependencies pinned and bringup draft preserved.
The progressive restart implements Python planning, storage, measurement,
deterministic synthetic rosbag recording, a local scenario interface and complete
synthetic session reports. An optional subscriptions-only passive acquisition
adapter now exists but has no approved real topic/QoS contract; real M/S control,
participant-facing operation and scientific analysis remain future work.**
Initial dependency references do not constitute a validated experiment version.

## Contents

| Path | Purpose |
| --- | --- |
| `protocol/session.md` | Agreed session flow and rules, consolidated after discussion |
| `protocol/parameters.json` | Machine-readable agreed parameters and synthetic configuration |
| `protocol/sources/` | English translation of the Drive draft and source provenance |
| `bringup/` | Original launch and configuration files, preserved without changes |
| `dependencies/` | Software repositories at exact commits, with caveats in their README |
| `src/` | Experiment-owned ROS 2 packages, including the Panel A calibration interface |
| `analysis/` | Location for future analysis calculations and views |
| `docs/` | Target architecture, data, work status and open questions |

```bash
git clone --recurse-submodules https://github.com/emoullet/exp_snake.git
```

Private dependencies require access permissions. Nested submodules may be hosted
by their original authors. Retrieving them requires no writes to those third-party
repositories or to ISIR-EXTENDER.

## Verify the dependencies

From this repository's root, run:

```bash
git submodule status --recursive
git status --short
git submodule foreach --recursive 'git status --short'
```

Each submodule status line should start with a space. `-` means uninitialized,
`+` means a different commit, and `U` means a conflict. Short status output reveals
local file changes; `foreach` repository headings are normal. Detached HEAD is
normal for pinned dependencies. After preserving local changes, restore the
recorded versions with:

```bash
git submodule update --init --recursive
```

See [how to select and publish a dependency revision](dependencies/README.md#select-a-dependency-revision).

## Build in an isolated workspace

The preparation machine uses Ubuntu 24.04 and ROS 2 Jazzy. Package discovery and
`rosdep check` and the full 15-package isolated build succeeded after the authorized
cartesian_manager documentation-installation fix. All seven target package origins
were verified inside that install. See [build evidence](docs/build_status.md).
Robot execution and experimental behavior remain unvalidated. The corrected
dependency commit is local only; reproducing it on another machine requires its
later explicit publication or transfer.

Use Bash in an environment where no other Extender workspace has been sourced.
If your shell startup files automatically source one, start a clean environment
before proceeding. Run the following from the **Snake repository root** (the
`snake_experiment/` submodule, or the root of an independent `exp_snake` clone). Source only
the system ROS installation before building:

```bash
source /opt/ros/jazzy/setup.bash

snake_src="$(git rev-parse --show-toplevel)"
snake_revision="$(git -C "$snake_src" rev-parse --short HEAD)"
snake_build_root="${SNAKE_BUILD_ROOT:-$(dirname "$snake_src")/workspaces}"
snake_ws="$snake_build_root/snake_experiment_${snake_revision}"

mkdir -p "$snake_ws"
cd "$snake_ws"

snake_targets=(
  cartesian_manager
  joystick_mapper
  qontrol_controller
  explorer_bringup
  explorer_description
  explorer_gazebo
  gripper_pincette
  snake_experiment_ui
)

colcon list \
  --base-paths "$snake_src/dependencies" "$snake_src/src" \
  --packages-up-to "${snake_targets[@]}"
```

The source path is derived from Git and does not depend on the clone's location
or the owner's username. Build outputs default to a `workspaces` directory next to
the source checkout. `SNAKE_BUILD_ROOT` can select another absolute location
outside the source checkout.
Keep the experiment repositories outside the package-development workspace.
Keep both `COLCON_IGNORE` markers as an additional safeguard. Explicit
`--base-paths` starts discovery inside `dependencies`, while the markers prevent
accidental discovery from an enclosing workspace. `--packages-up-to` selects the named packages and
their declared recursive dependencies. Description, simulation and gripper packages
are named explicitly because launch-time resources are not all pulled in by the
current package manifests.

Check the system dependencies for the selected packages:

```bash
mapfile -t snake_packages < <(
  colcon list \
    --base-paths "$snake_src/dependencies" "$snake_src/src" \
    --packages-up-to "${snake_targets[@]}" \
    --paths-only
)

rosdep check --from-paths "${snake_packages[@]}" --ignore-src --rosdistro jazzy
```

Do not proceed if discovery failed or returned no packages. If rosdep reports
missing dependencies, review them and install them with:

```bash
rosdep install --from-paths "${snake_packages[@]}" --ignore-src --rosdistro jazzy
```

This can request administrative privileges. A successful rosdep check only covers
declared system dependencies; it does not prove API compatibility or cover every
dependency fetched by CMake. In particular, the pinned Qontrol requires Pinocchio
3.4.0 or a compatible version accepted by CMake and fetches qpmad/plog during configuration.

Build from the dedicated workspace directory:

```bash
colcon build \
  --base-paths "$snake_src/dependencies" "$snake_src/src" \
  --packages-up-to "${snake_targets[@]}" \
  --symlink-install \
  --cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo
```

Sources come from Snake's submodules. `build/`, `install/` and `log/` are written
under `$snake_ws`, separately from the development workspace. Stop and investigate
any build failure before sourcing the result; use the logs under `$snake_ws/log`.
After a successful build:

```bash
source "$snake_ws/install/setup.bash"

ros2 pkg prefix cartesian_manager
ros2 pkg prefix joystick_mapper
ros2 pkg prefix qontrol_controller
```

All three paths should resolve inside `$snake_ws/install`, not an older workspace.
Use a new terminal with only system ROS sourced for later builds, rather than
building in a terminal that has already sourced this install.

For a clean rebuild after a revision, toolchain or significant configuration
change, choose a new `$snake_ws` directory. The revision suffix only identifies the
Snake commit: uncommitted submodule changes do not change it. Commit dependency
references first or use an explicitly distinct scratch directory for development.
`--symlink-install` is convenient for development, but changing linked sources can
change installed behavior; it is not an immutable acquisition environment.

After moving the source checkout, use a fresh build directory: CMake caches and
symlink installs can still refer to the previous source path. Moving the repository
does not relocate or repair external build outputs automatically.

The original launch files still target installed package configurations. They
require integration with Snake's own configuration files before they can be treated
as a validated experiment entry point. This procedure builds the available robot
stack; it does not validate the synthetic interface against that stack.
