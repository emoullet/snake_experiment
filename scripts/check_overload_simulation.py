#!/usr/bin/env python3
"""Opt-in integration test against an already running protected fake Kinova.

Run in an isolated ROS domain, with no physical hardware. This script changes
retreat targets and sends test arm/gripper commands after verifying fake mode.
Restart the stack afterwards to reload the session calibration.
"""
import math, time, json
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from diagnostic_msgs.msg import DiagnosticArray
from geometry_msgs.msg import PoseStamped, TwistStamped
from sensor_msgs.msg import Joy, JointState
from std_msgs.msg import Bool, Float64MultiArray
from std_srvs.srv import Trigger
from rcl_interfaces.srv import SetParametersAtomically
def main():
    rclpy.init()
    n = None
    try:
        n=Node('overload_integration_test')
        latest={}
        validated_fake=False
        def store(k,m): latest[k]=m
        subscriptions=[n.create_subscription(DiagnosticArray,'/qontrol_explorer/overload_status',lambda m:store('status',m),QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL,reliability=ReliabilityPolicy.RELIABLE)),n.create_subscription(PoseStamped,'/ee_pose',lambda m:store('pose',m),1),n.create_subscription(JointState,'/joint_states',lambda m:store('joints',m),1)]
        joy=n.create_publisher(Joy,'/joy',1)
        overload=n.create_publisher(Bool,'/qontrol_explorer/effort_overload',1)
        cart=n.create_publisher(TwistStamped,'/cartesian_command',1)
        grip=n.create_publisher(Float64MultiArray,'/gripper_controller/commands',1)
        def values():
         s=latest['status'].status[0]
         return {'state':s.message,**{v.key:v.value for v in s.values}}
        def wait(predicate,timeout=8, actions=None):
         deadline=time.monotonic()+timeout
         while time.monotonic()<deadline:
          if validated_fake: joy.publish(Joy(axes=[0.,0.],buttons=[0]*12))
          if actions: actions()
          rclpy.spin_once(n,timeout_sec=.025)
          if predicate(): return
         raise RuntimeError('Timeout: '+json.dumps(values() if 'status' in latest else {}))
        def service(client,req):
         assert client.wait_for_service(timeout_sec=5)
         f=client.call_async(req)
         wait(lambda:f.done(),timeout=8)
         return f.result()
        wait(lambda:all(k in latest for k in ('pose','status','joints')))
        assert values()['state']=='normal'
        if not (values()['force_source']=='fake_hardware_no_contact' and latest['status'].status[0].hardware_id == 'kinova' and values()['enabled']=='true'):
         raise RuntimeError('Refusing test outside protected fake Kinova hardware')
        validated_fake=True
        p=latest['pose'].pose
        # Near-zero displacement remains reachable even at the straight initial pose.
        pos=[p.position.x+.006,p.position.y,p.position.z-.003]
        quat=[p.orientation.x,p.orientation.y,p.orientation.z,p.orientation.w]
        parameters={'overload_protection.reference_frame':'base_link','overload_protection.ee_frame':'end_effector_link','overload_protection.out_positions':pos+[pos[0]+.1,pos[1],pos[2]]+[pos[0]-.1,pos[1],pos[2]],'overload_protection.out_orientations':quat*3}
        client=n.create_client(SetParametersAtomically,'/qontrol_explorer/set_parameters_atomically')
        r=service(client,SetParametersAtomically.Request(parameters=[Parameter(k,value=v).to_parameter_msg() for k,v in parameters.items()]))
        assert r.result.successful, r.result.reason
        print('Atomic target configuration accepted',flush=True)
        grip.publish(Float64MultiArray(data=[.3]))
        wait(lambda: abs(latest['joints'].position[list(latest['joints'].name).index('robotiq_85_left_knuckle_joint')]-.3)<.01)
        def inject(): overload.publish(Bool(data=True))
        wait(lambda: values()['blocked']=='true',actions=inject)
        print('Overload:',values()['state'],values()['target'],flush=True)
        event=values()['event']
        held=latest['joints'].position[list(latest['joints'].name).index('robotiq_85_left_knuckle_joint')]
        def forbidden():
         msg=TwistStamped();msg.header.stamp=n.get_clock().now().to_msg();msg.twist.linear.y=1.
         cart.publish(msg);grip.publish(Float64MultiArray(data=[.8]))
        wait(lambda: values()['state'] in ('locked','error'),timeout=31,actions=forbidden)
        print('Recovery result:',values()['state'],values()['reason'],flush=True)
        assert values()['state']=='locked',values()
        assert values()['event']==event
        assert abs(latest['joints'].position[list(latest['joints'].name).index('robotiq_85_left_knuckle_joint')]-held)<.01
        wait(lambda: values()['rearm_ready']=='true',timeout=5)
        rearm=n.create_client(Trigger,'/qontrol_explorer/rearm_overload')
        r=service(rearm,Trigger.Request());assert r.success,r.message
        wait(lambda: values()['state']=='normal')
        a=list(latest['joints'].position)
        end=time.monotonic()+1.
        while time.monotonic()<end:
         joy.publish(Joy(axes=[0.,0.],buttons=[0]*12));rclpy.spin_once(n,timeout_sec=.025)
        b=latest['joints'].position
        assert max(abs(x-y) for x,y in zip(a,b))<.002, (a,b)
        print('Rearm accepted; no cached arm/gripper command replay; joint state stable',flush=True)
    finally:
        if n is not None:
            n.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
