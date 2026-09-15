"""Shared subscriber and control client for the Touch Glove examples."""
from __future__ import annotations

import argparse
import json
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass

import numpy as np
import zmq

from imprintx import IMAGE_HEIGHT, IMAGE_WIDTH
from imprintx.messaging import (
    CONTROL_ENDPOINT,
    DATA_ENDPOINT,
    IMU_FIELDS,
    QUEUE_SIZE,
    Control,
    DeviceSyncState,
    send_json,
    socket,
)

_MISSING = object()


class StreamDiscontinuityError(RuntimeError):
    def __init__(self, message, batch):
        super().__init__(message)
        self.batch = batch


def argument_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--data-endpoint", default=DATA_ENDPOINT)
    parser.add_argument("--control-endpoint", default=CONTROL_ENDPOINT)
    return parser


@dataclass(frozen=True)
class Frame:
    channel: int
    raw_channel: int
    sensor_id: str
    seq_id: int
    timestamp_us: int
    image: np.ndarray


@dataclass(frozen=True)
class FrameBatch:
    session: str
    batch_id: int
    timestamp_us: int
    timestamp_spread_us: int
    host_interval_us: int
    frames: tuple

    @property
    def total_frames(self):
        return len(self.frames)

    def __iter__(self):
        return iter(self.frames)


@dataclass(frozen=True)
class ImuSample:
    sensor_time: int
    accel_x: int
    accel_y: int
    accel_z: int
    gyro_x: int
    gyro_y: int
    gyro_z: int


@dataclass(frozen=True)
class ImuBatch:
    samples: tuple

    def __len__(self):
        return len(self.samples)

    def __iter__(self):
        return iter(self.samples)


class Client:
    """Subscribe before START; auxiliary high-rate topics are explicitly opt-in."""

    def __init__(self, data_endpoint=DATA_ENDPOINT, control_endpoint=CONTROL_ENDPOINT, *,
                 subscribe_images=True, subscribe_imu=False,
                 subscribe_device_states=False):
        self.context = zmq.Context()
        self.data = socket(self.context, zmq.SUB)
        self.commands = socket(self.context, zmq.PUSH)
        self.reply_topic = b"reply/" + uuid.uuid4().hex.encode()
        self.data_endpoint = data_endpoint
        self.control_endpoint = control_endpoint
        self.pending_events = deque()
        self.previous = None
        self.subscribe_images = subscribe_images
        self.subscribe_imu = subscribe_imu
        self.subscribe_device_states = subscribe_device_states

    def __enter__(self):
        # Subscription order makes the ready reply a barrier for batch delivery.
        try:
            if self.subscribe_images:
                self.data.subscribe(b"batch")
            if self.subscribe_imu:
                self.data.subscribe(b"imu")
            if self.subscribe_device_states:
                self.data.subscribe(b"device_state")
            self.data.subscribe(b"error")
            self.data.subscribe(self.reply_topic)
            self.data.connect(self.data_endpoint)
            self.commands.connect(self.control_endpoint)
            deadline = time.monotonic() + 3.0
            while True:
                topic, value = self._receive(deadline)
                if topic == self.reply_topic and value == {"ready": True}:
                    return self
                self._queue(topic, value)
        except BaseException:
            self.close()
            raise

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.data.close()
        self.commands.close()
        self.context.term()

    def _receive(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self.data.poll(math.ceil(remaining * 1000)):
            raise TimeoutError("SDK data/control response timed out")
        parts = self.data.recv_multipart(copy=False)
        if len(parts) < 2:
            raise ValueError("Incomplete SDK multipart message")
        topic = bytes(parts[0])
        value = json.loads(bytes(parts[1]))
        if topic == b"error":
            raise RuntimeError(value["error"])
        if topic == b"imu":
            if (len(parts) != 2 or not isinstance(value, dict)
                    or set(value) != {"samples"}
                    or not isinstance(value["samples"], list)):
                raise ValueError("Invalid IMU report envelope")
            if any(not isinstance(sample, dict)
                   or set(sample) != set(IMU_FIELDS)
                   or any(type(axis) is not int for axis in sample.values())
                   for sample in value["samples"]):
                raise ValueError("Invalid raw IMU sample")
            samples = tuple(ImuSample(**sample) for sample in value["samples"])
            for sample in samples:
                if (not 0 <= sample.sensor_time <= 0xFFFFFFFF
                        or any(not -32768 <= axis <= 32767 for axis in (
                            sample.accel_x, sample.accel_y, sample.accel_z,
                            sample.gyro_x, sample.gyro_y, sample.gyro_z))):
                    raise ValueError("Invalid raw IMU sample")
            return topic, ImuBatch(samples)
        if topic == b"device_state":
            if (len(parts) != 2 or not isinstance(value, dict) or set(value) != {"state"}
                    or type(value["state"]) is not int):
                raise ValueError("Invalid device-state report envelope")
            return topic, DeviceSyncState(value["state"])
        if topic != b"batch":
            if len(parts) != 2:
                raise ValueError("Unexpected control response payload")
            return topic, value
        metadata = value.pop("frames")
        if len(metadata) != len(parts) - 2 or not 1 <= len(metadata) <= 5:
            raise ValueError("Incomplete image batch")
        frames = tuple(
            Frame(**info, image=np.frombuffer(part.buffer, dtype=np.uint8).reshape(
                IMAGE_HEIGHT, IMAGE_WIDTH))
            for info, part in zip(metadata, parts[2:])
        )
        batch = FrameBatch(**value, frames=frames)
        channels = [frame.channel for frame in frames]
        timestamps = [frame.timestamp_us for frame in frames]
        spread = max(timestamps) - min(timestamps)
        if (channels != sorted(set(channels)) or any(ch not in range(5) for ch in channels)
                or len({f.raw_channel for f in frames}) != len(frames)
                or min(timestamps) <= 0 or spread != batch.timestamp_spread_us
                or spread > 5000 or batch.timestamp_us not in timestamps):
            raise RuntimeError("[TS_ERROR_DATA:-8] subscribe: Invalid synchronized batch")
        if self.previous is not None:
            prev = self.previous
            problems = []
            if batch.session != prev.session:
                problems.append(f"session {prev.session}->{batch.session}")
            if batch.batch_id != prev.batch_id + 1:
                problems.append(f"batch {prev.batch_id}->{batch.batch_id}")
            if batch.timestamp_us <= prev.timestamp_us:
                problems.append(f"batch timestamp {prev.timestamp_us}->{batch.timestamp_us}")
            if ([(f.channel, f.raw_channel, f.sensor_id) for f in frames]
                    != [(f.channel, f.raw_channel, f.sensor_id) for f in prev]):
                problems.append("channel identity changed")
            for frame, old in zip(frames, prev):
                seq_delta = (frame.seq_id - old.seq_id) & 0xFFFFFFFF
                if seq_delta != 1:
                    problems.append(
                        f"ch{frame.channel}/raw{frame.raw_channel} seq "
                        f"{old.seq_id}->{frame.seq_id} (delta {seq_delta})")
                if frame.timestamp_us <= old.timestamp_us:
                    problems.append(
                        f"ch{frame.channel}/raw{frame.raw_channel} timestamp "
                        f"{old.timestamp_us}->{frame.timestamp_us}")
            if problems:
                self.previous = batch
                raise StreamDiscontinuityError(
                    "[TS_ERROR_DATA:-8] subscribe: " + "; ".join(problems), batch
                )
        self.previous = batch
        return topic, batch

    def _queue(self, topic, value):
        if topic not in (b"batch", b"imu", b"device_state"):
            raise RuntimeError("Unexpected SDK reply")
        if ((topic == b"batch" and not self.subscribe_images)
                or (topic == b"imu" and not self.subscribe_imu)):
            return
        if sum(queued_topic == topic for queued_topic, _ in self.pending_events) >= QUEUE_SIZE:
            raise RuntimeError(
                f"[TS_ERROR_DATA:-8] subscribe: Client {topic.decode()} queue is full"
            )
        self.pending_events.append((topic, value))

    def _pop_pending(self, expected_topic):
        for index, (topic, value) in enumerate(self.pending_events):
            if topic == expected_topic:
                del self.pending_events[index]
                return value
        return _MISSING

    def control(self, command, value=None):
        request_id = uuid.uuid4().hex
        send_json(self.commands, b"control", {
            "id": request_id, "reply": self.reply_topic.decode(),
            "command": int(Control(command)), "value": value,
        })
        deadline = time.monotonic() + 3.0
        while True:
            try:
                topic, result = self._receive(deadline)
            except StreamDiscontinuityError as error:
                self._queue(b"batch", error.batch)
                continue
            if topic == self.reply_topic and result.get("id") == request_id:
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result["result"]
            self._queue(topic, result)

    def receive(self, timeout=2.0):
        if not self.subscribe_images:
            raise RuntimeError("Client is not subscribed to image batches")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        pending = self._pop_pending(b"batch")
        if pending is not _MISSING:
            return pending
        deadline = time.monotonic() + timeout
        while True:
            topic, value = self._receive(deadline)
            if topic == b"batch":
                return value
            self._queue(topic, value)

    def receive_imu(self, timeout=2.0):
        if not self.subscribe_imu:
            raise RuntimeError("Client is not subscribed to IMU reports")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        pending = self._pop_pending(b"imu")
        if pending is not _MISSING:
            return pending
        deadline = time.monotonic() + timeout
        while True:
            topic, value = self._receive(deadline)
            if topic == b"imu":
                return value
            self._queue(topic, value)

    def receive_device_sync_state(self, timeout=2.0):
        if not self.subscribe_device_states:
            raise RuntimeError("Client is not subscribed to device-state reports")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        pending = self._pop_pending(b"device_state")
        if pending is not _MISSING:
            return pending
        deadline = time.monotonic() + timeout
        while True:
            topic, value = self._receive(deadline)
            if topic == b"device_state":
                return value
            self._queue(topic, value)

    def receive_event(self, timeout=2.0):
        """Return the next event; do not mix with topic-specific receive methods."""
        if not (self.subscribe_images or self.subscribe_imu or self.subscribe_device_states):
            raise RuntimeError("Client is not subscribed to any data topics")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if self.pending_events:
            return self.pending_events.popleft()
        deadline = time.monotonic() + timeout
        while True:
            topic, value = self._receive(deadline)
            if ((topic == b"batch" and not self.subscribe_images)
                    or (topic == b"imu" and not self.subscribe_imu)):
                continue
            if topic in (b"batch", b"imu", b"device_state"):
                return topic, value
            self._queue(topic, value)

    def discard_pending_data(self):
        """Set the next image/IMU event as the local recording boundary."""
        self.pending_events = deque(
            event for event in self.pending_events if event[0] not in (b"batch", b"imu")
        )

    def unsubscribe_recording_data(self):
        """Stop receiving image and IMU data without affecting the device."""
        if self.subscribe_images:
            self.data.unsubscribe(b"batch")
            self.subscribe_images = False
        if self.subscribe_imu:
            self.data.unsubscribe(b"imu")
            self.subscribe_imu = False
        self.discard_pending_data()
