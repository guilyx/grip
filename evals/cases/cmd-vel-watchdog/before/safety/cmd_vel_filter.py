"""Velocity safety filter: stop the base when the lidar sees something close."""

import math

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from sensor_msgs.msg import LaserScan

STOP_DISTANCE = 0.5  # metres


class CmdVelFilter(Node):
    def __init__(self) -> None:
        super().__init__("cmd_vel_filter")
        self._min_range = math.inf
        self.create_subscription(LaserScan, "scan", self._on_scan, 10)
        self.create_subscription(Twist, "cmd_vel_raw", self._on_cmd_vel, 10)
        self._pub = self.create_publisher(Twist, "cmd_vel", 10)

    def _on_scan(self, msg: LaserScan) -> None:
        self._min_range = min((r for r in msg.ranges if r > msg.range_min), default=math.inf)

    def _on_cmd_vel(self, msg: Twist) -> None:
        if self._min_range < STOP_DISTANCE and msg.linear.x > 0.0:
            msg.linear.x = 0.0
        self._pub.publish(msg)


def main() -> None:
    rclpy.init()
    rclpy.spin(CmdVelFilter())
    rclpy.shutdown()
