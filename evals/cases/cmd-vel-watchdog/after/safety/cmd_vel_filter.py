"""Velocity safety filter: stop the base when the lidar sees something close."""

import math

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import LaserScan


class CmdVelFilter(Node):
    def __init__(self) -> None:
        super().__init__("cmd_vel_filter")
        self.declare_parameter("stop_distance", 0.5)  # metres
        self.declare_parameter("scan_timeout", 0.5)  # seconds
        self._min_range = math.inf
        self._last_scan: Time | None = None
        self.create_subscription(LaserScan, "scan", self._on_scan, 10)
        self.create_subscription(Twist, "cmd_vel_raw", self._on_cmd_vel, 10)
        self._pub = self.create_publisher(Twist, "cmd_vel", 10)

    def _on_scan(self, msg: LaserScan) -> None:
        self._min_range = min((r for r in msg.ranges if r > msg.range_min), default=math.inf)
        self._last_scan = Time.from_msg(msg.header.stamp)

    def _scan_is_stale(self) -> bool:
        if self._last_scan is None:
            return True
        age = (self.get_clock().now() - self._last_scan).nanoseconds / 1e9
        return age > self.get_parameter("scan_timeout").value

    def _on_cmd_vel(self, msg: Twist) -> None:
        if self._scan_is_stale():
            self.get_logger().warn("no recent scan, stopping", throttle_duration_sec=2.0)
            self._pub.publish(Twist())  # full stop, never the last command
            return
        if self._min_range < self.get_parameter("stop_distance").value and msg.linear.x > 0.0:
            msg.linear.x = 0.0
        self._pub.publish(msg)


def main() -> None:
    rclpy.init()
    rclpy.spin(CmdVelFilter())
    rclpy.shutdown()
