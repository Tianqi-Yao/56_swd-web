# edge：田间节点的采集与上传

这个文件夹整份复制到 Raspberry Pi 上就能运行。里面有两个程序，都是"跑一次就退出"，由 systemd timer 定时启动：

| 程序 | 何时运行 | 做什么 |
|---|---|---|
| `capture.py` | 06:00–20:00，每个整点和半点 | 拍一张粘虫卡，切块推理数出 male SWD，把结果写进 `outbox/`；中午 12:00–12:29 第一张成功的照片额外生成缩略图 |
| `uploader.py` | 开机 2 分钟后，之后每 5 分钟 | 把 `outbox/` 里的记录补上温湿度、电压、剩余空间，上传到 Firebase，再写一次心跳 |

两者只通过 `outbox/` 交换数据。断网时记录留在 `outbox/`，恢复后按时间顺序补传，不会丢，也不会重复。

## 依赖

- `capture.py`：Pi 上已有的 `ultralytics`、`opencv`（`run_code/yolo/yolo_ncnn.py` 用的同一套），以及系统自带的 `rpicam-still`
- `uploader.py`：只要 Python 3.9+ 和 `requests`

## 在 Pi 上安装

以下假设用户是 `paalab`，放在 `~/swd/` 下。

1. **复制代码和模型**

   ```bash
   mkdir -p ~/swd
   # 在 Mac 上：scp -r edge paalab@<pi>:~/swd/
   mkdir -p ~/swd/edge/model
   cp -r ~/Desktop/run_code/yolo/yolo11n_b8/best_ncnn_model ~/swd/edge/model/
   ```

2. **标定对焦**（每台节点装好后做一次，卡和相机的距离变了要重做）

   ```bash
   python3 ~/Desktop/run_code/cam_test/calibration.py --mode normal
   ```

   记下报告里的最佳 `LensPosition`。

3. **填配置**

   ```bash
   cd ~/swd/edge
   cp config.example.ini config.ini
   nano config.ini
   ```

   - `device_id`：设备名，例如 `MSU1`，同时也是网站上的陷阱 ID
   - `[firebase]`：这台节点专用的账号和项目信息（向管理员要）
   - `[camera] mode`：64MP 填 `9248:6944:12:P`；16MP 按相机填
   - `[camera] lens_position`：第 2 步标定的值
   - `[paths] sensor_logs`：温湿度和电压日志所在目录（`YYYY-MM-DD.csv`、`voltage.csv`、`voltage_YYYY-MM.csv`）

4. **先手动跑一次**

   ```bash
   python3 capture.py && ls outbox/
   python3 uploader.py
   ```

5. **启用定时器**

   ```bash
   mkdir -p ~/.config/systemd/user
   cp systemd/* ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now swd-capture.timer swd-uploader.timer
   sudo loginctl enable-linger paalab   # 不登录也让定时器运行
   ```

   如果这台 Pi 还在跑旧的采集服务（`camera.service` 或 `camera64.service`），先停掉，避免两边同时抢相机：
   `sudo systemctl disable --now camera.service`

## 日常检查

```bash
systemctl --user list-timers 'swd-*'
journalctl --user -u swd-capture -n 20
journalctl --user -u swd-uploader -n 50
ls ~/swd/outbox ~/swd/bad
```

- `outbox/` 里一直积着文件：说明上传不出去。看 uploader 日志，`network` 是断网，`auth` 是账号、密码或设备映射有问题。
- `bad/` 里有文件：记录本身有问题，比如 JSON 损坏或设备名不对。日志里会写原因，不会自动重试。
- `sent/` 里按日期保存已上传的记录，30 天后自动删除（`sent_keep_days`）。

## 原图存储

原图存放在 `images/`，不上传。原图总量最多占整块磁盘的 60%（`max_image_pct`）。超过后，每次拍照前会从最旧的开始删，删到 60% 以下为止。

心跳里会上报三项：原图占用百分比、现存最旧原图的时间、最近 24 小时内是否删过原图。技术员在网站的节点健康页面看到"即将开始滚动删除"或"正在滚动删除"时，如果需要原图，请尽快到现场拷贝。

## outbox 记录格式（给改采集脚本的人）

每条记录是一个文件 `outbox/{device_id}_{YYYYMMDD_HHMMSS}.json`。**必须先写成 `.json.tmp`，写完再改名成 `.json`**，上传程序只读 `.json`，并且会跳过修改时间在 60 秒以内的文件。

必填字段：`device_id`、`acquired_at`（带时区偏移的 ISO8601）、`count_male_swd`（非负整数）。字段的完整说明见 `docs/superpowers/specs/2026-10-12-edge-node-design.md` 第 6 节。

## 安全说明（演示级）

- 每台 Pi 用一个 Firebase 账号，数据库规则限定它只能写自己设备名下的数据。
- 缩略图的 Storage 规则目前只要求"已登录、JPEG、小于 500 KB"，任何已登录账号都能写任意设备的缩略图目录。严格版规则后补。
- `config.ini` 和 `state/token.json` 里有密码和 token，不要复制到别处，也不要提交到 git。

## 在 Mac 上开发和测试

```bash
conda activate swd-web
cd edge && pytest -m "not emulator"   # 单元测试；推理一致性测试需要 data/ 下的样例图和模型
cd .. && npm run test:rules           # 数据库和 Storage 规则测试（Firebase Emulator）
bash scripts/e2e.sh                   # 端到端：上传 → 断网 → 恢复
```

连 Emulator 时，`config.ini` 里要设 `emulator_host = 127.0.0.1`、`db_url = https://demo-swd-web-default-rtdb.firebaseio.com`。Emulator 按 `db_url` 主机名的第一段来区分数据库实例。
