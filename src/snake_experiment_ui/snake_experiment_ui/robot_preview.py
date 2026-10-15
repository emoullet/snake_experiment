"""Read-only robot models and joint poses for the 3D preview."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

import yaml


class RobotPreviewError(RuntimeError):
    """The preview model or an allowed visual asset is unavailable."""


@dataclass(frozen=True)
class RobotDefinition:
    """Display metadata and joint/frame names for a supported robot model."""

    identifier: str
    label: str
    arm_joints: tuple[str, ...]
    gripper_joint: str
    base_frame: str
    ee_frame: str


ROBOT_DEFINITIONS = {
    "explorer": RobotDefinition("explorer_poc2", "Explorer POC2",
        tuple(f"joint_{i}" for i in range(1, 7)), "right_finger_joint", "base_link", "ft_frame"),
    "kinova": RobotDefinition("kinova_gen3", "Kinova Gen3",
        tuple(f"joint_{i}" for i in range(1, 8)), "robotiq_85_left_knuckle_joint",
        "base_link", "end_effector_link"),
}


def robot_definition(robot):
    try:
        return ROBOT_DEFINITIONS[robot]
    except KeyError as error:
        raise RobotPreviewError(f"Unknown preview robot: {robot}") from error


class PreviewConfiguration:
    """Validate installed mapper/manager profiles and expose browser-safe mapping data."""

    COMPONENTS = (
        "linear_x", "linear_y", "linear_z",
        "angular_x", "angular_y", "angular_z",
    )

    def __init__(self, preview_path: Path, package_share: Path, robot="explorer") -> None:
        self.robot = robot
        definition = robot_definition(robot)
        share = Path(package_share)
        preview = self._read_yaml(preview_path)
        if preview.get("schema_version") != 1:
            raise RobotPreviewError("Unsupported robot preview configuration schema.")
        try:
            animation = preview["animation"]
            linear_mm = self._positive(animation["linear_mm"], "linear_mm")
            angular_deg = self._positive(animation["angular_deg"], "angular_deg")
            loop_sec = self._positive(animation["loop_sec"], "loop_sec")
            if not (1 <= linear_mm <= 200 and 1 <= angular_deg <= 45 and 1 <= loop_sec <= 30):
                raise RobotPreviewError("Robot preview animation values are outside safe display ranges.")
            manager_path = share / "bringup/cartesian_manager/config" / f"{robot}_params.yaml"
            manager_parameters = self._read_yaml(manager_path)["cartesian_manager"]["ros__parameters"]
            frames = dict(manager_parameters["frames"])
            if robot == "kinova":
                targets = manager_parameters["behaviours"]["joint_targets"]
                names = targets["joint_names"]
                home_index = targets["target_names"].index("home")
                positions = targets["positions"]
                if tuple(names) != definition.arm_joints or len(positions) != len(names) * len(targets["target_names"]):
                    raise RobotPreviewError("Kinova home joint target is incomplete.")
                demo = dict(zip(names, positions[home_index * len(names):(home_index + 1) * len(names)]))
                demo[definition.gripper_joint] = 0.0
            else:
                demo = preview["demo_pose"]
            demo_joints = {
                name: self._finite(demo[name], name)
                for name in definition.arm_joints + (definition.gripper_joint,)
            }
            signs = preview["physical_axis_signs"]
            physical_signs = {
                "right": self._sign(signs["right"]),
                "up": self._sign(signs["up"]),
            }
            allowed_frames = {frames[name] for name in ("base_frame", "ee_frame")}
            if any(type(frame) is not str or not frame.strip() for frame in allowed_frames):
                raise RobotPreviewError("Cartesian manager command frames are invalid.")
            mapper = {}
            for mode, expected in (("baseline", ("b1", "b2", "b3")), ("snake", ("b1", "b2"))):
                mapper_path = share / "bringup/joystick_mapper/config" / f"joystick_2d_{mode}_{robot}.yaml"
                parameters = self._read_yaml(mapper_path)["joystick_mapper"]["ros__parameters"]
                modes = parameters["modes"]
                if tuple(modes["names"]) != expected:
                    raise RobotPreviewError(f"Unexpected {mode} mapper sub-modes.")
                if mode == "snake":
                    button_index = parameters["snake_button_index"]
                    if (
                        type(button_index) is not int
                        or button_index < 0
                        or parameters["snake_button_mode"] != "hold"
                    ):
                        raise RobotPreviewError("Snake button must use a valid hold binding.")
                    self.snake_button_index = button_index
                mapper[mode] = {}
                for name in expected:
                    configured = modes[name]
                    frame = configured["angular_output_frame_id"]
                    if frame not in allowed_frames:
                        raise RobotPreviewError(f"Unsupported {mode}/{name} angular frame.")
                    axes = {}
                    for component in self.COMPONENTS:
                        spec = configured["axes"][component]
                        index = spec["index"]
                        if type(index) is not int or index not in (-1, 0, 1):
                            raise RobotPreviewError(f"Unsupported {mode}/{name}/{component} axis index.")
                        axes[component] = {
                            "index": index,
                            "scale": self._finite(spec["scale"], component),
                        }
                    if any(
                        not any(
                            spec["index"] == axis and spec["scale"] != 0
                            for spec in axes.values()
                        )
                        for axis in (0, 1)
                    ):
                        raise RobotPreviewError(f"{mode}/{name} must map both joystick axes.")
                    mapper[mode][name] = {"angular_frame": frame, "axes": axes}
            gain = self._positive(
                manager_parameters["shapers"]["snake"]["gain"],
                "snake gain",
            )
        except RobotPreviewError:
            raise
        except (KeyError, TypeError, ValueError, IndexError) as error:
            raise RobotPreviewError("Robot preview configuration is incomplete or invalid.") from error
        self.public = {
            "schema_version": 1,
            "robot": definition.identifier,
            "robot_label": definition.label,
            "arm_joints": list(definition.arm_joints),
            "gripper_joint": definition.gripper_joint,
            "frames": {"base": frames["base_frame"], "ee": frames["ee_frame"]},
            "animation": {
                "linear_mm": linear_mm,
                "angular_deg": angular_deg,
                "loop_sec": loop_sec,
            },
            "demo_pose": demo_joints,
            "physical_axis_signs": physical_signs,
            "snake_gain": gain,
            "mapper": mapper,
        }

    @staticmethod
    def _read_yaml(path: Path) -> dict:
        try:
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise RobotPreviewError("Robot preview configuration file is unavailable or invalid.") from error
        if not isinstance(raw, dict):
            raise RobotPreviewError("Robot preview configuration must be a mapping.")
        return raw

    @staticmethod
    def _finite(value, name: str) -> float:
        if isinstance(value, bool):
            raise RobotPreviewError(f"Robot preview {name} must be a finite number.")
        result = float(value)
        if not math.isfinite(result):
            raise RobotPreviewError(f"Robot preview {name} must be a finite number.")
        return result

    @classmethod
    def _positive(cls, value, name: str) -> float:
        result = cls._finite(value, name)
        if result <= 0:
            raise RobotPreviewError(f"Robot preview {name} must be positive.")
        return result

    @staticmethod
    def _sign(value):
        if value is None:
            return None
        if type(value) is not int or value not in (-1, 1):
            raise RobotPreviewError("Physical joystick axis signs must be -1, 1, or null.")
        return value


class ExplorerModelProvider:
    """Build a browser-safe visual URDF from the installed Apache-2.0 packages."""

    PACKAGES = ("explorer_description", "gripper_pincette")
    XACRO_PACKAGE = "explorer_description"
    ANCHORS = {
        "explorer_description": Path("urdf/explorer.urdf.xacro"),
        "gripper_pincette": Path("description/urdf/gripper_pincette.urdf.xacro"),
    }
    MAPPINGS = {"use_POC2": "true", "simulation": "false"}
    LABEL = "Explorer POC2"
    VISUAL_PREFIXES = None

    def __init__(self, package_share=None, xacro_processor=None) -> None:
        if package_share is None:
            from ament_index_python.packages import get_package_share_directory

            package_share = get_package_share_directory
        if xacro_processor is None:
            import xacro

            xacro_processor = xacro.process_file
        self._package_share = package_share
        self._xacro_processor = xacro_processor
        self._lock = threading.RLock()
        self._urdf: str | None = None
        self._assets: dict[str, Path] = {}

    def urdf(self) -> str:
        with self._lock:
            if self._urdf is None:
                self._build()
            return self._urdf

    def asset(self, asset_id: str) -> Path:
        with self._lock:
            if self._urdf is None:
                self._build()
            try:
                return self._assets[asset_id]
            except KeyError as error:
                raise RobotPreviewError("Unknown robot visual asset.") from error

    def _build(self) -> None:
        try:
            shares = {
                package: Path(self._package_share(package)).absolute()
                for package in self.PACKAGES
            }
            xacro_path = shares[self.XACRO_PACKAGE] / self.ANCHORS[self.XACRO_PACKAGE]
            anchors = self.ANCHORS
            sources = {}
            for package, anchor in anchors.items():
                anchor_path = shares[package] / anchor
                if not anchor_path.is_file():
                    raise RobotPreviewError("Installed robot description is incomplete.")
                sources[package] = anchor_path.resolve().parents[len(anchor.parts) - 1]
            document = self._xacro_processor(
                str(xacro_path), mappings=dict(self.MAPPINGS)
            )
            root = ElementTree.fromstring(document.toxml())
            assets: dict[str, Path] = {}
            for child in list(root):
                if child.tag not in ("link", "joint", "material"):
                    root.remove(child)
            for link in root.findall("link"):
                for child in list(link):
                    if child.tag not in ("visual",):
                        link.remove(child)
                for mesh in link.findall(".//visual/geometry/mesh"):
                    package, path, relative = self._visual_path(
                        mesh.attrib.get("filename", ""), shares, sources
                    )
                    asset_id = hashlib.sha256(
                        f"{package}/{relative.as_posix()}".encode("utf-8")
                    ).hexdigest()[:20] + path.suffix.lower()
                    assets[asset_id] = path.resolve()
                    mesh.set("filename", f"/participant/3d-preview/assets/{asset_id}")
            for material in root.findall(".//material"):
                for child in list(material):
                    if child.tag == "texture":
                        material.remove(child)
            self._assets = assets
            self._urdf = ElementTree.tostring(root, encoding="unicode")
        except RobotPreviewError:
            raise
        except Exception as error:
            raise RobotPreviewError(f"{self.LABEL} model is unavailable.") from error

    @classmethod
    def _visual_path(
        cls, uri: str, shares: dict[str, Path], sources: dict[str, Path]
    ) -> tuple[str, Path, Path]:
        parsed = urlparse(uri)
        if parsed.scheme == "file" and not parsed.netloc:
            path = Path(os.path.abspath(unquote(parsed.path)))
        elif parsed.scheme == "package":
            package = parsed.netloc
            if package not in shares:
                raise RobotPreviewError("Robot visual asset package is not allowed.")
            path = Path(os.path.abspath(shares[package] / unquote(parsed.path.lstrip("/"))))
        else:
            raise RobotPreviewError("Robot visual asset URL is not supported.")
        for package, share in shares.items():
            try:
                relative = path.relative_to(share)
            except ValueError:
                continue
            if (
                not path.is_file()
                or path.suffix.lower() not in (".dae", ".stl")
                or (cls.VISUAL_PREFIXES is None and "visual" not in relative.parts)
                or (cls.VISUAL_PREFIXES is not None
                    and not relative.is_relative_to(cls.VISUAL_PREFIXES[package]))
                or path.resolve() != sources[package] / relative
            ):
                break
            return package, path, relative
        raise RobotPreviewError("Robot visual asset is outside allowed package files.")


class KinovaModelProvider(ExplorerModelProvider):
    """Expand Gen3/Robotiq visual geometry without connecting to hardware."""

    PACKAGES = ("kortex_description", "robotiq_description")
    XACRO_PACKAGE = "kortex_description"
    ANCHORS = {
        "kortex_description": Path("robots/gen3.xacro"),
        "robotiq_description": Path("urdf/robotiq_2f_85_macro.urdf.xacro"),
    }
    MAPPINGS = {
        "arm": "gen3", "dof": "7", "gripper": "robotiq_2f_85",
        "use_fake_hardware": "true", "use_internal_bus_gripper_comm": "true",
        "gripper_joint_name": "robotiq_85_left_knuckle_joint",
        "use_external_cable": "true",
    }
    LABEL = "Kinova Gen3"
    VISUAL_PREFIXES = {
        "kortex_description": Path("arms/gen3/7dof/meshes"),
        "robotiq_description": Path("meshes/visual/2f_85"),
    }


class RobotPoseMonitor:
    """Publish only the joints needed by the selected robot preview."""

    ARM_JOINTS = tuple(f"joint_{index}" for index in range(1, 7))
    GRIPPER_JOINT = "right_finger_joint"

    def __init__(self, clock=time.monotonic, max_age_sec: float = 1.0, robot="explorer") -> None:
        self.definition = robot_definition(robot)
        self.ARM_JOINTS = self.definition.arm_joints
        self.GRIPPER_JOINT = self.definition.gripper_joint
        if max_age_sec <= 0:
            raise ValueError("max_age_sec must be positive")
        self._clock = clock
        self._max_age_sec = max_age_sec
        self._lock = threading.RLock()
        self._positions: dict[str, float] | None = None
        self._received_at: float | None = None

    def record(self, message) -> None:
        names = list(message.name)
        values = list(message.position)
        positions = None
        if len(names) == len(values) and len(names) == len(set(names)):
            try:
                observed = {name: float(value) for name, value in zip(names, values)}
                if all(math.isfinite(value) for value in observed.values()) and all(
                    name in observed for name in self.ARM_JOINTS
                ):
                    relevant = self.ARM_JOINTS + (self.GRIPPER_JOINT,)
                    positions = {
                        name: observed[name] for name in relevant if name in observed
                    }
            except (TypeError, ValueError):
                pass
        with self._lock:
            self._positions = positions
            self._received_at = self._clock() if positions is not None else None

    def snapshot(self) -> dict:
        with self._lock:
            if self._positions is None or self._received_at is None:
                status = "unavailable"
            elif self._clock() - self._received_at > self._max_age_sec:
                status = "stale"
            else:
                status = "live"
            return {
                "robot": self.definition.identifier,
                "status": status,
                "joints": dict(self._positions) if status == "live" else None,
                "gripper_available": bool(
                    status == "live" and self.GRIPPER_JOINT in self._positions
                ),
            }


class SnakeButtonMonitor:
    """Keep only a fresh boolean for the mapped Snake hold button."""

    def __init__(self, button_index: int, clock=time.monotonic, max_age_sec: float = 0.5) -> None:
        if type(button_index) is not int or button_index < 0 or max_age_sec <= 0:
            raise ValueError("Invalid Snake button monitoring configuration.")
        self._button_index = button_index
        self._clock = clock
        self._max_age_sec = max_age_sec
        self._lock = threading.RLock()
        self._held: bool | None = None
        self._received_at: float | None = None

    def record(self, message) -> None:
        try:
            value = message.buttons[self._button_index]
            held = bool(value) if value in (0, 1) else None
        except (AttributeError, IndexError, TypeError):
            held = None
        with self._lock:
            self._held = held
            self._received_at = self._clock() if held is not None else None

    def reset(self) -> None:
        with self._lock:
            self._held = None
            self._received_at = None

    def snapshot(self) -> bool | None:
        with self._lock:
            if self._received_at is None or self._clock() - self._received_at > self._max_age_sec:
                return None
            return self._held


class RobotPreview:
    """Read-only model and pose for the selected experiment robot."""

    def __init__(self, model=None, pose=None, configuration=None, robot="explorer") -> None:
        robot_definition(robot)
        provider = KinovaModelProvider if robot == "kinova" else ExplorerModelProvider
        self.model = model if model is not None else provider()
        self.pose = pose if pose is not None else RobotPoseMonitor(robot=robot)
        self._configuration = configuration

    def record(self, message) -> None:
        self.pose.record(message)

    def snapshot(self) -> dict:
        return self.pose.snapshot()

    def configuration(self) -> dict:
        if self._configuration is None:
            raise RobotPreviewError("Robot preview configuration is unavailable.")
        return self._configuration.public
