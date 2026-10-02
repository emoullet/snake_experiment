from glob import glob
from setuptools import find_packages, setup


package_name = "snake_experiment_ui"


setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        (
            "share/ament_index/resource_index/packages",
            ["resource/" + package_name],
        ),
        ("share/" + package_name, ["package.xml"]),
        (
            "share/" + package_name + "/launch",
            glob("launch/*.launch.py")
            + glob("bringup/joystick_mapper/launch/*.launch.py"),
        ),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        (
            "share/" + package_name + "/bringup/cartesian_manager/config",
            glob("bringup/cartesian_manager/config/*.yaml"),
        ),
        (
            "share/" + package_name + "/bringup/joystick_mapper/config",
            glob("bringup/joystick_mapper/config/*.yaml"),
        ),
        (
            "share/" + package_name + "/templates",
            glob("snake_experiment_ui/templates/*.html"),
        ),
        (
            "share/" + package_name + "/static",
            glob("snake_experiment_ui/static/*"),
        ),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Etienne Moullet",
    maintainer_email="etienne.moullet@gmail.com",
    description="Operator web interfaces for the Snake experiment",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "target_calibration_node = snake_experiment_ui.node:main",
            "session_interface_node = snake_experiment_ui.session_node:main",
        ],
    },
)
