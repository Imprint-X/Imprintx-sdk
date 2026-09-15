# ImprintX Python Examples

本目录提供 ImprintX Sensor 和 Touch Glove 的 Python 示例。以下命令均从项目根目录运行。

## 安装

```bash
python -m pip install imprintx
python -m pip install -r requirements.txt
```

Touch Glove 录像还需要系统已安装支持 `libx265` 的 FFmpeg。

## ImprintX Sensor

读取序列号时直接访问设备，无需启动 SDK 服务：

```bash
python sensor/read_sn.py /dev/ttyACM0
```

先启动 SDK 服务：

```bash
imprintx-sdk --device sensor /dev/ttyACM0
```

在另一个终端运行示例：

```bash
# 查看事件流
python sensor/stream.py --duration 10

# 显示事件图像，按 q 退出
python sensor/gui.py

# 录制事件数据
python sensor/record.py --output sensor_events.json --duration 10
```

## Touch Glove

先启动 SDK 服务：

```bash
imprintx-sdk --device glove /dev/ttyACM0
```

在另一个终端运行示例：

```bash
# 查看五通道数据流
python glove/stream.py --duration 10

# 显示五通道图像和帧率，按 q 退出
python glove/gui.py

# 保存五通道 PNG 和拼图
python glove/save_image.py --output-dir snapshots

# 录制 10 秒
python glove/record.py --output output.mp4 --duration 10

# 使用 Glove 按键开始和停止录制
python glove/record.py --output button.mp4 --button

# 读取录像时间戳
python glove/load_timestamps.py output.mp4

# 查看图像上报状态
python glove/control.py get_image_reporting
```

录像会生成 MP4、图像时间戳 JSON 和 IMU JSON 三个文件。输出文件已存在时不会覆盖。
