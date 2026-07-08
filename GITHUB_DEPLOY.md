# GitHub 仓库维护与 AGX 部署流程

## 1. 第一次推到 GitHub

在本机进入本目录：

```bash
cd robot_web_launcher
git init
git add .
git commit -m "Initial robot web launcher"
git branch -M main
git remote add origin git@github.com:<your-org-or-user>/robot-web-launcher.git
git push -u origin main
```

如果你使用 HTTPS：

```bash
git remote add origin https://github.com/<your-org-or-user>/robot-web-launcher.git
```

## 2. AGX Orin 第一次部署

```bash
cd /data/sinuo_project
git clone git@github.com:<your-org-or-user>/robot-web-launcher.git robot_web_launcher
cd robot_web_launcher
./scripts/install_service.sh
```

浏览器访问：

```text
http://<AGX_IP>:8080
```

## 3. 后续更新

开发电脑修改代码后：

```bash
git add .
git commit -m "Update launcher config"
git push
```

AGX 上更新：

```bash
cd /data/sinuo_project/robot_web_launcher
./scripts/update_on_robot.sh
```

## 4. 常用维护命令

查看服务状态：

```bash
systemctl status robot-web-launcher --no-pager
```

查看后台服务日志：

```bash
journalctl -u robot-web-launcher -f
```

查看各 ROS 模块运行日志：

```bash
tail -f /data/sinuo_project/robot_web_launcher/runtime/logs/<module_id>.log
```

停止 Web 启动器：

```bash
sudo systemctl stop robot-web-launcher
```

开机自启：

```bash
sudo systemctl enable robot-web-launcher
```

关闭开机自启：

```bash
sudo systemctl disable robot-web-launcher
```

