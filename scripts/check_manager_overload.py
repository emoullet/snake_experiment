#!/usr/bin/env python3
"""ROS regression on a manager alone in an isolated domain; no robot is launched.

Run with ROS_DOMAIN_ID set to a free domain. All feedback is synthetic and only
this script's manager process is stopped on exit.
"""
import os
import signal
import subprocess
import time
from pathlib import Path

import rclpy
from ament_index_python.packages import get_package_share_directory
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import PoseStamped, TwistStamped
from rcl_interfaces.srv import SetParametersAtomically
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from std_msgs.msg import Bool
from std_srvs.srv import Trigger


def check(strategy):
    if not os.environ.get('ROS_DOMAIN_ID'):
        raise RuntimeError('Set an unused ROS_DOMAIN_ID before running synthetic manager tests')
    profile = str(Path(get_package_share_directory('snake_experiment_ui')) / 'bringup/cartesian_manager/config/kinova_params.yaml')
    log = open(f'/tmp/manager-synthetic-{strategy}.log', 'w')
    proc = subprocess.Popen(['ros2', 'run', 'cartesian_manager', 'cartesian_manager_node', '--ros-args',
        '--params-file', profile, '-p', 'overload_protection.enabled:=true',
        '-p', 'overload_protection.robot:=kinova', '-p', f'overload_retreat_strategy:={strategy}',
        '-p', 'overload_protection.controller_linear_gain:=0.05', '-p', 'overload_protection.controller_angular_gain:=0.4'],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    rclpy.init()
    n = rclpy.create_node('synthetic_manager_protection_test')
    state = {}
    commands = []
    pose_pub = n.create_publisher(PoseStamped, '/ee_pose', 1)
    bool_pub = n.create_publisher(Bool, '/qontrol_explorer/effort_overload', 1)
    subscriptions = [n.create_subscription(DiagnosticArray, '/cartesian_manager/overload_status',
        lambda m: state.update({'state': m.status[0].message, **{v.key: v.value for v in m.status[0].values}}),
        QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)),
        n.create_subscription(TwistStamped, '/cartesian_command', commands.append, 1)]
    send_pose, send_bool, overloaded = True, True, False
    def wait(predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f'Manager exited: {proc.returncode}; see {log.name}')
            if send_pose:
                p = PoseStamped(); p.header.frame_id = 'base_link'; p.header.stamp = n.get_clock().now().to_msg(); p.pose.orientation.w = 1.
                pose_pub.publish(p)
            if send_bool:
                bool_pub.publish(Bool(data=overloaded))
            rclpy.spin_once(n, timeout_sec=.025)
            if predicate(): return
        raise AssertionError(state)
    def call(name, typ, req):
        c = n.create_client(typ, name)
        assert c.wait_for_service(timeout_sec=5)
        f = c.call_async(req); wait(f.done); result = f.result(); n.destroy_client(c); return result
    def rearm():
        wait(lambda: state.get('rearm_ready') == 'true')
        assert call('/cartesian_manager/rearm_overload', Trigger, Trigger.Request()).success
        wait(lambda: state.get('state') == 'normal')
    try:
        wait(lambda: state.get('state') == 'normal' and state.get('pose_valid') == 'true')
        assert state['event'] == '0', state
        if strategy == 'closest_target_out':
            values = {'overload_protection.reference_frame': 'base_link', 'overload_protection.ee_frame': 'end_effector_link',
                'overload_protection.out_positions': [.1,0.,0., .2,0.,0., -.1,0.,0.],
                'overload_protection.out_orientations': [0.,0.,1.,0.]*3}
            req = SetParametersAtomically.Request(parameters=[Parameter(k,value=v).to_parameter_msg() for k,v in values.items()])
            assert call('/cartesian_manager/set_parameters_atomically', SetParametersAtomically, req).result.successful
        overloaded = True
        wait(lambda: state.get('state') in ('locked','translating'))
        if strategy == 'none':
            assert state['reason'] == 'retreat_disabled' and state['target'] == '', state
            wait(lambda: commands and commands[-1].twist.linear.x == 0.)
        else:
            assert state['target'] == 'target_out_1' and state['flipped'] == 'true', state
            wait(lambda: commands and abs(commands[-1].twist.linear.x-.4) < 1e-6)
            assert call('/cartesian_manager/stop_recovery', Trigger, Trigger.Request()).success
            wait(lambda: state.get('reason') == 'operator_stop')
        overloaded = False
        rearm()
        # Lost pose aborts even without overload. Fresh pose cannot clear the latch.
        send_pose = False
        wait(lambda: state.get('state') == 'error')
        assert state['reason'] == 'invalid_pose', state
        send_pose = True
        wait(lambda: state.get('pose_valid') == 'true')
        assert state['state'] == 'error' and state['reason'] == 'invalid_pose', state
        rearm()
        # Real Bool reception is monitored independently from pose reception.
        send_bool = False
        wait(lambda: state.get('state') == 'error')
        assert state['reason'] == 'invalid_overload_feedback', state
        send_bool = True
        wait(lambda: state.get('overload_valid') == 'true')
        assert state['state'] == 'error' and state['reason'] == 'invalid_overload_feedback', state
        rearm()
        print(f'{strategy}: strategy, normalized output, silent rearm, pose and Bool watchdogs passed', flush=True)
    finally:
        n.destroy_node(); rclpy.shutdown()
        os.killpg(proc.pid, signal.SIGINT)
        try: proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM); proc.wait(timeout=5)
        log.close()


if __name__ == '__main__':
    for name in ('none', 'closest_target_out'):
        check(name)
