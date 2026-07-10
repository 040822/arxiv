# systemd 服务管理

本文说明如何把项目注册为 Linux `systemd` 服务，并通过 `systemctl` 管理 Web 服务和内置 APScheduler 定时日报。

适用场景：服务器长期运行、开机自启、需要用 `journalctl` 查看日志。

---

## 部署前确认

1. 项目路径为 `/home/wenxin/office/arxiv`。
2. 运行服务的用户建议使用普通用户，例如 `wenxin`。
3. 该用户需要能读写 `data/` 和 `temp/`。
4. 不要同时手动运行 `python app.py` 和 systemd 服务，否则会启动两个进程，内置定时日报可能重复执行。

确认 Python 解释器路径：

```bash
cd /home/wenxin/office/arxiv
which python
```

如果使用 venv/uv，通常是：

```bash
/home/wenxin/office/arxiv/.venv/bin/python
```

如果使用 conda，建议直接使用环境里的 Python 绝对路径，例如：

```bash
/home/wenxin/miniconda3/envs/arxiv/bin/python
```

---

## 创建服务文件

创建 `/etc/systemd/system/arxiv-paper.service`：

```ini
[Unit]
Description=AI arXiv Paper Database
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wenxin
Group=wenxin
WorkingDirectory=/home/wenxin/office/arxiv
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/wenxin/office/arxiv/.venv/bin/python /home/wenxin/office/arxiv/app.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=multi-user.target
```

把 `ExecStart` 中的 Python 路径改成你的实际解释器路径。不要在 systemd 服务里依赖 `source .venv/bin/activate`，直接使用虚拟环境中的 Python 绝对路径更稳定。

如果尚未调整运行时目录权限：

```bash
sudo chown -R wenxin:wenxin /home/wenxin/office/arxiv/data /home/wenxin/office/arxiv/temp
```

---

## 启用与启动

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now arxiv-paper.service
systemctl status arxiv-paper.service
```

服务启动后访问：

```text
http://服务器地址:5000
```

项目默认监听 `0.0.0.0:5000`。如果只想通过 Nginx 反向代理访问，可把 `config.py` 中的 `WEB_HOST` 改为 `127.0.0.1`，或用防火墙限制 5000 端口。

---

## 常用命令

```bash
# 查看状态
systemctl status arxiv-paper.service

# 启动 / 停止 / 重启
sudo systemctl start arxiv-paper.service
sudo systemctl stop arxiv-paper.service
sudo systemctl restart arxiv-paper.service

# 开机自启 / 取消自启
sudo systemctl enable arxiv-paper.service
sudo systemctl disable arxiv-paper.service

# 实时查看日志
journalctl -u arxiv-paper.service -f

# 查看最近 200 行日志
journalctl -u arxiv-paper.service -n 200 --no-pager
```

修改服务文件后需要重新加载并重启：

```bash
sudo systemctl daemon-reload
sudo systemctl restart arxiv-paper.service
```

---

## 运行方式说明

`app.py` 启动时会执行 `create_app()`，初始化数据库、收口上次遗留的 running 任务日志，并启动 APScheduler。systemd 只负责守护这个单进程；定时日报仍由应用内部的 APScheduler 执行。

因此不建议在当前结构下使用多 worker 方式直接托管 `app.py`，否则每个 worker 都可能注册自己的定时任务。若未来改成 Gunicorn/Nginx 部署，需要先把定时任务拆到独立 worker 或只允许一个进程启动 scheduler。

---

## 排查

### 服务启动失败

先看日志：

```bash
journalctl -u arxiv-paper.service -n 200 --no-pager
```

常见原因：

- `ExecStart` 的 Python 路径不存在
- 依赖没有安装到该 Python 环境
- `data/` 或 `temp/` 权限不足
- 5000 端口已被另一个进程占用

### 修改代码后没有生效

重启服务：

```bash
sudo systemctl restart arxiv-paper.service
```

### 定时日报没有执行

检查三处：

1. 服务是否正在运行：`systemctl status arxiv-paper.service`
2. Web 设置页「定时任务」是否启用
3. 服务器本地时区和页面显示的下次执行时间是否符合预期

APScheduler 使用内存 job store，服务停机期间错过的触发不会自动补跑。需要补跑时可在 Web 页面手动执行「抓取、分析并生成报告」。
