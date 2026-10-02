# Snake experiment

This repository contains the ROS 2 software for preparing the Snake experiment:
robot dependencies, a calibration interface, and a session interface. The
software workflows are under development; robot operation and scientific
validation of the protocol remain to be confirmed.

| Location | Contents |
| --- | --- |
| `dependencies/` | Git dependencies pinned to specific commits. |
| `src/snake_experiment_ui/` | Web interfaces, launch files, and experiment configuration. |

## Get the repository

```bash
git clone --recurse-submodules https://github.com/emoullet/snake_experiment.git
cd snake_experiment
git submodule update --init --recursive
```

Some dependencies are private and require access permissions. Run
`git submodule status --recursive` to check that they are present. A line
starting with `-` indicates an uninitialized submodule.

## Build in a separate workspace

Expected environment: Ubuntu 24.04 with ROS 2 Jazzy. From the repository root,
in a shell where no other ROS workspace has been sourced:

```bash
source /opt/ros/jazzy/setup.bash
snake_src="$(git rev-parse --show-toplevel)"
snake_ws="$(dirname "$snake_src")/workspaces/snake_experiment"
mkdir -p "$snake_ws"
cd "$snake_ws"

snake_targets=(
  cartesian_manager joystick_mapper qontrol_controller explorer_bringup
  explorer_description explorer_gazebo gripper_pincette snake_experiment_ui
)
mapfile -t snake_packages < <(
  colcon list --base-paths "$snake_src/dependencies" "$snake_src/src" \
    --packages-up-to "${snake_targets[@]}" --paths-only
)
rosdep install --from-paths "${snake_packages[@]}" --ignore-src --rosdistro jazzy
colcon build --base-paths "$snake_src/dependencies" "$snake_src/src" \
  --packages-up-to "${snake_targets[@]}" --symlink-install
source "$snake_ws/install/setup.bash"
```

Build output goes into `$snake_ws`, outside the repository. If package
discovery finds nothing, check the submodules before running `rosdep`. After
moving the repository or making a significant dependency change, create a
fresh build workspace to avoid stale cached paths.

## Run the interfaces

The [`snake_experiment_ui` package guide](src/snake_experiment_ui/README.md)
explains how to launch each interface, where to open it, how a session works,
and where recorded data is stored. Calibration uses port `8080`; the session
interface uses port `8081`.
