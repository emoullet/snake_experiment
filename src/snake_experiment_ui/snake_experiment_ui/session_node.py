"""ROS node hosting the independent Snake experiment Views B-G interface."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
import json
import math
import queue
from pathlib import Path
import threading
import time

from ament_index_python.packages import get_package_share_directory
from controller_manager_msgs.srv import ListControllers
from geometry_msgs.msg import PoseStamped, TwistStamped
import rclpy
from rcl_interfaces.msg import ParameterType
from rcl_interfaces.srv import GetParameters, ListParameters, SetParametersAtomically
from diagnostic_msgs.msg import DiagnosticArray
from std_srvs.srv import Trigger
from rclpy.parameter import Parameter
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import JointState, Joy
from std_msgs.msg import String
import uvicorn

from .checkup import CheckupController
from .diagnostics import DiagnosticMonitor, DiagnosticProfile, collect_git_provenance
from .enrollment import EnrollmentController
from .experiment import ExperimentController, ExperimentProfile
from .go_to import GoToController
from .mode_manager import ModeManager
from .participant_profile import ParticipantProfile
from .rosbag_manager import RosbagManager
from .robot_preview import PreviewConfiguration, RobotPreview, SnakeButtonMonitor
from .session_web_app import create_session_app
from .stack_manager import StackManager
from .storage import default_calibration_path
from .calibration import CalibrationError
from .overload import ProtectionState, ProtectionError, recovery_parameters, EventJournal, protection_action_state, protection_waiting_for_initial_feedback


CONTROLLER_REQUEST_TIMEOUT_SEC = 5.0


class SessionInterfaceNode(Node):
    """Bridge View B state, ROS diagnostics, and launch ownership to the web UI."""

    def __init__(self) -> None:
        super().__init__("snake_experiment_session_interface")
        self._closing = False
        self.declare_parameter("host", "127.0.0.1")
        self.declare_parameter("port", 8081)
        self.declare_parameter("stack_robot", "explorer")
        self.declare_parameter("stack_robot_ip", "192.168.1.10")
        self.declare_parameter("stack_use_simulation", False)
        self.declare_parameter("diagnostic_profile", "")
        self.declare_parameter("experiment_profile", "")
        self.declare_parameter("participant_interface_profile", "")
        self.declare_parameter("presentation_video_path", "")
        self.declare_parameter("measurement_window_sec", 2.0)
        self.declare_parameter("repository_root", "")
        self.declare_parameter("sessions_root", "")
        self.declare_parameter("calibration_file", "")
        self.declare_parameter("mode_startup_timeout_sec", 5.0)
        self.declare_parameter("mode_shutdown_timeout_sec", 5.0)
        self.declare_parameter("stack_startup_timeout_sec", 30.0)
        self.declare_parameter("stack_shutdown_timeout_sec", 10.0)
        self.declare_parameter("overload_retreat_delay_sec", 1.0)
        self.declare_parameter("rosbag_startup_timeout_sec", 5.0)
        self.declare_parameter("rosbag_shutdown_timeout_sec", 10.0)
        self.declare_parameter("go_to_timeout_sec", 120.0)
        self.declare_parameter("go_to_stall_timeout_sec", 5.0)
        self.declare_parameter("go_to_progress_window_sec", 1.0)
        self.declare_parameter("go_to_feedback_timeout_sec", 2.0)

        share = Path(get_package_share_directory("snake_experiment_ui"))
        configured_experiment = str(self.get_parameter("experiment_profile").value)
        experiment_profile_path = (
            Path(configured_experiment)
            if configured_experiment
            else share / "config/experiment.yaml"
        )
        self._experiment_profile = ExperimentProfile(experiment_profile_path)
        go_to_thresholds = self._experiment_profile.data["go_to_thresholds"]
        configured_profile = str(self.get_parameter("diagnostic_profile").value)
        profile_path = (
            Path(configured_profile)
            if configured_profile
            else share / "config" / (
                "system_checkup_kinova.yaml"
                if self.get_parameter("stack_robot").value == "kinova"
                else "system_checkup.yaml"
            )
        )
        self._profile = DiagnosticProfile(profile_path)
        measurement_window = float(
            self.get_parameter("measurement_window_sec").value
        )
        if measurement_window <= 0:
            raise ValueError("measurement_window_sec must be positive")
        self._profile.measurement_window_sec = measurement_window
        configured_repository = str(self.get_parameter("repository_root").value)
        self._repository_root = (
            Path(configured_repository) if configured_repository else Path.cwd()
        ).resolve()
        self._graph_lock = threading.RLock()
        self._graph = {
            "nodes": [],
            "topics": {},
            "controllers": {},
            "mapper_modes": [],
            "mapper_parameter_names": [],
            "mapper_parameters": {},
        }
        self._controller_future = None
        self._controller_future_started_at = None
        self._controller_client_created_at = time.monotonic()
        self._controller_client_generation = 0
        self._controller_lifecycle_events = queue.SimpleQueue()
        self._controller_polling_paused = False
        self._parameter_list_future = None
        self._parameter_get_future = None
        self._mapper_client_generation = 0
        self._mapper_client_reset_requested = threading.Event()

        self._protection = ProtectionState(str(self.get_parameter("stack_robot").value))
        self._protection_events = queue.SimpleQueue()
        self._protection_last_enqueued = None
        self._protection_shutdown = threading.Event()
        self._protection_cleanup_pending = False
        self._protection_configuration = "pending"
        self._protection_restarting = False
        self._mode_publisher = self.create_publisher(String, "/mode_request", 10)
        self._pose_target_publisher = self.create_publisher(
            PoseStamped, "/pose_target", 10
        )
        self._mode_manager = ModeManager(
            robot=str(self.get_parameter("stack_robot").value),
            node_names=self._node_names,
            publish_mode_request=self._publish_mode_request,
            mapper_transition=self._request_mapper_client_reset,
            command_guard=self._require_protection_clear,
            startup_timeout_sec=float(
                self.get_parameter("mode_startup_timeout_sec").value
            ),
            shutdown_timeout_sec=float(
                self.get_parameter("mode_shutdown_timeout_sec").value
            ),
        )
        use_simulation = bool(self.get_parameter("stack_use_simulation").value)
        self._stack_manager = StackManager(
            robot=str(self.get_parameter("stack_robot").value),
            robot_ip=str(self.get_parameter("stack_robot_ip").value),
            node_names=self._node_names,
            use_simulation=use_simulation,
            overload_protection=True,
            overload_retreat_delay_sec=float(self.get_parameter("overload_retreat_delay_sec").value),
            before_start=self._before_stack_start,
            monitoring_changed=self._controller_lifecycle_events.put,
            startup_timeout_sec=float(
                self.get_parameter("stack_startup_timeout_sec").value
            ),
            shutdown_timeout_sec=float(
                self.get_parameter("stack_shutdown_timeout_sec").value
            ),
        )
        self._rosbag_manager = RosbagManager(
            node_names=self._node_names,
            startup_timeout_sec=float(
                self.get_parameter("rosbag_startup_timeout_sec").value
            ),
            shutdown_timeout_sec=float(
                self.get_parameter("rosbag_shutdown_timeout_sec").value
            ),
        )
        self._diagnostics = DiagnosticMonitor(self._profile, self._graph_snapshot)
        preview_robot = str(self.get_parameter("stack_robot").value)
        preview_configuration = PreviewConfiguration(share / "config/robot_preview.yaml", share, robot=preview_robot)
        self._robot_preview = RobotPreview(configuration=preview_configuration, robot=preview_robot)
        self._snake_button_monitor = SnakeButtonMonitor(preview_configuration.snake_button_index)
        self._go_to_finish_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="go-to-finish")
        go_to_options = {
            "publish_target": self._publish_pose_target,
            "publish_passthrough": self._publish_passthrough,
            "preflight": self._go_to_preflight,
            "timeout_sec": float(self.get_parameter("go_to_timeout_sec").value),
            "linear_tolerance_m": float(go_to_thresholds["linear_mm"]) / 1000.0,
            "angular_tolerance_rad": math.radians(float(go_to_thresholds["angular_deg"])),
            "dwell_sec": float(go_to_thresholds["dwell_sec"]),
            "stall_timeout_sec": float(self.get_parameter("go_to_stall_timeout_sec").value),
            "progress_window_sec": float(self.get_parameter("go_to_progress_window_sec").value),
            "linear_progress_threshold_mps": float(go_to_thresholds["linear_progress_mm_per_sec"]) / 1000.0,
            "angular_progress_threshold_radps": math.radians(float(go_to_thresholds["angular_progress_deg_per_sec"])),
            "feedback_timeout_sec": float(self.get_parameter("go_to_feedback_timeout_sec").value),
            "feedback_finish_dispatch": self._dispatch_go_to_finish,
        }
        self._checkup_go_to = GoToController(**go_to_options)
        self._experiment_go_to = GoToController(**go_to_options)

        self._subscriptions = [
            self.create_subscription(DiagnosticArray, "/cartesian_manager/overload_status",
                self._record_protection, QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)),
            self.create_subscription(
                JointState,
                "/joint_states",
                self._record_joint_states,
                50,
            ),
            self.create_subscription(
                PoseStamped,
                "/ee_pose",
                self._record_ee_pose,
                50,
            ),
            self.create_subscription(
                Joy,
                "/joy",
                self._record_joy,
                50,
            ),
            self.create_subscription(
                TwistStamped,
                "/joystick_cartesian_command",
                lambda message: self._diagnostics.record(
                    "/joystick_cartesian_command", message
                ),
                50,
            ),
            self.create_subscription(
                TwistStamped,
                "/cartesian_command",
                lambda message: self._diagnostics.record("/cartesian_command", message),
                50,
            ),
            self.create_subscription(
                String,
                "/mode_request",
                lambda message: self._diagnostics.record("/mode_request", message),
                10,
            ),
            self.create_subscription(
                String,
                "/joystick_mapper/active_mode",
                self._record_mapper_local_mode,
                QoSProfile(
                    depth=1,
                    durability=DurabilityPolicy.TRANSIENT_LOCAL,
                    reliability=ReliabilityPolicy.RELIABLE,
                ),
            ),
        ]
        self._protection_parameters = self.create_client(SetParametersAtomically, "/cartesian_manager/set_parameters_atomically")
        self._protection_rearm = self.create_client(Trigger, "/cartesian_manager/rearm_overload")
        self._protection_stop = self.create_client(Trigger, "/cartesian_manager/stop_recovery")
        self._controller_client = self.create_client(
            ListControllers, "/controller_manager/list_controllers"
        )
        self._parameter_list_client = self.create_client(
            ListParameters, "/joystick_mapper/list_parameters"
        )
        self._parameter_get_client = self.create_client(
            GetParameters, "/joystick_mapper/get_parameters"
        )
        self._go_to_timer = self.create_timer(0.1, self._supervise_go_to)
        self._graph_timer = self.create_timer(0.5, self._refresh_graph)
        self._service_timer = self.create_timer(1.0, self._refresh_services)

        configured_calibration = str(self.get_parameter("calibration_file").value)
        calibration_file = (
            Path(configured_calibration)
            if configured_calibration
            else default_calibration_path(Path.cwd(), preview_robot)
        )
        self._protection_calibration_file = calibration_file
        self._checkup = CheckupController(
            robot=preview_robot,
            stack_manager=self._stack_manager,
            mode_manager=self._mode_manager,
            diagnostics=self._diagnostics,
            provenance_provider=lambda: collect_git_provenance(self._repository_root),
            use_simulation=use_simulation,
            ros_distro=os.environ.get("ROS_DISTRO", "unknown"),
            go_to_controller=self._checkup_go_to,
            calibration_file=calibration_file,
        )
        configured_sessions_root = str(self.get_parameter("sessions_root").value)
        sessions_root = Path(configured_sessions_root) if configured_sessions_root else Path.cwd()
        configured_participant_profile = str(
            self.get_parameter("participant_interface_profile").value
        )
        participant_profile_path = (
            Path(configured_participant_profile)
            if configured_participant_profile
            else share / "config/participant_interface.yaml"
        )
        self._participant_profile = ParticipantProfile(participant_profile_path)
        video_override = str(self.get_parameter("presentation_video_path").value)
        presentation_video = video_override or self._participant_profile.video_path(
            "experiment_presentation"
        )
        mode_explanation_videos = {
            mode: self._participant_profile.video_path("mode_explanation", mode)
            for mode in ("baseline", "snake")
        }
        self._experiment = ExperimentController(
            robot=preview_robot,
            command_allowed=lambda: not self._protection.snapshot()["blocked"],
            stack_command_allowed=self._stack_commands_allowed,
            wait_for_control=self._wait_control_configuration,
            profile=self._experiment_profile,
            stack_manager=self._stack_manager,
            mode_manager=self._mode_manager,
            rosbag_manager=self._rosbag_manager,
            go_to_controller=self._experiment_go_to,
            presentation_video=presentation_video,
            mode_explanation_videos=mode_explanation_videos,
            snake_button_provider=self._snake_button_monitor.snapshot,
        )
        self._enrollment = EnrollmentController(
            sessions_root=sessions_root,
            calibration_file=calibration_file,
            bringup_root=share / "bringup",
            checkup_report_provider=lambda: self._checkup.snapshot()["pending_report"],
            provenance_provider=lambda: collect_git_provenance(self._repository_root),
            stack_manager=self._stack_manager,
            mode_manager=self._mode_manager,
            experiment_profile=experiment_profile_path,
            experiment_prepare=self._prepare_protected_experiment,
            robot_configuration={
                "id": preview_robot,
                "model": preview_configuration.public["robot"],
                "label": preview_configuration.public["robot_label"],
                "use_simulation": bool(self.get_parameter("stack_use_simulation").value),
                "robot_ip": self._stack_manager.snapshot()["robot_ip"],
                "joystick_profiles": {
                    mode: f"experimental_environment/bringup/joystick_mapper/config/joystick_2d_{mode}_{preview_robot}.yaml"
                    for mode in ("baseline", "snake")
                },
            },
        )
        self._protection_thread = threading.Thread(target=self._protection_worker, name="session-protection", daemon=True)
        self._protection_thread.start()
        app = create_session_app(
            self._checkup,
            static_directory=share / "static",
            template_directory=share / "templates",
            enrollment=self._enrollment,
            experiment=self._experiment,
            presentation_video=str(presentation_video or ""),
            mode_explanation_videos=mode_explanation_videos,
            robot_preview=self._robot_preview,
            preview_default_view=self._participant_profile.preview_default_view,
            overload_sound_duration_sec=self._participant_profile.overload_sound_duration_sec,
            protection_snapshot=self._protection_snapshot,
            stack_snapshot=self._stack_manager.snapshot,
            protection_guard=self._require_protection_clear,
            protection_action=self._protection_action,
        )
        self._app = app
        config = uvicorn.Config(
            app,
            host=str(self.get_parameter("host").value),
            port=int(self.get_parameter("port").value),
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        self._server_thread = threading.Thread(
            target=self._server.run,
            name="session-interface-web-server",
            daemon=True,
        )
        self._server_thread.start()
        host = self.get_parameter("host").value
        port = self.get_parameter("port").value
        self.get_logger().info(f"Experiment interface available at http://{host}:{port}")

    def _before_stack_start(self):
        self._protection_restarting = True
        self._protection_configuration = "pending"
        self._protection.invalidate()

    def _wait_control_configuration(self):
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self._protection_configuration != "pending" and self._protection.snapshot()["fresh"]:
                self._require_protection_clear()
                return
            if self._protection_shutdown.wait(0.05):
                break
        raise ProtectionError("La stack n’a pas fourni un état de protection prêt après son redémarrage.")

    def _prepare_protected_experiment(self, participant):
        previous = self._protection_configuration
        self._protection_configuration = "pending"
        try:
            return self._experiment.prepare(participant)
        except Exception:
            self._protection_configuration = previous
            raise

    def _record_protection(self, message):
        # This callback only updates a small status object and enqueues events.
        # No process wait, file I/O or recorder shutdown runs in the ROS executor.
        state = self._protection.record(message, self.get_clock().now().nanoseconds * 1e-9)
        identity = (state.get("instance"), state.get("event"), state["state"], state["reason"])
        if identity != self._protection_last_enqueued:
            self._protection_events.put(state)
            self._protection_last_enqueued = identity

    def _protection_snapshot(self):
        state = self._protection.snapshot()
        state.update(robot=self._protection.robot, configuration=self._protection_configuration,
                     cleanup_pending=self._protection_cleanup_pending)
        return state

    def _require_protection_clear(self):
        if self._closing:
            raise ProtectionError("L’interface est en cours d’arrêt.")
        self._protection.require_clear()
        if self._protection_cleanup_pending or self._protection_configuration == "pending":
            raise ProtectionError("La protection termine la configuration ou l’arrêt de l’acquisition.")

    def _stack_commands_allowed(self):
        try:
            self._require_protection_clear()
            return True
        except ProtectionError:
            return False

    def _wait_ros_result(self, future):
        complete = threading.Event()
        future.add_done_callback(lambda ignored: complete.set())
        if not complete.wait(5.0):
            raise ProtectionError("Le contrôleur ne répond pas à la demande de protection.")
        return future.result()

    def _protection_action(self, command):
        if self._closing:
            raise ProtectionError("L’interface est en cours d’arrêt.")
        if self._stack_manager.snapshot()["status"] not in ("active", "error"):
            raise ProtectionError("Aucune stack appartenant à cette interface n’est active.")
        if command not in ("stop", "rearm"):
            raise ProtectionError("Action de protection inconnue.")
        if command == "rearm":
            state = self._protection_snapshot()
            if not state["fresh"] or not state.get("rearm_ready") or state["cleanup_pending"]:
                raise ProtectionError("Réarmement indisponible : signal de surcharge clair 1 s, joystick neutre 0,5 s ou silencieux 1 s et recul terminé ou arrêté requis.")
            self._publish_passthrough()
        client = self._protection_rearm if command == "rearm" else self._protection_stop
        if not client.service_is_ready():
            raise ProtectionError("Service de protection indisponible.")
        result = self._wait_ros_result(client.call_async(Trigger.Request()))
        if not result.success:
            raise ProtectionError(result.message)
        return self._protection_snapshot()

    def _protection_worker(self):
        seen = None
        configuration_key = None
        journal = EventJournal()
        while not self._protection_shutdown.wait(0.05):
            try:
                state = self._protection_events.get_nowait()
            except queue.Empty:
                state = self._protection.snapshot()
            journal_state = state
            current_state = self._protection.snapshot()
            state = protection_action_state(state, current_state)
            try:
                if self._stack_manager.snapshot()["status"] not in ("active", "error"):
                    continue
                context = self._experiment.protection_context()
                folder = Path(context["folder"]) if context.get("folder") else self._repository_root
                startup_idle = (
                    state["blocked"]
                    and not self._protection_restarting
                    and not self._protection_cleanup_pending
                    and protection_waiting_for_initial_feedback(state)
                    and not self._mode_manager.active_mode()
                    and not self._checkup_go_to.active()
                    and not self._experiment_go_to.active()
                    and self._experiment.protection_startup_is_idle(state)
                )
                # A new event, or loss of fresh status during control, interrupts once.
                identity = (state.get("instance"), state.get("event"), state.get("state") == "unavailable")
                if state["blocked"] and not startup_idle and not self._protection_restarting and (identity != seen or self._protection_cleanup_pending):
                    seen = identity
                    self._protection_cleanup_pending = True
                    self.get_logger().warning(
                        f"Session protection cutoff: state={state['state']}, reason={state['reason']}, "
                        f"event={state.get('event', 0)}, signal_valid={state.get('overload_valid')}."
                    )
                    self._publish_passthrough()
                    self._checkup_go_to.cancel_if_active("effort_overload")
                    self._experiment_go_to.cancel_if_active("effort_overload")
                    # Persist the interrupted trial before waiting on mapper or recorder processes.
                    self._experiment.interrupt_overload(state)
                    self._checkup.interrupt_overload()
                    active = self._mode_manager.active_mode()
                    if active:
                        self._mode_manager.deactivate(active)
                    self._protection_cleanup_pending = False
                journal_state["robot"] = self._protection.robot
                journal.append(folder / "safety/overload_events.jsonl", journal_state, {"block": context.get("block")})
                state = current_state
                self._experiment.clear_initial_feedback_warning(state)
                # Install a complete configuration atomically. Never replace an
                # ongoing retreat's targets, and never leave an old participant's
                # targets installed if the next calibration is invalid.
                if not state.get("fresh") or state.get("state") in ("waiting_retreat", "translating", "waiting_clear", "rotating"):
                    continue
                document = context.get("calibration")
                if document is None:
                    document = self._checkup.protection_calibration()
                if document is None:
                    try:
                        document = json.loads(self._protection_calibration_file.read_text())
                    except (OSError, ValueError):
                        document = None
                key = json.dumps(document, sort_keys=True)
                # Reload after every controller restart even if the file is unchanged.
                key = (key, state.get("instance"))
                if (key != configuration_key or self._protection_configuration == "pending") and self._protection_parameters.service_is_ready():
                    try:
                        parameters = recovery_parameters(document, self._protection.robot)
                        configuration = "ready"
                    except (CalibrationError, TypeError) as error:
                        parameters = {"overload_protection.out_positions": [], "overload_protection.out_orientations": []}
                        configuration = "cutoff_only: " + str(error)
                    request = SetParametersAtomically.Request()
                    request.parameters = [Parameter(name, Parameter.Type.DOUBLE_ARRAY if isinstance(value, list) else Parameter.Type.STRING, value).to_parameter_msg()
                                          for name, value in parameters.items()]
                    response = self._wait_ros_result(self._protection_parameters.call_async(request))
                    if not response.result.successful:
                        raise ProtectionError(response.result.reason)
                    configuration_key = key
                    self._protection_configuration = configuration
                    self._protection_restarting = False
            except Exception as error:
                self.get_logger().error(f"Protection session: {error}")
                # A failed cleanup must continue blocking rearm and mode activation.
                self._protection_shutdown.wait(1.0)

    def _supervise_go_to(self):
        if not self._closing:
            self._checkup_go_to.tick()
            self._experiment_go_to.tick()

    def _dispatch_go_to_finish(self, callback, record):
        # Restoring a mapper waits on process discovery. Never run that wait in
        # the ROS feedback callback that also feeds protection telemetry.
        if not self._closing:
            self._go_to_finish_executor.submit(self._run_go_to_finish, callback, record)

    def _run_go_to_finish(self, callback, record):
        if not self._closing:
            try:
                callback(record)
            except Exception as error:
                self.get_logger().error(f"Go-to finalisation: {error}")

    def _record_ee_pose(self, message: PoseStamped) -> None:
        self._diagnostics.record("/ee_pose", message)
        checkup = getattr(self, "_checkup", None)
        if checkup is not None and not self._protection.snapshot()["blocked"]:
            checkup.try_update_ee_pose(message)
        experiment = getattr(self, "_experiment", None)
        if experiment is not None:
            experiment.try_update_ee_pose(message)

    def _record_joint_states(self, message: JointState) -> None:
        self._diagnostics.record("/joint_states", message)
        self._robot_preview.record(message)

    def _record_joy(self, message: Joy) -> None:
        self._diagnostics.record("/joy", message)
        self._snake_button_monitor.record(message)

    def _node_names(self):
        if self._closing:
            return []
        return [
            f"{namespace.rstrip('/')}/{name}" if namespace != "/" else name
            for name, namespace in self.get_node_names_and_namespaces()
        ]

    def _refresh_graph(self) -> None:
        with self._graph_lock:
            self._graph["nodes"] = self._node_names()
            self._graph["topics"] = dict(self.get_topic_names_and_types())

    def _graph_snapshot(self) -> dict:
        with self._graph_lock:
            return {
                key: value.copy() if isinstance(value, (dict, list)) else value
                for key, value in self._graph.items()
            }

    def _refresh_services(self) -> None:
        if self._mapper_client_reset_requested.is_set():
            self._reset_mapper_parameter_clients()
        self._refresh_controllers()
        active_mode = self._mode_manager.try_active_mode()
        if (
            active_mode is not None
            and self._parameter_list_client.service_is_ready()
            and self._parameter_list_future is None
        ):
            request = ListParameters.Request()
            request.depth = 0
            self._parameter_list_future = self._parameter_list_client.call_async(request)
            generation = self._mapper_client_generation
            self._parameter_list_future.add_done_callback(
                lambda future, generation=generation: self._parameter_names_received(
                    future, generation
                )
            )
        if (
            active_mode is not None
            and self._parameter_get_client.service_is_ready()
            and self._parameter_get_future is None
        ):
            request = GetParameters.Request()
            request.names = sorted(
                self._profile.modes[active_mode].mapper_parameters
            )
            self._parameter_get_future = self._parameter_get_client.call_async(request)
            generation = self._mapper_client_generation
            self._parameter_get_future.add_done_callback(
                lambda future,
                names=request.names,
                generation=generation: self._mapper_parameters_received(
                    future, names, generation
                )
            )

    def _refresh_controllers(self) -> None:
        now = time.monotonic()
        # Only the executor thread touches ROS clients; lifecycle hooks enqueue
        # events which take precedence over timeout handling and new requests.
        while not self._controller_lifecycle_events.empty():
            active = self._controller_lifecycle_events.get_nowait()
            self._controller_polling_paused = not active
            self._reconnect_controller_client(now)
        if self._controller_polling_paused:
            return
        if (
            self._controller_future is not None
            and self._controller_future_started_at is not None
            and now - self._controller_future_started_at >= CONTROLLER_REQUEST_TIMEOUT_SEC
        ):
            self.get_logger().warning(
                "Timed out listing controllers; reconnecting to controller_manager."
            )
            self._reconnect_controller_client(now)

        if not self._controller_client.service_is_ready():
            with self._graph_lock:
                self._graph["controllers"] = {}
                manager_visible = "/controller_manager" in self._graph["nodes"]
            if (
                manager_visible
                and now - self._controller_client_created_at >= CONTROLLER_REQUEST_TIMEOUT_SEC
            ):
                self.get_logger().warning(
                    "Controller manager is visible but its service is unavailable; reconnecting."
                )
                self._reconnect_controller_client(now)
            return
        if self._controller_future is None:
            self._controller_future = self._controller_client.call_async(
                ListControllers.Request()
            )
            self._controller_future_started_at = now
            generation = self._controller_client_generation
            self._controller_future.add_done_callback(
                lambda future, generation=generation: self._controllers_received(
                    future, generation
                )
            )

    def _reconnect_controller_client(self, now: float) -> None:
        if self._controller_future is not None:
            self._controller_client.remove_pending_request(self._controller_future)
        self.destroy_client(self._controller_client)
        self._controller_client = self.create_client(
            ListControllers, "/controller_manager/list_controllers"
        )
        self._controller_client_generation += 1
        self._controller_client_created_at = now
        self._controller_future = None
        self._controller_future_started_at = None
        with self._graph_lock:
            self._graph["controllers"] = {}

    def _request_mapper_client_reset(self) -> None:
        self._snake_button_monitor.reset()
        if hasattr(self, "_experiment"):
            self._experiment.clear_mapper_local_mode()
        with self._graph_lock:
            self._graph["mapper_modes"] = []
            self._graph["mapper_parameter_names"] = []
            self._graph["mapper_parameters"] = {}
        self._mapper_client_reset_requested.set()

    def _record_mapper_local_mode(self, message: String) -> None:
        if hasattr(self, "_experiment"):
            self._experiment.try_record_mapper_local_mode(message.data)

    def _reset_mapper_parameter_clients(self) -> None:
        self._mapper_client_generation += 1
        self._mapper_client_reset_requested.clear()
        self._parameter_list_future = None
        self._parameter_get_future = None
        self.destroy_client(self._parameter_list_client)
        self.destroy_client(self._parameter_get_client)
        self._parameter_list_client = self.create_client(
            ListParameters, "/joystick_mapper/list_parameters"
        )
        self._parameter_get_client = self.create_client(
            GetParameters, "/joystick_mapper/get_parameters"
        )

    def _controllers_received(self, future, generation) -> None:
        if generation != self._controller_client_generation:
            return
        try:
            response = future.result()
            controllers = {item.name: item.state for item in response.controller}
            with self._graph_lock:
                self._graph["controllers"] = controllers
        except Exception as error:  # service failures are exposed as missing checks
            self.get_logger().warning(f"Unable to list controllers: {error}")
            with self._graph_lock:
                self._graph["controllers"] = {}
        finally:
            if generation == self._controller_client_generation:
                self._controller_future = None
                self._controller_future_started_at = None

    def _parameter_names_received(self, future, generation) -> None:
        if generation != self._mapper_client_generation:
            return
        try:
            names = list(future.result().result.names)
            with self._graph_lock:
                self._graph["mapper_parameter_names"] = names
        except Exception as error:
            self.get_logger().warning(f"Unable to list mapper parameters: {error}")
        finally:
            if generation == self._mapper_client_generation:
                self._parameter_list_future = None

    def _mapper_parameters_received(self, future, names, generation) -> None:
        if generation != self._mapper_client_generation:
            return
        try:
            values = future.result().values
            parameters = {
                name: self._parameter_value(value)
                for name, value in zip(names, values)
                if value.type != ParameterType.PARAMETER_NOT_SET
            }
            with self._graph_lock:
                self._graph["mapper_parameters"] = parameters
                self._graph["mapper_modes"] = parameters.get("modes.names", [])
        except Exception as error:
            self.get_logger().warning(f"Unable to read mapper parameters: {error}")
        finally:
            if generation == self._mapper_client_generation:
                self._parameter_get_future = None

    @staticmethod
    def _parameter_value(value):
        fields = {
            ParameterType.PARAMETER_BOOL: "bool_value",
            ParameterType.PARAMETER_INTEGER: "integer_value",
            ParameterType.PARAMETER_DOUBLE: "double_value",
            ParameterType.PARAMETER_STRING: "string_value",
            ParameterType.PARAMETER_BYTE_ARRAY: "byte_array_value",
            ParameterType.PARAMETER_BOOL_ARRAY: "bool_array_value",
            ParameterType.PARAMETER_INTEGER_ARRAY: "integer_array_value",
            ParameterType.PARAMETER_DOUBLE_ARRAY: "double_array_value",
            ParameterType.PARAMETER_STRING_ARRAY: "string_array_value",
        }
        field = fields.get(value.type)
        if field is None:
            return None
        observed = getattr(value, field)
        return list(observed) if value.type >= ParameterType.PARAMETER_BYTE_ARRAY else observed

    def _publish_mode_request(self, request: str) -> None:
        if request != "behaviour/passthrough":
            self._require_protection_clear()
        message = String()
        message.data = request
        self._mode_publisher.publish(message)
        self.get_logger().info(f"Published control mode request: {request}")

    def _publish_pose_target(self, pose: dict) -> None:
        self._require_protection_clear()
        message = PoseStamped()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = pose["frame_id"]
        for key in ("x", "y", "z"):
            setattr(message.pose.position, key, float(pose["position"][key]))
        for key in ("x", "y", "z", "w"):
            setattr(message.pose.orientation, key, float(pose["orientation"][key]))
        self._pose_target_publisher.publish(message)
        self.get_logger().info("Published Cartesian pose target")

    def _publish_passthrough(self) -> None:
        if self._closing or not rclpy.ok():
            return
        message = String()
        message.data = "behaviour/passthrough"
        self._mode_publisher.publish(message)
        self.get_logger().info("Cancelled Cartesian pose behaviour")

    def _go_to_preflight(self):
        graph = self._graph_snapshot()
        nodes = {"/" + str(name).strip("/") for name in graph.get("nodes", [])}
        if "/cartesian_manager" not in nodes:
            return "The /cartesian_manager node is not available."
        topic_types = graph.get("topics", {}).get("/pose_target", [])
        if "geometry_msgs/msg/PoseStamped" not in topic_types:
            return "The /pose_target PoseStamped topic is not available."
        return None

    def destroy_node(self):
        # End web polling and background ROS users before destroying handles.
        self._closing = True
        self._go_to_timer.cancel()
        self._app.state.shutting_down = True
        self._server.should_exit = True
        self._protection_shutdown.set()
        self._protection_thread.join(timeout=6.0)
        if self._server_thread.is_alive():
            try:
                self._server_thread.join(timeout=3.0)
            except KeyboardInterrupt:
                pass
        self._go_to_finish_executor.shutdown(wait=True, cancel_futures=True)
        self._experiment.shutdown()
        self._checkup.shutdown()
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = SessionInterfaceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
