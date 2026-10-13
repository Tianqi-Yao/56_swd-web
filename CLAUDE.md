SWD（斑翅果蝇，*Drosophila suzukii*）图像监测网络的 Web 平台：田间 Raspberry Pi 边缘推理，只上传计数 JSON 和每天一张中午缩略图；Firebase（Realtime DB + Storage + Auth）存储，Next.js 部署在 Vercel。

**开工前先读 `docs/SPEC.md`**。它是唯一的需求来源，已经按用户的逐条回答修订过；跟最初那份英文需求提示词冲突的地方，以 SPEC 为准。

## 当前状态（2026-10-12）

- 需求已确认（SPEC v0.2），还没写代码，也还没 `git init`。
- 下一步：第一期，先写 `edge-uploader/`（复制到 Pi 上直接运行，只依赖 Python 和 `requests`，越简单越好），再定 Firebase 结构和 Rules，然后做网站。
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
