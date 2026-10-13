# SWD 监测网络 Web 平台：修订版需求

> 版本 v0.2，2026-10-12（v0.2：第 9 节问题已全部确认）。以最初的需求提示词为底稿，按用户逐条回答和实际数据核查修订。以后开发以本文为准；跟原提示词冲突的地方，以本文为准。

## 0. 跟原需求相比改了什么

| 原需求 | 修订后 | 原因 |
|---|---|---|
| 两种回传模式：实时上传 + 现场拷卡批量上传原图 | **只有实时数据流**。节点在田间算好计数，只上传 JSON 数据和每天一张中午缩略图 | 边缘计算的意义就是不传大量图片、省流量 |
| 后端批量推理 | **网站不跑模型**。YOLO 在 Raspberry Pi 上跑，网站只接收结果 | 同上 |
| 每个周期多张对焦图 | **正式部署每 30 分钟只拍一张**、只算一个计数 | 多焦距只是采集训练数据阶段的做法 |
| Postgres + 对象存储 + Docker | **Firebase Realtime Database + Firebase Storage**，前端部署在 **Vercel** | 数据结构简单，优先简单 |
| 计数对象未明确 | **只计数 male SWD** | 用户确认 |
| 首期就做完整的安全和权限 | 账号登录和角色**先做出来、能截图演示**，严格的安全策略后补 | 先服务论文汇报，后期再正式上线 |

## 1. 系统背景（不变的事实）

- 节点：每季 10–14 个，2026 年是 14 个（见第 8 节）。由太阳能供电的 Raspberry Pi 4B，加 16MP 或 64MP 相机，拍约 25 cm 外的红色或黄色粘虫卡，再配一个 SHT35 温湿度传感器。
  - **16MP 节点**：由 Pi 直接读 SHT35，写入 `environment_log.csv`（`Timestamp,Temperature (C),Humidity (%)`，每分钟一条）。
  - **64MP 节点**：由 ESP32-C3 读 SHT35 并记录电池电压，写到 ESP32 自己的 SD 卡上，包括 `logs/YYYY-MM-DD.csv`（`date,time,temp,rh`，每分钟一条）、`logs/voltage.csv`（`date,time,V,percent`，每 10 分钟一条）和 `systemLog_*.txt`。ESP32 还负责定时开关机，例如 21:00 关机。
  - **当前假设**：温湿度和电压已经在 Pi 上可以读到。ESP32 和 Pi 之间怎么传数据，以后再升级；上传程序只读 Pi 本地的数据。
- 正式运行时：白天每 30 分钟拍一张，Pi 上的 YOLO11n（NCNN）输出这张图里 **male SWD** 的数量。
- **时间戳**：数据一律按**采集时间**（设备时钟，美国中部时间）建索引，不用上传时间。
- **计数不等于捕获量**：虫子几小时内就可能挣脱粘胶，所以每张图的计数只表示"这一刻卡上有几只"。展示时，按卡片给出**日最大值**（主指标）和**日中位数**；换卡当天要切开分段；**绝不**把一天的计数加起来。

## 2. 数据流和架构

```
[田间节点 Pi]
  拍照 → YOLO 推理 → 写一条 JSON 到 outbox/
  读 SHT35 / 电压 / 磁盘空间 → 写进同一条 JSON
  中午 12:00 那一次额外生成缩略图
        │  4G/LTE，HTTPS（Firebase REST）
        ▼
[Firebase]
  Realtime Database：节点读数、心跳、卡片、维护记录、人工计数、告警、用户
  Storage：每天一张缩略图
  Auth：邮箱登录
        │
        ▼
[Next.js on Vercel]
  页面：总览 / 陷阱时间序列 / 图像 / 告警 / 节点健康 / 维护日志 / 人工计数 / 导出
  Vercel Cron：定时算日统计和告警，发送邮件和短信
```

论文分层对应：
- **L3 采集与存储**：节点上传程序、Firebase RTDB 和 Storage
- **L4 分析**：日最大值/中位数、onset 规则、健康指标、告警（Vercel Cron 和前端计算）
- **L5 应用**：Next.js 页面

## 3. 节点上传程序（`edge-uploader/`）

这是一个独立文件夹，**直接复制到 Pi 上运行**，越简单越好。

- **跟推理代码解耦**：推理脚本每个周期往 `outbox/` 写一个 JSON 文件，上传程序负责把它推到 Firebase。上传成功的移到 `sent/`；失败的留在原处，下次重试，所以断网时数据也不会丢。
- **每条记录的字段（草案）**：
  ```json
  {
    "device_id": "MSU1",
    "acquired_at": "2026-07-15T12:00:03-05:00",
    "count_male_swd": 3,
    "boxes": [[x1, y1, x2, y2, conf], ...],   // 只有中午那条带
    "model_version": "yolo11n-ncnn-v5",
    "infer_ms": 840,
    "temp_c": 28.4, "rh": 61.2,
    "battery_v": 12.55,
    "free_gb": 42.1,
    "thumb": "20260715_120003.jpg"
  }
  ```
  `temp_c`/`rh`/`battery_v` 拿不到时填 `null`。只有中午那条带 `thumb` 和 `boxes`，其他记录只传计数，节省流量。
- **心跳**：每次上传同时更新 `heartbeat/{device_id}`，写入设备时间和服务器时间（`{".sv": "timestamp"}`）。
- **认证**：每台 Pi 用一个 Firebase Auth 账号（邮箱+密码），Database Rules 规定它只能写自己 `device_id` 下的数据。
- **依赖**：只用 Python 3 + `requests`，不装 Firebase SDK。
- **运行方式**：systemd timer，每 5 分钟扫描一次 `outbox/`。

## 4. Realtime Database 结构（草案）

```
/farms/{farmId}            name, city, region, crop, lat, lon
/traps/{trapId}            farmId, deviceId, cameraMp, lat, lon, installed, removed
/readings/{trapId}/{YYYY-MM-DD}/{HHMMSS}   上传程序写入的记录（见第 3 节）
/thumbs/{trapId}/{YYYY-MM-DD}              storagePath, acquiredAt, boxes
/heartbeat/{trapId}        deviceTime, serverTime, lastReadingAt
/cards/{trapId}/{cardId}   placedAt, removedAt, colour
/daily/{trapId}/{YYYY-MM-DD}  cardId, max, median, n, clockFaultN   ← Cron 计算
/visits/{visitId}          trapId, date, technician, actions[], captureResumed
/manualCounts/{id}         cardId, trapId, date, count, sex, photoPath
/alerts/{alertId}          trapId, type, rule, value, raisedAt, ackAt, snoozeUntil, resolvedAt
/users/{uid}               email, role, farmIds[]
/settings/thresholds/{trapId|farmId}
```

按日期分节点，前端按日期范围读取时不用拉全量数据。按 50 个陷阱 × 每天约 32 条算，数据量很小。

## 5. 规则

- **时钟故障**：设备时间和服务器接收时间相差超过阈值（默认 15 分钟，扣除离线补传的情况），或者设备时间落在季节范围之外，就把这条记录标记为 `clockFault`，同时发出"设备时钟异常"告警。**画图时改用服务器接收时间**，图上用特殊标记注明"时间已按接收时间校正"，原始设备时间保留不改。示例：ElderFarm2 2025 年凌晨有大量图片，是 RTC 故障；HSH1 的 `voltage.csv` 里出现 `72:41:18` 这种时间。
- **日统计**：按卡片、按日期计算 max、median 和 n（白天的有效记录数）。换卡日拆成两段。
- **onset**：一张卡的日中位数，比这张卡此前的最大日中位数高出 ≥ 5，并且第二天还保持在那个水平之上，就判定为 onset。**每张新卡的前 2 天只积累基线，不判定。**展示时一律标注"监测提示，不是喷药阈值"。
- **节点健康告警**：
  - 白天（06:00–19:59）超过 N 小时没有新记录，默认 3 h
  - `free_gb` < 15
  - `battery_v` < 12.0
  - 时钟故障
  - 温湿度连续缺失，即 SHT35 读不到数据（ESP32 日志里的 `Failed to read SHT35 data (NaN)`）
- **诚实展示**：每个数字都标明来源（自动计数，基于单张图；或人工计数，基于整张卡）和时间口径。缺失数据就显示为缺失。计数统一标为"male SWD（模型计数）"。

## 6. 功能和分期

**第一期：能跑通，能截图**
1. Firebase 结构、Rules，以及 `edge-uploader/`
2. 种子数据：用真实的 8 个农场和 14 个设备名，配上**合成**读数（明确标注 synthetic），覆盖 30 天
3. 农场总览：地图，用颜色**加形状**区分状态（正常/关注/高/离线）；KPI 卡片；每个陷阱的"最后收到数据"时间
4. 陷阱时间序列：日最大值和中位数、换卡竖虚线、人工计数单独标记、缺失处断开不连线、可选日期范围、多陷阱对比
5. 图像查看：每天的中午缩略图，叠加检测框，可缩放
6. 节点健康：设备 × 日期热力图（按每天的有效周期数）、覆盖率、断线列表、电压和剩余空间曲线、最新温湿度
7. 维护日志：现场维护记录，换卡记录用来驱动时间序列里的换卡标记
8. 邮箱登录，三种角色（种植户、推广专家/研究人员、技术员/管理员）

**第二期**
告警（站内列表、邮件、短信；确认、暂缓、关闭；历史记录）、人工计数表单（手机可用、离线可存）、CSV 导入纸质记录、小气候面板、导出和 REST API、旧 Firebase 数据迁移。

**第三期（先预留设计，不做）**
7 天爆发风险预测（标注"实验性：模型预测"）、节点端推理状态、远程命令。

**非功能要求**：使用 Okabe-Ito 色盲友好配色，不用红绿作为唯一的状态区分，满足 WCAG AA 对比度，桌面和手机都能用。交付时提供 1440×900 和手机宽度两种截图。

## 7. 技术栈

Next.js（App Router）+ TypeScript + Tailwind + shadcn/ui；Firebase JS SDK（Auth、RTDB、Storage）；图表待定（倾向 Recharts 或 ECharts）；地图用 Leaflet + OpenStreetMap，不用 key；测试用 Vitest 做规则单元测试，Playwright 做端到端测试；部署在 Vercel。开发环境：conda `swd-web`（Python 3.12 + Node 22）。

## 8. 农场和设备对照表（草案，需要用户核对）

农场名称和坐标来自 `40_swd-doc/paper1/02_dataset/figures/swd_monitoring_sites.geojson`。设备缩写和农场的对应关系是**我推断的**。16TB 盘上的 `00_docs/03_site_glossary.md` 目前还没填。

| # | 农场 | 城市 | 纬度, 经度 | 设备缩写（各年份） | 推断依据 |
|---|---|---|---|---|---|
| 1 | MU South Farm | Columbia | 38.91988, −92.27963 | southfarm1/2 | 名字一致 |
| 2 | Elderberry Farm（机场附近） | Ashland | 38.84566, −92.25089 | air1–4 | 用户确认是"airport 附近的农场"；离 Columbia Regional Airport 约 4 km，推断就是 Elderberry Farm |
| 3 | Lloyd's Family Farm | Ashland | 38.77604, −92.23627 | lloyd1/2 | 名字一致 |
| 4 | LU G. W. Carver Farm | Jefferson City | 38.52975, −92.13816 | jeff | 名字一致 |
| 5 | MSU Fruit Experiment Station | Mountain Grove | 37.15552, −92.26584 | MSU1/2、MGrove1/2、ms1/ms2 | 用户确认 ms2 = MGrove2 |
| 6 | MU SWREEC | Mt Vernon | 37.07489, −93.87938 | SW1/2、SWREEC1/2、sw1/sw2 | 名字一致 |
| 7 | Elder Farm | Hoberg | 37.04365, −93.83356 | ElderFarm1–3、elder1 | 名字一致 |
| 8 | HSH Farm | Carl Junction | 37.24601, −94.55482 | HSH1/2 | 名字一致 |

paper1 里 2024 年数据表把 `ms2` 写成 "Mt Vernon"，是写错了，应为 Mountain Grove（MGrove2）。`DAREC` 指什么还不清楚，网站暂时用不到。

## 9. 已确认问题（2026-10-12）

1. 64MP 节点的温湿度和电压：先假设在 Pi 上已经能读到，ESP32 和 Pi 的数据交互以后再升级。
2. 时钟故障：选方案 (b)，画图时改用服务器接收时间，并保留标记。
3. `air1–4` 是机场附近的农场；`ms2` 是 MGrove2。
4. 检测框只在中午那条记录里传。
