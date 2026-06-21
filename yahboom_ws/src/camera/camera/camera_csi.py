import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
from jetcam.csi_camera import CSICamera

class CameraNode(Node):
    def __init__(self):
        super().__init__('camera_csi')
        self.publisher = self.create_publisher(Image, 'image_raw', 10)
        self.bridge = CvBridge()
        
        self.cap = CSICamera(capture_device=0, width=640, height=480)
        
        self.timer = self.create_timer(0.05, self.timer_callback)

    def timer_callback(self):
        frame = self.cap.read()
        if frame is not None:
            image_msg = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
            self.publisher.publish(image_msg)
        else:
            self.get_logger().warn('Failed to capture image')

def main(args=None):
    rclpy.init(args=args)
    node = CameraNode()
    rclpy.spin(node)

    node.cap.release()
    rclpy.shutdown()

if __name__ == '__main__':
    main()