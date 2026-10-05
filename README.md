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

## Install and build

Use Ubuntu 24.04 with ROS 2 Jazzy. Choose **one** way to install the Python web
dependencies: Ubuntu packages or a virtual environment. Both options still
require ROS and the other system dependencies from `rosdep`.

The build commands below use Bash (`mapfile` and Bash arrays). If your terminal
opens zsh, switch to Bash first and keep using that same shell for the setup,
your chosen option, and the build:

```bash
exec bash
```

From the repository root, with no other ROS workspace or virtual environment
active, prepare a separate build workspace:

```bash
source /opt/ros/jazzy/setup.bash
snake_src="$(git rev-parse --show-toplevel)"
snake_ws="$(dirname "$snake_src")/workspaces/snake_experiment"
git -C "$snake_src" submodule update --init --recursive
mkdir -p "$snake_ws"
cd "$snake_ws"

colcon list --base-paths "$snake_src/dependencies" "$snake_src/src" \
  --names-only | grep -x cartesian_manager

snake_targets=(
  cartesian_manager joystick_mapper qontrol_controller explorer_bringup
  explorer_description explorer_gazebo gripper_pincette snake_experiment_ui
)
mapfile -t snake_packages < <(
  colcon list --base-paths "$snake_src/dependencies" "$snake_src/src" \
    --packages-up-to "${snake_targets[@]}" --paths-only
)
```

The check must print `cartesian_manager`. If it does not, resolve the
submodule issue below before continuing.

### Option 1: Ubuntu packages

Use the system Python and let `rosdep` install all declared dependencies:

```bash
rosdep install --from-paths "${snake_packages[@]}" --ignore-src --rosdistro jazzy
python3 -c 'import fastapi, uvicorn, jinja2, yaml, rclpy; print("Imports OK")'
snake_builder=(colcon)
```

If `uvicorn` or `fastapi` is missing on a PC after setup, install the web
packages there and relaunch the interface; this does not require a rebuild:

```bash
sudo apt update
sudo apt install python3-fastapi python3-uvicorn python3-jinja2 python3-yaml
```

### Option 2: Virtual environment

Keep the web dependencies in a venv. `--system-site-packages` gives it access
to ROS 2 Python modules. Install the web packages with pip and build through
the venv's Python:

```bash
rosdep install --from-paths "${snake_packages[@]}" --ignore-src --rosdistro jazzy \
  --skip-keys "python3-fastapi python3-uvicorn python3-jinja2 python3-yaml"
python3 -m venv --system-site-packages "$snake_ws/.venv"
source "$snake_ws/.venv/bin/activate"
python -m pip install -r "$snake_src/requirements-web.txt"
python -c 'import fastapi, uvicorn, jinja2, yaml, rclpy; print("Imports OK")'
snake_builder=(python -m colcon)
```

If venv creation fails, install `python3-venv` with APT. Use a fresh build
workspace when changing options: installed node scripts retain the Python
interpreter used during their build. In every new launch terminal, activate
`$snake_ws/.venv/bin/activate` before sourcing the workspace.

### Build (both options)

Run this in the same shell after selecting an option:

```bash
"${snake_builder[@]}" build --base-paths "$snake_src/dependencies" "$snake_src/src" \
  --packages-up-to "${snake_targets[@]}" --symlink-install
source "$snake_ws/install/setup.bash"
```

Build output goes into `$snake_ws`, outside the repository. If the
`cartesian_manager` check prints nothing, confirm that
`$snake_src/dependencies/cartesian_manager/package.xml` exists and run
`git -C "$snake_src" submodule status --recursive`. A leading `-` means the
submodule has not been initialized; a failed update may mean access to the
private repository is missing. After moving the repository or
changing dependencies, use a fresh build workspace to avoid stale paths. If
imports work in your shell but launch still reports a missing module, check
the node interpreter in the [interface guide](src/snake_experiment_ui/README.md#missing-python-module).

## Run the interfaces

The [`snake_experiment_ui` package guide](src/snake_experiment_ui/README.md)
explains how to launch each interface, where to open it, how a session works,
and where recorded data is stored. Calibration uses port `8080`; the session
interface uses port `8081`.
