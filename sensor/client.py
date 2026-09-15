"""Shared stream subscriber and control client for ImprintX Sensor examples."""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass

import zmq

from imprintx import SENSOR_HEIGHT, SENSOR_WIDTH
from imprintx.messaging import (
    CONTROL_ENDPOINT, DATA_ENDPOINT, Control, send_json, socket,
)


@dataclass(frozen=True)
class Event:
    x: int
    y: int
    polarity: bool
    timestamp_us: int


@dataclass(frozen=True)
class EventBatch:
    session: str
    batch_id: int
    timestamp_us: int
    events: tuple

    def __len__(self):
        return len(self.events)

    def __iter__(self):
        return iter(self.events)


class Client:
    def __init__(self, data_endpoint=DATA_ENDPOINT, control_endpoint=CONTROL_ENDPOINT):
        self.context = zmq.Context()
        self.data = socket(self.context, zmq.SUB)
        self.commands = socket(self.context, zmq.PUSH)
        self.reply_topic = b"reply/" + uuid.uuid4().hex.encode()
        self.data_endpoint = data_endpoint
        self.control_endpoint = control_endpoint
        self.previous = None

    def __enter__(self):
        try:
            self.data.subscribe(b"events")
            self.data.subscribe(b"error")
            self.data.subscribe(self.reply_topic)
            self.data.connect(self.data_endpoint)
            self.commands.connect(self.control_endpoint)
            deadline = time.monotonic() + 3.0
            while True:
                topic, value = self._receive(deadline)
                if topic == self.reply_topic and value == {"ready": True}:
                    return self
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
        parts = self.data.recv_multipart()
        if len(parts) != 2:
            raise ValueError("Invalid Sensor message")
        topic, payload = parts
        value = json.loads(payload)
        if topic == b"error":
            raise RuntimeError(value["error"])
        if topic != b"events":
            return topic, value
        if not isinstance(value, dict) or set(value) != {
                "session", "batch_id", "timestamp_us", "events"}:
            raise ValueError("Invalid Sensor event batch")
        try:
            events = tuple(Event(*event) for event in value.pop("events"))
        except (TypeError, ValueError):
            raise ValueError("Invalid Sensor event") from None
        if (not events or any(type(event.x) is not int
                              or type(event.y) is not int
                              or type(event.polarity) is not bool
                              or type(event.timestamp_us) is not int
                              or not 0 <= event.x < SENSOR_WIDTH
                              or not 0 <= event.y < SENSOR_HEIGHT
                              for event in events)
                or any(current.timestamp_us < previous.timestamp_us
                       for previous, current in zip(events, events[1:]))
                or value["timestamp_us"] != events[0].timestamp_us // 1000 * 1000):
            raise RuntimeError("[TS_ERROR_DATA:-8] subscribe: Invalid Sensor event batch")
        batch = EventBatch(**value, events=events)
        if self.previous is not None and (
                batch.session != self.previous.session
                or batch.batch_id != self.previous.batch_id + 1
                or batch.events[0].timestamp_us < self.previous.events[-1].timestamp_us):
            raise RuntimeError("[TS_ERROR_DATA:-8] subscribe: Stream changed or lost data")
        self.previous = batch
        return topic, batch

    def control(self, command, value=None):
        request_id = uuid.uuid4().hex
        send_json(self.commands, b"control", {
            "id": request_id, "reply": self.reply_topic.decode(),
            "command": int(Control(command)), "value": value,
        })
        deadline = time.monotonic() + 3.0
        while True:
            topic, result = self._receive(deadline)
            if topic == self.reply_topic and result.get("id") == request_id:
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result["result"]

    def receive(self, timeout=2.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        deadline = time.monotonic() + timeout
        while True:
            topic, value = self._receive(deadline)
            if topic == b"events":
                return value
