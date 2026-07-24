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
- 常驻 rclpy topic 频率监测，默认关闭，可按需在页面开启
- 公共路径变量、模块路径和服务器端口统一由 YAML 配置
- 网页配置预检，检查工作目录、setup、可执行文件和 Python 脚本
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

`run_dev.sh` 会读取 `config/modules.yaml` 中的 `paths.ros_setup`、`monitor_setups`、`host` 和 `port`，不再单独写死 ROS 与 MID360 路径。监测 setup 用于让后端导入 Livox 等自定义消息。
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

如果曾经因为后端重启或异常退出留下 ROS 残余进程，先 dry-run 查看：

```bash
./scripts/cleanup_ros_modules.sh
```

确认列表无误后再清理：

```bash
./scripts/cleanup_ros_modules.sh --kill
```

浏览器访问：

```text
http://AGX_IP:8080
```

## 桌面双击启动

不使用开机自启动时，可以为当前 Ubuntu 用户安装“启动”和“停止”两个快捷方式，无需 `sudo`：

```bash
cd /data/sinuo_project/robot_web_launcher
./scripts/install_desktop_shortcuts.sh
```

机械臂主机如果要默认加载 `config/modules_arm.yaml`，安装机械臂专用快捷方式：

```bash
cd ~/robot_web_launcher
./scripts/install_desktop_shortcuts.sh --arm
```

机械臂专用快捷方式会使用独立的 pid、lock 和日志文件，不会和底盘默认快捷方式互相误停。默认打开 `modules_arm.yaml` 中配置的端口，一般是 `8081`。

安装后可以从 Ubuntu 应用菜单搜索：

- `拉风机器人控制台`：后台未运行时启动服务，等待接口就绪后打开浏览器；已经运行时只打开浏览器，不会重复启动。
- `停止拉风机器人控制台`：先发送正常退出信号，等待后台停止所管理的 ROS 模块，超时后才强制结束。
- `机械臂机器人控制台`：启动机械臂主机配置。
- `停止机械臂机器人控制台`：停止机械臂主机配置启动的后台服务。

系统支持桌面图标时，也会把图标复制到当前用户的桌面目录。首次双击若被 Ubuntu 拦截，右键图标选择“允许启动”。

桌面启动日志位于：

```text
runtime/logs/web_launcher.log
```

卸载快捷方式：

```bash
./scripts/install_desktop_shortcuts.sh --remove

# 卸载机械臂专用快捷方式
./scripts/install_desktop_shortcuts.sh --arm --remove
```

桌面启动和 systemd 服务不要同时使用。如果以后启用 systemd，应先用“停止拉风机器人控制台”结束桌面启动的后台，再安装并启动 systemd 服务。

## 生产运行

使用安装脚本生成并安装 systemd 服务。脚本默认采用当前登录用户，也可以通过 `SERVICE_USER` 指定：

```bash
SERVICE_USER=nvidia1 PROJECT_DIR=/data/sinuo_project/robot_web_launcher ./scripts/install_service.sh
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
host: 0.0.0.0
port: 8080
paths:
  project_root: /data/sinuo_project
  ros_setup: /opt/ros/humble/setup.bash
  object_detection_python: /home/nvidia1/miniforge3/envs/zhaigou/bin/python
monitor_setups:
  - ${project_root}/mid_ws/install/setup.bash
```

`paths` 中的变量可以在 `workdir`、`setup`、`cmd`、`env` 和 `python_script` 中通过 `${变量名}` 使用。未定义变量会原样保留，因此 `${LD_LIBRARY_PATH}` 仍会在模块启动时按运行环境展开。

```yaml
module_id:
  name: 显示名称
  domain_id: 10
  workdir: ${project_root}/xxx_ws
  setup: install/setup.bash
  cmd: ["ros2", "launch", "pkg", "xxx.launch.py"]
```

目标检测这类 Python 模块建议直接指定环境里的 Python，避免 `conda activate` 依赖 shell 初始化脚本：

```yaml
object_detection:
  name: 目标检测
  domain_id: 20
  workdir: ${project_root}/object_detection/26_4_9
  cmd: ["${object_detection_python}"]
  python_script: "导航-all2-单侧点云-先验更远-目标范围-先验选点优化-读取内参-先验姿态优化.py"
```

如果仍希望使用 `conda activate`，可以配置：

```yaml
conda_env: zhaigou
conda_sh: /home/nvidia/anaconda3/etc/profile.d/conda.sh
```

## 本地配置覆盖，避免 git pull 冲突

仓库里的 `config/modules.yaml` 和 `config/modules_arm.yaml` 建议只当作模板维护，不再直接写现场差异。现场机器上的路径、IP、端口、Python 环境、录包目录等内容，放到同名的 `.local.yaml` 里：

```text
config/modules.yaml              仓库模板，底盘从机默认配置
config/modules.local.yaml        底盘从机现场覆盖，不提交 git
config/modules_arm.yaml          仓库模板，机械臂主机默认配置
config/modules_arm.local.yaml    机械臂主机现场覆盖，不提交 git
```

启动时仍然可以使用原来的命令：

```bash
./scripts/run_dev.sh
```

程序会自动读取 `config/modules.yaml`，如果旁边存在 `config/modules.local.yaml`，就把 local 文件里的字段覆盖到模板上。机械臂配置同理：

```bash
ROBOT_LAUNCHER_CONFIG=$PWD/config/modules_arm.yaml ./scripts/run_dev.sh
```

如果存在 `config/modules_arm.local.yaml`，也会自动覆盖 `modules_arm.yaml`。

第一次配置现场机器时，建议复制示例文件：

```bash
# 底盘从机
cp config/modules.local.example.yaml config/modules.local.yaml

# 机械臂主机
cp config/modules_arm.local.example.yaml config/modules_arm.local.yaml
```

`.local.yaml` 只需要写和模板不同的部分，不需要复制整份配置。例如只修改从机 IP：

```yaml
cluster:
  hosts:
    chassis:
      base_url: http://192.168.1.166:8080
```

或者只修改目标检测 Python 环境：

```yaml
paths:
  object_detection_python: /home/nvidia1/miniforge3/envs/zhaigou/bin/python
```

合并规则是：字典递归合并，列表、字符串、数字直接由 `.local.yaml` 覆盖模板。因此 `monitor_setups`、`cmd`、`topics` 这类列表如果写在 local 里，会整体替换模板中的对应列表。

## 调试状态发布

控制台支持一个独立的“调试状态发布”栏目，用于在网页上手动发送固定的 `ros2 topic pub --once` 调试状态位。该功能只允许发布 YAML 白名单中配置好的 topic 和 payload，不开放任意 topic 输入。

机械臂配置中默认包含：

```yaml
debug_publish:
  enabled: true
  commands:
    robot_state_2:
      name: robot_state = 2
      domain_id: 20
      topic: /robot_state
      msg_type: std_msgs/msg/Int32
      payload: "data: 2"
    arm_state_3:
      name: arm_state = 3
      domain_id: 20
      topic: /arm_state
      msg_type: std_msgs/msg/Int32
      payload: "data: 3"
    target_process_command_1:
      name: target_process_command = 1
      domain_id: 20
      topic: /target_process_command
      msg_type: std_msgs/msg/Int32
      payload: "data: 1"
```

如果现场要临时增加调试项，建议写到 `config/modules_arm.local.yaml`，不要直接改模板文件。

## 双 Orin 从机配置

机械臂从机可以复用同一套后端，但使用独立配置文件：

```bash
cd /data/sinuo_project/robot_web_launcher
ROBOT_LAUNCHER_CONFIG=/data/sinuo_project/robot_web_launcher/config/modules_arm.yaml ./scripts/run_dev.sh
```

`config/modules_arm.yaml` 默认监听 `8081` 端口，适合在机械臂 Orin 上作为主机控制台运行：

```text
http://机械臂主机IP:8081
```

这份配置把机械臂流程拆成：

- `FR10 机器人`
- `点云转换与裁切`
- `力传感器`
- `力传感器监控`
- `机械臂 RGB-D`
- `ICP 算法`

机械臂、相机、力传感器、ICP 等模块默认都保持 `autostart: false`，由现场人员确认后手动启动。示例配置默认使用 `ROS_DOMAIN_ID=20`；如果手工终端流程仍在其它 Domain 运行，需要同步修改 `modules_arm.yaml` 中各模块的 `domain_id`。

### 机械臂主机聚合底盘从机

`config/modules_arm.yaml` 已开启最小集群模式：

```yaml
cluster:
  enabled: true
  self: arm
  hosts:
    arm:
      name: 机械臂主机
      kind: local
    chassis:
      name: 底盘从机
      kind: remote
      base_url: http://192.168.1.166:8080
```

推荐启动顺序：

```bash
# 底盘从机，192.168.1.166
cd /data/sinuo_project/robot_web_launcher
./scripts/run_dev.sh

# 机械臂主机，192.168.1.101
cd ~/robot_web_launcher
ROBOT_LAUNCHER_CONFIG=$PWD/config/modules_arm.yaml ./scripts/run_dev.sh
```

机械臂主机启动前先确认能访问底盘从机：

```bash
curl http://192.168.1.166:8080/api/modules
```

集群模式下，统一页面中的模块 ID 会带主机前缀，例如：

```text
arm:force_sensor
chassis:lidar
chassis:fast_lio
```

如果底盘从机后端未启动或 IP 不通，页面会显示 `底盘从机 离线`，不会影响机械臂主机本地模块操作。

## 重要注意

1. `uvicorn --workers` 必须是 `1`，否则会有多个进程管理器同时控制同一批 ROS 模块。
2. 不要把这个 Web 页面暴露到公网。现场局域网使用即可。
3. 自动启动时建议关闭 RViz，比如 `rviz:=false`。
4. 串口设备建议用 udev 固定名称，不要长期依赖 `/dev/ttyUSB0`。
5. 频率监测需要后端 Python 能导入 `rclpy` 和被监测话题的消息类型。开发脚本和 systemd 服务会读取 YAML 中的 `paths.ros_setup` 与 `monitor_setups`；ROS 安装路径或工作空间变化时只需修改配置。
6. 后端退出时会尝试停止所有由它管理的 ROS 模块；若后端异常退出导致旧进程残留，可用 `scripts/cleanup_ros_modules.sh` 清理。
7. 频率监测按 topic 独立线程连续运行，并优先使用 rclpy raw subscription，只统计消息到达时间，尽量避免对 MID360 `/livox/lidar` 这类大消息做 Python 反序列化。连续监测高频话题仍会占用明显 CPU，因此默认关闭，需要时再从页面开启。
8. `depends_on` 只用于页面提示和启动前 warning，不会自动启动依赖模块；底盘控制等安全敏感模块应保持 `autostart: false`，由现场人员确认后手动启动。
9. 对 IMU 这类容易残留的硬件驱动，可以在模块配置里写 `process_patterns`。后端启动前会检查这些进程，发现残留时拒绝重复启动；停止后也会按这些 pattern 做兜底清理。N300pro IMU 默认已配置 `hipnuc_imu/lib/hipnuc` 等 pattern。
