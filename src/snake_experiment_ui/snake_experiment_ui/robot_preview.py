"""Read-only Explorer POC2 model and joint pose for the 3D preview."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree


class RobotPreviewError(RuntimeError):
    """The preview model or an allowed visual asset is unavailable."""


class ExplorerModelProvider:
    """Build a browser-safe visual URDF from the installed Apache-2.0 packages."""

    PACKAGES = ("explorer_description", "gripper_pincette")

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
            xacro_path = shares["explorer_description"] / "urdf/explorer.urdf.xacro"
            anchors = {
                "explorer_description": Path("urdf/explorer.urdf.xacro"),
                "gripper_pincette": Path("description/urdf/gripper_pincette.urdf.xacro"),
            }
            sources = {}
            for package, anchor in anchors.items():
                anchor_path = shares[package] / anchor
                if not anchor_path.is_file():
                    raise RobotPreviewError("Installed robot description is incomplete.")
                sources[package] = anchor_path.resolve().parents[len(anchor.parts) - 1]
            document = self._xacro_processor(
                str(xacro_path), mappings={"use_POC2": "true", "simulation": "false"}
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
            raise RobotPreviewError("Explorer POC2 model is unavailable.") from error

    @staticmethod
    def _visual_path(
        uri: str, shares: dict[str, Path], sources: dict[str, Path]
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
                or "visual" not in relative.parts
                or path.resolve() != sources[package] / relative
            ):
                break
            return package, path, relative
        raise RobotPreviewError("Robot visual asset is outside allowed package files.")


class RobotPoseMonitor:
    """Publish only the joints needed by the Explorer preview."""

    ARM_JOINTS = tuple(f"joint_{index}" for index in range(1, 7))
    GRIPPER_JOINT = "right_finger_joint"

    def __init__(self, clock=time.monotonic, max_age_sec: float = 1.0) -> None:
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
                "robot": "explorer_poc2",
                "status": status,
                "joints": dict(self._positions) if status == "live" else None,
                "gripper_available": bool(
                    status == "live" and self.GRIPPER_JOINT in self._positions
                ),
            }


class RobotPreview:
    """Model-provider boundary; Kinova can supply another provider later."""

    def __init__(self, model=None, pose=None) -> None:
        self.model = model if model is not None else ExplorerModelProvider()
        self.pose = pose if pose is not None else RobotPoseMonitor()

    def record(self, message) -> None:
        self.pose.record(message)

    def snapshot(self) -> dict:
        return self.pose.snapshot()
