from types import SimpleNamespace

from snake_experiment_ui.robot_preview import JoystickInputMonitor


def test_input_tracks_neutral_buttons_deadzone_and_silence():
    now = [0.0]
    monitor = JoystickInputMonitor(clock=lambda: now[0])
    joy = lambda axes, buttons: SimpleNamespace(axes=axes, buttons=buttons)
    assert not monitor.snapshot()
    monitor.record(joy([0.1, -0.2, 1.0], [0]))
    assert not monitor.snapshot()
    monitor.record(joy([0.0, 0.3], [0]))
    assert monitor.snapshot()
    monitor.record(joy([0.0, 0.0], [0]))
    assert not monitor.snapshot()
    monitor.record(joy([0.0, 0.0], [1]))
    assert monitor.snapshot()
    now[0] = 1.1
    assert not monitor.snapshot()
