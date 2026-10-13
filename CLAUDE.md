SWD（斑翅果蝇，*Drosophila suzukii*）图像监测网络的 Web 平台：田间 Raspberry Pi 边缘推理，只上传计数 JSON 和每天一张中午缩略图；Firebase（Realtime DB + Storage + Auth）存储，Next.js 部署在 Vercel。

**开工前先读 `docs/SPEC.md`**。它是唯一的需求来源，已经按用户的逐条回答修订过；跟最初那份英文需求提示词冲突的地方，以 SPEC 为准。

## 当前状态（2026-10-12）

- 第一期第 1 项完成（分支 `feat/edge-node`）：设计见 `docs/superpowers/specs/2026-10-12-edge-node-design.md`，计划见 `docs/superpowers/plans/2026-10-12-edge-node.md`。
  - `edge/`：Pi 上的采集脚本 `capture.py`（拍照 + 切块推理 + 中午缩略图 + 原图滚动删除）和上传程序 `uploader.py`（SPEC 里叫 `edge-uploader/`，因为也装了采集脚本而改名）。
  - 根目录：`database.rules.json`、`storage.rules`（演示级），用 Firebase Emulator 测试（`npm run test:rules`、`bash scripts/e2e.sh`）。
  - 还没做：真机验证、新建云端 Firebase 项目并部署 Rules、批量创建 14 个设备账号。
- 下一步：第一期第 2 项起（种子数据、网站）。
- 本项目装了 superpowers 插件（project scope），按它的 brainstorming → plan → 实现流程来做。

## 约定

- SPEC 里没写的选型，先问用户再定。
- 安全和权限：先做出来能演示、能截图，严格的安全策略后补。最终目标是正式上线。
- 计数只针对 male SWD；日最大值和中位数按卡片计算，绝不把一天的计数加起来。
- 环境：`conda activate swd-web`（Python 3.12 + Node 22，见 `environment.yml`）。

## 参考资料

- `data/weather/`：用户给的 2026 年 ESP32 温湿度和电压日志样例（还在整理中，不全），不要提交到 git。
- 农场名称和坐标：`~/Work/01_projects/40_swd-doc/paper1/02_dataset/figures/swd_monitoring_sites.geojson`；设备和农场的对照见 SPEC 第 8 节。
- Linux 台式机：`ssh tianqi@192.168.244.241`（旧地址 `10.244.148.105` 经常超时）。SWD 原始数据在 `/media/tianqi/16tb/20_SWD/01_Data/<年份>_SWD_data_RAW`；模型训练项目在 `/mnt/D/01_Projects/01_swd/02_code/pipeline_v2`（只做训练，不提供推理服务）。
- 旧版网站（Next.js 14 + Firebase RTDB）：`~/Work/04_archives/smart-trap-website`，第二期迁移旧数据时参考。
