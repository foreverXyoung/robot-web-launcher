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

Conda 模块示例：

```yaml
object_detection:
  name: Object Detection
  domain_id: 20
  workdir: /data/sinuo_project/object_detection/26_4_9
  conda_env: zhaigou
  cmd: ["python", "导航-all2-单侧点云-先验更远-目标范围-先验选点优化-读取内参-先验姿态优化.py"]
```

如果你的 Conda 不在 `~/miniconda3`，需要加：

```yaml
conda_sh: /home/nvidia/anaconda3/etc/profile.d/conda.sh
```

## 重要注意

1. `uvicorn --workers` 必须是 `1`，否则会有多个进程管理器同时控制同一批 ROS 模块。
2. 不要把这个 Web 页面暴露到公网。现场局域网使用即可。
3. 自动启动时建议关闭 RViz，比如 `rviz:=false`。
4. 串口设备建议用 udev 固定名称，不要长期依赖 `/dev/ttyUSB0`。
5. `system_real_robot.launch` 建议改名为 `system_real_robot.launch.py`，配置里已经按 `.launch.py` 写了。
