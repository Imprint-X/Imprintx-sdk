"""Display live ImprintX Sensor events; press q to close."""
import argparse
import os
import threading
import time

os.environ["QT_LOGGING_RULES"] = "*.warning=false"
import cv2
import numpy as np

from imprintx import SENSOR_HEIGHT, SENSOR_WIDTH
from imprintx.messaging import CONTROL_ENDPOINT, DATA_ENDPOINT, Control

from client import Client


def show_gui(data_endpoint=DATA_ENDPOINT, control_endpoint=CONTROL_ENDPOINT,
             display_fps=60.0):
    pending = [np.zeros((SENSOR_HEIGHT, SENSOR_WIDTH, 3), dtype=np.uint8)]
    frame = np.zeros_like(pending[0])
    image_lock = threading.Lock()
    stop = threading.Event()
    receive_error = []
    next_display = time.monotonic()

    with Client(data_endpoint, control_endpoint) as sensor:
        sensor.control(Control.SET_LIGHT, True)
        receiver = None
        try:
            sensor.control(Control.START)

            def receive():
                try:
                    while not stop.is_set():
                        batch = sensor.receive()
                        with image_lock:
                            image = pending[0]
                            for event in batch:
                                image[event.y, event.x] = (
                                    (0, 0, 255) if event.polarity else (255, 0, 0)
                                )
                except BaseException as error:
                    if not stop.is_set():
                        receive_error.append(error)
                    stop.set()

            receiver = threading.Thread(target=receive, name="sensor-receiver")
            receiver.start()
            while True:
                if stop.wait(max(0.0, next_display - time.monotonic())):
                    if receive_error:
                        raise receive_error[0]
                    break
                with image_lock:
                    frame, pending[0] = pending[0], frame
                cv2.imshow("ImprintX Sensor", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                frame.fill(0)
                next_display = time.monotonic() + 1.0 / display_fps
        finally:
            stop.set()
            if receiver is not None:
                receiver.join()
            try:
                sensor.control(Control.STOP)
            finally:
                sensor.control(Control.SET_LIGHT, False)
                cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-endpoint", default=DATA_ENDPOINT)
    parser.add_argument("--control-endpoint", default=CONTROL_ENDPOINT)
    parser.add_argument("--display-fps", type=float, default=60.0)
    args = parser.parse_args()
    if not np.isfinite(args.display_fps) or args.display_fps <= 0:
        parser.error("--display-fps must be finite and positive")
    try:
        show_gui(args.data_endpoint, args.control_endpoint, args.display_fps)
    except KeyboardInterrupt:
        pass
