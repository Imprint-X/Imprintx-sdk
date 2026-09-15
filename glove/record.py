"""Record Touch Glove video and raw IMU for a duration or from the board button."""
from __future__ import annotations

import json
import math
import os
import subprocess
import time
import uuid
from contextlib import closing
from pathlib import Path

import numpy as np
from imprintx import IMAGE_HEIGHT, IMAGE_WIDTH
from imprintx.messaging import (
    CONTROL_ENDPOINT,
    DATA_ENDPOINT,
    IMU_FIELDS,
    DeviceSyncState,
)
from client import Client, Control, StreamDiscontinuityError, argument_parser

VIDEO_FPS = 30
IMU_NOMINAL_RATE_HZ = 200
IMU_SENSOR_TICKS_PER_SECOND = 25_600


class _DeviceStateError(RuntimeError):
    pass


def _recording_plan(output_path, crf):
    if not isinstance(crf, int) or not 0 <= crf <= 51:
        raise ValueError("crf must be an integer in range 0..51")
    output = Path(output_path)
    if output.suffix.lower() != ".mp4":
        raise ValueError("output_path must end in .mp4")
    timestamp_path = output.with_name(output.stem + "_timestamps.json")
    imu_path = output.with_name(output.stem + "_imu.json")
    partial_id = uuid.uuid4().hex
    video_partial = output.with_name(
        f".{output.stem}.{partial_id}.partial{output.suffix}"
    )
    timestamp_partial = timestamp_path.with_name(
        f".{timestamp_path.stem}.{partial_id}.partial{timestamp_path.suffix}"
    )
    imu_partial = imu_path.with_name(
        f".{imu_path.stem}.{partial_id}.partial{imu_path.suffix}"
    )
    final_paths = output, timestamp_path, imu_path
    partial_paths = video_partial, timestamp_partial, imu_partial
    if any(path.exists() for path in final_paths):
        raise FileExistsError("Video, timestamps, or IMU output already exists")
    width, height = IMAGE_WIDTH * 3, IMAGE_HEIGHT * 2
    pixels = np.full(height * width * 3 // 2, 128, dtype=np.uint8)
    canvas = pixels[:height * width].reshape(height, width)
    canvas.fill(0)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
        "-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", f"{width}x{height}",
        "-framerate", str(VIDEO_FPS), "-i", "pipe:0", "-an", "-c:v", "libx265",
        "-preset", "ultrafast", "-tune", "zerolatency", "-crf", str(crf),
        "-x265-params", "pools=2:frame-threads=1:wpp=0:log-level=error",
        "-pix_fmt", "yuv420p", "-bf", "0", "-tag:v", "hvc1", str(video_partial),
    ]
    return final_paths, partial_paths, pixels, canvas, command


def _write_json_item(file, index, value):
    file.write("," if index else "")
    file.write("\n")
    json.dump(value, file, indent=2)


def _record_events(events, plan, on_started=None):
    final_paths, partial_paths, pixels, canvas, command = plan
    output, timestamp_path, imu_path = final_paths
    video_partial, timestamp_partial, imu_partial = partial_paths
    frame_count = 0
    imu_report_count = 0
    imu_empty_report_count = 0
    imu_sample_count = 0
    previous_imu_time = None
    imu_elapsed_ticks = 0
    imu_max_interval_ticks = 0
    imu_rate = None
    try:
        with timestamp_partial.open("x", encoding="utf-8") as timestamps, \
                imu_partial.open("x", encoding="utf-8") as imu_file, \
                subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
            timestamps.write("[")
            imu_file.write("{")
            imu_file.write(f'\n  "nominal_sample_rate_hz": {IMU_NOMINAL_RATE_HZ},')
            imu_file.write('\n  "sensor_time_tick_us": 39.0625,')
            imu_file.write('\n  "samples": [')
            try:
                with closing(events):
                    for event_idx, (topic, value) in enumerate(events):
                        if topic == b"batch":
                            if (frame_count == 0
                                    and [frame.channel for frame in value]
                                    != list(range(5))):
                                raise ValueError("Recording requires all five image channels")
                            record = {"event_idx": event_idx,
                                      "frame_idx": frame_count,
                                      "master_timestamp_us": value.frames[0].timestamp_us}
                            for frame in value:
                                row, column = divmod(frame.channel, 3)
                                canvas[row * IMAGE_HEIGHT:(row + 1) * IMAGE_HEIGHT,
                                       column * IMAGE_WIDTH:(column + 1) * IMAGE_WIDTH] = (
                                    frame.image
                                )
                                record[f"ch{frame.channel}_timestamp_us"] = frame.timestamp_us
                                record[f"ch{frame.channel}_seq"] = frame.seq_id
                            encoder.stdin.write(memoryview(pixels))
                            _write_json_item(timestamps, frame_count, record)
                            frame_count += 1
                            if frame_count == 1 and on_started is not None:
                                on_started()
                        elif topic == b"imu":
                            if not value.samples:
                                imu_empty_report_count += 1
                            for sample in value:
                                sensor_time = sample.sensor_time & 0xFFFFFF
                                if previous_imu_time is not None:
                                    interval = (sensor_time - previous_imu_time) & 0xFFFFFF
                                    imu_elapsed_ticks += interval
                                    imu_max_interval_ticks = max(
                                        imu_max_interval_ticks, interval
                                    )
                                previous_imu_time = sensor_time
                                record = {"event_idx": event_idx,
                                          "sample_idx": imu_sample_count,
                                          "report_idx": imu_report_count}
                                record.update({
                                    name: getattr(sample, name) for name in IMU_FIELDS
                                })
                                _write_json_item(imu_file, imu_sample_count, record)
                                imu_sample_count += 1
                            imu_report_count += 1
            finally:
                encoder.stdin.close()
            if frame_count == 0:
                raise RuntimeError("Recording ended before the first complete image batch")
            if encoder.wait() != 0:
                raise RuntimeError("FFmpeg/libx265 encoding failed; see stderr")
            timestamps.write("\n]\n")
            imu_rate = ((imu_sample_count - 1) * IMU_SENSOR_TICKS_PER_SECOND
                        / imu_elapsed_ticks if imu_elapsed_ticks else None)
            imu_file.write("\n  ],")
            imu_file.write(f'\n  "sample_count": {imu_sample_count},')
            imu_file.write(f'\n  "report_count": {imu_report_count},')
            imu_file.write(f'\n  "empty_report_count": {imu_empty_report_count},')
            imu_file.write(f'\n  "max_interval_ticks": {imu_max_interval_ticks},')
            imu_file.write('\n  "measured_sample_rate_hz": ')
            json.dump(imu_rate, imu_file)
            imu_file.write("\n}\n")
        publish_pairs = (
            (imu_path, imu_partial),
            (timestamp_path, timestamp_partial),
            (output, video_partial),
        )
        try:
            for published, partial in publish_pairs:
                os.link(partial, published)
        except BaseException:
            for published, partial in publish_pairs:
                try:
                    if published.samefile(partial):
                        published.unlink()
                except OSError:
                    pass
            raise
    finally:
        for partial in partial_paths:
            partial.unlink(missing_ok=True)
    return {
        "video_path": str(output),
        "json_path": str(timestamp_path),
        "imu_path": str(imu_path),
        "frame_count": frame_count,
        "imu_report_count": imu_report_count,
        "imu_empty_report_count": imu_empty_report_count,
        "imu_sample_count": imu_sample_count,
        "imu_sample_rate_hz": imu_rate,
        "imu_max_interval_ticks": imu_max_interval_ticks,
    }


def _enable_imu(client):
    enabled = client.control(Control.GET_IMU_REPORTING)
    if type(enabled) is not bool:
        raise RuntimeError("SDK returned an invalid IMU reporting state")
    if not enabled:
        if client.control(Control.SET_IMU_REPORTING, True) is not True:
            raise RuntimeError("SDK did not acknowledge IMU reporting")
        if client.control(Control.GET_IMU_REPORTING) is not True:
            raise RuntimeError("Touch Glove did not enable IMU reporting")


def _timed_events(client, duration, started, stream_timeout=2.0):
    last_image = started
    try:
        while True:
            now = time.monotonic()
            duration_remaining = duration - (now - started)
            if duration_remaining <= 0:
                return
            image_remaining = stream_timeout - (now - last_image)
            if image_remaining <= 0:
                raise TimeoutError(f"No synchronized image batch for {stream_timeout:.3f}s")
            try:
                topic, value = client.receive_event(
                    min(duration_remaining, image_remaining, 0.1)
                )
            except TimeoutError:
                continue
            except StreamDiscontinuityError as error:
                topic, value = b"batch", error.batch
            if topic == b"batch":
                last_image = time.monotonic()
            yield topic, value
    finally:
        client.unsubscribe_recording_data()


def record_video(data_endpoint=DATA_ENDPOINT, control_endpoint=CONTROL_ENDPOINT,
                 output_path="output_h265.mp4", duration=5.0, crf=18):
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be finite and positive")
    plan = _recording_plan(output_path, crf)
    with Client(data_endpoint, control_endpoint, subscribe_imu=True) as client:
        _enable_imu(client)
        client.control(Control.START)
        started = time.monotonic()
        client.discard_pending_data()
        return _record_events(_timed_events(client, duration, started), plan)


def _set_device_state(client, state):
    try:
        actual = DeviceSyncState(
            client.control(Control.SET_DEVICE_SYNC_STATE, int(state))
        )
    except Exception as error:
        raise _DeviceStateError(str(error)) from error
    if actual != state:
        raise _DeviceStateError(
            f"Touch Glove rejected device state 0x{state:02X}; reported 0x{actual:02X}"
        )


def _button_events(client, stream_timeout):
    last_image = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            image_remaining = stream_timeout - (now - last_image)
            if image_remaining <= 0:
                raise TimeoutError(f"No synchronized image batch for {stream_timeout:.3f}s")
            try:
                topic, value = client.receive_event(
                    min(image_remaining, 0.1)
                )
            except TimeoutError:
                continue
            except StreamDiscontinuityError as error:
                topic, value = b"batch", error.batch
            if topic == b"device_state":
                if value == DeviceSyncState.RECORDING_END:
                    return
                if value == DeviceSyncState.ERROR:
                    raise RuntimeError("Touch Glove reported an error while recording")
            elif topic == b"batch":
                last_image = time.monotonic()
                yield topic, value
            elif topic == b"imu":
                yield topic, value
    finally:
        client.unsubscribe_recording_data()


def record_video_on_button(data_endpoint=DATA_ENDPOINT, control_endpoint=CONTROL_ENDPOINT,
                           output_path="output_h265.mp4", crf=18, stream_timeout=2.0):
    if not math.isfinite(stream_timeout) or stream_timeout <= 0:
        raise ValueError("stream_timeout must be finite and positive")
    plan = _recording_plan(output_path, crf)
    with Client(data_endpoint, control_endpoint,
                subscribe_imu=True, subscribe_device_states=True) as client:
        _set_device_state(client, DeviceSyncState.CONNECTED)
        print("Recorder armed; press the Glove button to start.", flush=True)
        while True:
            try:
                topic, value = client.receive_event(2.0)
            except TimeoutError:
                continue
            if topic == b"device_state":
                if value == DeviceSyncState.RECORDING_START:
                    break
                if value == DeviceSyncState.ERROR:
                    raise RuntimeError("Touch Glove reported an error while armed")

        def mark_recording():
            _set_device_state(client, DeviceSyncState.RECORDING)
            print("Recording; press the Glove button again to stop.", flush=True)

        _enable_imu(client)
        client.control(Control.START)
        client.discard_pending_data()
        try:
            result = _record_events(
                _button_events(client, stream_timeout), plan, on_started=mark_recording
            )
        except _DeviceStateError:
            raise
        except Exception:
            try:
                _set_device_state(client, DeviceSyncState.ERROR)
            except Exception:
                pass
            raise
        state = DeviceSyncState(client.control(Control.GET_DEVICE_SYNC_STATE))
        if state != DeviceSyncState.RECORDING_END:
            raise RuntimeError(f"Recording ended with unexpected device state 0x{state:02X}")
        _set_device_state(client, DeviceSyncState.CONNECTED)
        return result


if __name__ == "__main__":
    parser = argument_parser(__doc__)
    parser.add_argument("--output", default="output_h265.mp4")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--button", action="store_true",
                      help="wait for the board button to start and stop one recording")
    mode.add_argument("--duration", type=float,
                      help="record immediately for this many seconds (default: 5)")
    parser.add_argument("--crf", type=int, default=18)
    args = parser.parse_args()
    if args.button:
        result = record_video_on_button(
            args.data_endpoint, args.control_endpoint, args.output, args.crf)
    else:
        duration = 5.0 if args.duration is None else args.duration
        result = record_video(
            args.data_endpoint, args.control_endpoint,
            args.output, duration, args.crf)
    print(f"Recording saved to {result['video_path']}")
