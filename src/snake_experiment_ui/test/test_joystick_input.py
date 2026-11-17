from types import SimpleNamespace

from snake_experiment_ui.robot_preview import CartesianCommandMonitor


def test_cartesian_command_activity_neutral_and_expiry():
    now = [0.0]
    monitor = CartesianCommandMonitor(clock=lambda: now[0])
    def command(linear=0.0, angular=0.0):
        return SimpleNamespace(twist=SimpleNamespace(
            linear=SimpleNamespace(x=linear, y=0.0, z=0.0),
            angular=SimpleNamespace(x=0.0, y=angular, z=0.0),
        ))
    monitor.record_command(command(0.1))
    assert monitor.snapshot()
    monitor.record_command(command())
    assert not monitor.snapshot()
    monitor.record_command(command(angular=0.2))
    assert monitor.snapshot()
    now[0] = 1.1
    assert not monitor.snapshot()
