# Robot Web Launcher

这是给 AGX Orin + JetPack 6 + ROS 2 Humble 使用的 Web 后台启动器。它不改动现有 ROS 工作空间，只负责按配置启动、停止、重启模块，并把终端输出实时推到网页。

## 功能

- 一键启动勾选模块
- 停止勾选模块
- 单独启动 / 停止 / 重启模块
- 按模块设置 `ROS_DOMAIN_ID`
- 支持 `source install/setup.bash`
- 支持 Conda 环境中的 Python 检测脚本
- 使用 Linux 进程组停止 ROS launch 及其子进程
- WebSocket 实时日志
- 常驻 rclpy 传感器 topic 频率监测，默认开启，可在页面关闭
- systemd 开机自启模板

## 安装

在 AGX Orin 上放到：

```bash
/data/sinuo_project/robot_web_launcher
```

安装依赖：

```bash
cd /data/sinuo_project/robot_web_launcher
python3 -m pip install -r requirements.txt
```

开发运行：

```bash
./scripts/run_dev.sh
```

`run_dev.sh` 会自动尝试 source `/opt/ros/humble/setup.bash` 和 `/data/sinuo_project/mid_ws/install/setup.bash`，这样后端的常驻 rclpy 监测器可以导入 ROS 2 Python 包以及 Livox 自定义消息。
默认是单进程模式并关闭 access log，适合现场调试。若确实需要热重载：

```bash
ROBOT_LAUNCHER_RELOAD=1 ./scripts/run_dev.sh
```

如果还有其它频率监测话题使用自定义消息类型，可以额外指定 workspace setup：

```bash
ROBOT_LAUNCHER_EXTRA_SETUPS=/data/xxx_ws/install/setup.bash ./scripts/run_dev.sh
```

停止开发服务：

```bash
./scripts/stop_dev.sh
```

浏览器访问：

```text
http://AGX_IP:8080
```

## 生产运行

复制 systemd 服务：

```bash
sudo cp systemd/robot-web-launcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable robot-web-launcher
sudo systemctl start robot-web-launcher
```

查看服务日志：

```bash
journalctl -u robot-web-launcher -f
```

如果希望后台服务启动后自动启动 `autostart: true` 的模块，把 service 里的：

```ini
Environment=ROBOT_LAUNCHER_AUTOSTART=0
```

改成：

```ini
Environment=ROBOT_LAUNCHER_AUTOSTART=1
```

## 配置模块

模块在 `config/modules.yaml` 里配置。每个模块至少需要：

```yaml
module_id:
  name: 显示名称
  domain_id: 10
  workdir: /data/sinuo_project/xxx_ws
  setup: install/setup.bash
  cmd: ["ros2", "launch", "pkg", "xxx.launch.py"]
```

目标检测这类 Python 模块建议直接指定环境里的 Python，避免 `conda activate` 依赖 shell 初始化脚本：

```yaml
object_detection:
  name: 目标检测
  domain_id: 20
  workdir: /data/sinuo_project/object_detection/26_4_9
  cmd: ["/home/nvidia1/miniforge3/envs/zhaigou/bin/python"]
  python_script: "导航-all2-单侧点云-先验更远-目标范围-先验选点优化-读取内参-先验姿态优化.py"
```

如果仍希望使用 `conda activate`，可以配置：

```yaml
conda_env: zhaigou
conda_sh: /home/nvidia/anaconda3/etc/profile.d/conda.sh
```

## 重要注意

1. `uvicorn --workers` 必须是 `1`，否则会有多个进程管理器同时控制同一批 ROS 模块。
2. 不要把这个 Web 页面暴露到公网。现场局域网使用即可。
3. 自动启动时建议关闭 RViz，比如 `rviz:=false`。
4. 串口设备建议用 udev 固定名称，不要长期依赖 `/dev/ttyUSB0`。
5. `system_real_robot.launch` 建议改名为 `system_real_robot.launch.py`，配置里已经按 `.launch.py` 写了。
6. 频率监测需要后端 Python 能导入 `rclpy` 和被监测话题的消息类型。开发脚本和 systemd 模板会 source `/opt/ros/humble/setup.bash` 以及 MID360 的 `mid_ws`；如果你的 ROS 安装路径或工作空间路径不同，需要同步修改脚本。
7. `depends_on` 只用于页面提示和启动前 warning，不会自动启动依赖模块；底盘控制等安全敏感模块应保持 `autostart: false`，由现场人员确认后手动启动。
