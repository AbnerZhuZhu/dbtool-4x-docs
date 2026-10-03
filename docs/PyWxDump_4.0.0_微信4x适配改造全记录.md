# PyWxDump 4.0.0 微信 4.x 适配改造全记录

> **项目性质**：在 `PyWxDump 3.1.46` 源码基础上完成微信 4.x（4.1.15.13）适配改造，交付版本 `4.0.0`
> **记录基线**：2026-10-04（UTC+8）　**最近更新**：2026-10-04（遗留问题处理 + 补片库清理，见文末「本次处理记录」）
> **数据口径说明**：本报告只收录已确认的事实与实测数字；无法取证处显式标注「未确认 / 待确认」，不做推测。

---

## 1. 项目背景与初始状态

### 1.1 环境与路径基线

| 项目 | 值 |
| --- | --- |
| 操作系统 / 权限 | Windows，管理员 PowerShell |
| Python | 3.12（实测 3.12.8，`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`） |
| 微信版本 | **4.1.15.13**（`D:\Program Files\Tencent\Weixin\Weixin.exe`） |
| 改造宿主（唯一改动目标） | `D:\PyWxDump_Source\`（原版 PyWxDump 3.1.46） |
| 工作 / 配置目录 | `C:\Users\Administrator\wxdump_work\`（`conf_auto.json`、`decrypted_wx4\<账号>\`） |
| 桌面（脚本与文档输出） | `E:\Users\Administrator\Desktop\` |
| 备份目录 | `E:\PyWxDump_4x_Backup\`（本次同步后：**125 文件 / 565,468,042 B**；其中不含 `_keep`、`wxdump_work` 的源码镜像为 86 文件 / 17,479,775 B） |
| 测试账号 | 柳岸 `<本人wxid>`（数据目录后缀 `<目录后缀>`）、聚客猫 `<账号目录名>` |
| 密钥文件 | `C:\Users\Administrator\.wechat-cli\all_keys.json`（当前 45 条）、`extra_mem_keys.json`（内存扫描发现、未匹配到当前可校验库，累计 15 条） |
| 第三方依赖 | `pycryptodome`、`zstandard`、`pymem`、`psutil`、`sqlite3`（标准库） |

### 1.2 初始状态与触发症状

- 原版 PyWxDump 3.1.46 只认微信 **3.x** 表结构：`MSG` / `Contact` / `Session` / `ContactHeadImgUrl` / `ChatRoom` / `ChatRoomInfo` / `ChatInfo` / `ContactLabel`。
- 在 4.1.15.13 上执行 `wxdump info` 抛出 **`ObjectNotFound`**：进程名已由 `WeChat.exe` 变为 `Weixin.exe`，且旧静态偏移地图 `WX_OFFS.json` 完全失效。
- 4.x 解密库表结构与 3.x 完全不同：
  - 消息主表为 **`Msg_<hash>`**（而非 `MSG`）；
  - 联系人表为 **`contact`**、会话表为 **`session`**（而非 `Contact` / `Session`）；
  - 每个消息库内有 **`Name2Id`** 表，用于把 `real_sender_id` 映射为 `wxid`。
- 材料证据：源码副本落地时间戳为 **2026-10-03 19:59:21**（`WX_OFFS.json` / `__init__.py` 等同批文件）。

### 1.3 贯穿全程的硬性约束

1. 不修改微信文件、不做 DLL 注入、不做 Hook、不使用 Frida；
2. 不更换工具（明确弃用 `wechatauto-replica`）；
3. 不重写整个项目，只做定向改造；
4. 逐步实施、一步一验收、关键分支先确认共识；
5. 不擅自扩展改动范围，点名范围之外不动。

---

## 2. 核心问题与挑战

| # | 挑战 | 具体表现 | 影响 |
| --- | --- | --- | --- |
| 1 | 进程名变更 | `WeChat.exe` → `Weixin.exe` | 进程查找直接失败，`info` / `bias` 不可用 |
| 2 | 静态偏移失效 | 旧 `WX_OFFS.json`（5 个偏移量）对 4.x 无效 | 内存读密钥链路整体作废 |
| 3 | 密钥不再明文驻留 | 4.1.15.13 内存中已无 `x'<hex>'` 形态明文密钥（实测扫描 5 个 `Weixin.exe` 进程、733 个区段、223 MB，候选 0 命中） | 「扫明文密钥串」方案失效，必须退守 |
| 4 | 密钥结构体 + 混淆 | 密钥材料位于 `com.Tencent.WCDB.Config.Cipher`，可能带 XOR 混淆 | 需要定位结构体 + 反推掩码 + 校验 |
| 5 | 表结构 / 字段全变 | `Msg_<hash>`、`contact`、`Name2Id`；4.x 分表 17 列 vs 3.x `MSG` 26 列 | 所有 SQL 与列映射需重写 |
| 6 | 消息体压缩 | 部分消息 `WCDB_CT_message_content == 4`（zstd 压缩） | 正文空白 |
| 7 | 消息 XML 结构变更 | 图片 / 链接 / 文件 / 位置消息 XML 与 3.x 不同 | 非文本消息显示异常 |
| 8 | 媒体外置 | 图片、语音、视频存放在 `media_*.db`，完整解密涉硬链接与额外逻辑 | 不追求预览，但必须保证类型标签正确 |
| 9 | 多账号并存 | 同机 14 个账号目录（`D:\xwechat_files` 下），密钥分属不同账号 | 账号定位、密钥归属、同名库互相覆盖 |
| 10 | UI/前端基于 3.x 假设 | `init_key` 等端点等待手动填库路径 | UI 初始化失败（错误码 2001） |

---

## 3. 改造方案与关键技术决策

| 决策项 | 最终方案 | 理由 / 依据 |
| --- | --- | --- |
| 取密钥路线 | **两条路并存**：默认 `-dd` 读解密库，`--mem` / `--scan_mem` 走只读内存扫描 | 4.x 库已可解密，两条路线互为兜底 |
| 内存扫描目标 | 已知明文（`x'<64hex key><32hex salt>'` 等 4 种形态）+ **周期性 XOR 掩码反推** + 第 1 页 **HMAC-SHA512** 校验 | 逆 `Config.Cipher` 的 C++ 结构过于脆弱，易随版本失效 |
| 注入类兜底 | **明确拒绝** DLL 注入 + Hook | 风控封号与杀软拦截风险过高 |
| 扫不到时行为 | 明确报错并提示「重启微信、登录后立即再扫」 | 不做危险兜底，不静默失败 |
| 权限要求 | 只需 `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ`，不需要管理员权限 | 只读内存，不改动进程 |
| 掩码反推 | 以磁盘真库 salt 作参照集（实测 **273 个**）假设 + 指令特征定位掩码，再对每个候选密钥做第 1 页 HMAC 校验 | 用真实库验证，避免误报 |
| 密钥落盘 | 校验通过的密钥**回写** `all_keys.json`（写前备份）；未匹配串另存 `extra_mem_keys.json`；跨账号不串写 | 密钥可复用，避免每次重扫 |
| 3.x 兼容 | `is_wx4` / `tables_exist("MSG")` 分流，命中 `MSG` 必走老逻辑 | **3.x 零回归**是硬约束 |
| 动态表名 | `Msg_ + md5(会话wxid)` 动态拼表名 | 4.x 按会话散列分表 |
| IsSender 判定 | 经 `Name2Id` 把 `real_sender_id` 反解为 `wxid`，再与当前账号 wxid 比对 | 4.x 不能再用 `real_sender_id == 1` 判定 |
| 消息体处理 | zstd 解压（`zstandard`）+ 4.x XML 适配 + 媒体类型标签（`[图片]` / `[语音时长：xx秒]` / `[视频]`） | 保证正文与类型可读 |
| UI/API 启动 | **启动即全自动**：读密钥 → 账号定位 → 增量解密 → 加载会话，无需手填任何路径 | 去掉 3.x 时代的手工初始化 |
| 热力图渲染 | 日历范围 = **后端跨度 ∪ 数据键（并集）**；一年一块横向堆叠；容器双向滚动；「缩放」下拉 + Ctrl+滚轮；跨度 ≥10 年自动紧凑 | `dataZoom` 对 calendar 坐标系无效（实测确认），改用等价方案 |
| 路径解析（本轮新增） | 4.x 数据根目录、解密库目录、密钥文件**全部改为动态定位**（注册表 → 各盘 → 用户目录 → 回退 `D:\xwechat_files`），支持 `PYWXDUMP_WX4_ROOT` 覆盖 | 换机器/换盘/清理历史目录后不再报错 |
| 大跨度防卡顿 | 后端按天 `GROUP BY` 聚合后再下发；画布随缩放联动 | 59 万条 → 约 2 389 个日期键，浏览器不接触原始行 |

---

## 4. 分阶段实施过程

> **日期口径（已统一）**：全部改造与修复集中在 **2026-10-03 19:59 ~ 2026-10-04**；下表日期均取自文件 mtime 取证，可复核。`batch_decrypt.py` 本身已在 P9 清理阶段删除，其自身时间戳不可取证 → 该文件标注「未确认」。

| 阶段 | 日期（取证来源） | 主要工作 | 解决的问题 | 关键留档 |
| --- | --- | --- | --- | --- |
| **P0 数据解密与原始导出** | 2026-10-03 21:35–22:18（桌面 `make_merge.py` / `fix_and_merge.py` / `merge_db.py` / `view_all_chat.py`、`我的全部聊天记录.txt`） | `batch_decrypt.py`（IV 取 `data[16:32]`、密文取 `data[32:-64]`，时间未确认）；`view_all_chat.py` 汇总 `message_*` 的 `MSG` 表导出 txt；合并 `merge_all.db`；把 4.x 桥接成 3.x 结构 | 先把数据「拿到手」，为后续读库铺路 | `我的全部聊天记录.txt` **309,488,450 B**（导出成功） |
| **P1 数据库连接层改造（第一步）** | 2026-10-03 23:28（`wx4_step1_verify.py`） | `db/dbbase.py` 表识别：找不到 `MSG` 时改找 `Msg_` 前缀表；`db/dbMSG.py` 动态表名；`IsSender` 改用 `Name2Id` 反查 | 从 3.x 死表名解放，消息读取跑通 | — |
| **P2 联系人 / UI / info / bias 适配（第二步）** | 2026-10-04 00:20–00:21（`wx4_step2_verify.py` / `wx4_step2_修改说明.md`） | `contact_decrypted.db → Contact`、`session_decrypted.db → Session` 映射；`get_user()` 可返回联系人；`info`/`bias` 跳过内存扫描改读本地密钥文件；端口占用处理 | UI「微信信息 / 联系人画像」有数据；`info` 不再报 `WeChat No Run` | 实测：联系人 **9 715 人**、会话 **549 个**、群 **197 个（加载 2.58 s）**（该组为第二步验收账号；验收日志中聚客猫为 125 人 / 19 会话） |
| **P3 zstd + 4.x XML + 媒体标签（第三步）** | 2026-10-04 00:42–00:45（`wx4_step3_verify.py` / `wx4_step3_修改说明.md`） | 检测 `WCDB_CT_message_content == 4` → `zstandard` 解压后回填正文；适配 4.x XML 提取标题/描述/链接；媒体消息统一类型标签 | 正文空白与媒体消息显示异常 | — |
| **P4 内存取密钥探索** | 2026-10-04 01:06–01:15（`wx4_mem_step1/2_修改说明.md`） | 进程识别同时匹配 `WeChat.exe` + `Weixin.exe`；废弃 `WX_OFFS.json`；试行明文密钥串扫描 → **实测 0 命中**（5 进程 / 733 区段 / 223 MB） | 确认 4.1.15.13 不再驻留明文密钥 | 正式退守 B 计划 |
| **P5 退守本地密钥文件** | 2026-10-04 01:15 前后（同上说明文档） | `wx_info.py` 去掉 `WeChatWin.dll` 硬前提；`--key_file` 读 `all_keys.json`；`info` 输出对齐 3.x 格式；`bias` 输出 `{数据库路径: enc_key}` | 无内存密钥也能输出账号与密钥 | `info`/`bias` 实测通过 |
| **P6 `--key_file` 接入 ui / api** | 2026-10-04 01:29–01:30（`wx4_cipher_scan_probe.py` / `wx4_ui_autoinit_修改说明.md`） | UI 启动接受 `--key_file`；默认读 `.wechat-cli\all_keys.json`；自动扫描 4.x 原始库目录并增量解密；前端不再要求填库路径 | 「启动即全自动」 | 建议用 `.bat` 固定 `PYTHONPATH`（你已自行采用） |
| **P7 `Config.Cipher` 动态扫描跑通** | 2026-10-04 01:38（`wx4_mem_scan_修改说明.md`） | 只读扫描 `Weixin.exe` 内存；真库 salt 假设反推 XOR 掩码；HMAC 校验；`info` 打印掩码与逐库密钥 | 回到「内存取密钥」正路，且全程只读 | 历史轮次实测 **20 把密钥** |
| **P8 收尾** | 2026-10-04 02:12–02:23（`wx4_mem_keys_修改说明.md`、`wx4_key_store_verify.py` / `_probe.py` / `_修改说明.md`） | ① 内存密钥与文件不一致时回写 `all_keys.json`；② 未匹配串存 `extra_mem_keys.json`；③ `--scan_mem` 长期保留为 4.x 默认 | 密钥不丢、可复用 | 收尾轮密钥文件 **46 条**（02:58 日志），当前 **45 条**（条数变化的直接原因未确认） |
| **P9 4.0.0 正式发布** | 2026-10-04 02:36–03:19（`v01~v03` 日志、`wx4_release_verify.py`、`_验收报告_4.0.0_*.log`、`pyproject.toml`、`_备份校验报告_20261004.txt`） | ① 备份整项目；② 清理中间产物与临时脚本；③ 版本号 `3.1.46 → 4.0.0`；④ `wxdump info` / `bias` 免加 `--mode 4x --scan_mem` 自动走 4.x 内存扫描 | 从「可跑」到「可交付」 | `wxdump info` 首行 `[*] PyWxDump v4.0.0`；验收 **38 项通过 / 0 项失败** |
| **P10 UI / CLI Bug 修复轮** | 2026-10-04 03:12–03:59（`_验收报告_4.0.0_wx_path_dbshow修复后_20261004.log`、`wx4_ui颜色块与联系人画像_说明.md`） | 修 `wx_path`、`dbshow --db_path`、UI `init_key` 2001、图例色块、初始化倒计时、联系人画像性别统计定位 | 详见第 5 章 | 多份 `*_修复说明.md` |
| **P11 热力图跨度自适应与缩放** | 2026-10-04 05:50–06:12（`wx4_热力图2018缺失_修复说明.md`、`wx4_热力图跨度自适应与缩放_修复说明.md`） | 后端新增 `/api/rs/date_range`；`/date_count` 增 `extra`；前端范围取并集、按年分块、双向滚动、缩放下拉 + Ctrl+滚轮、自动紧凑 | 「热力图只显示某一段／无法滚动」彻底根治 | 多场景实测（见 6.3） |
| **P12 `info` 账号信息修复** | 2026-10-04 06:28（`wx4_info联系人信息失败_修复说明.md`） | `_wx4_guess_decrypted_dir()` 识别 `wxdump_work\decrypted_wx4\<账号>`；`contact` 缺失时判空降级；删除会「杀掉密钥输出」的提前 `return []`；有密钥无库时按需解密最小集 | `nickname: None` / `account: None` 与 `text_factory` 报错 | 实测 `nickname: 柳岸` / `account: AbnerUP` |
| **P13 遗留问题处理（本次）** | 2026-10-04 06:38 之后（本报告文件 mtime 06:38:51 为上一版） | 备份同步 + SHA256 逐文件校验、路径动态化改造、补片库与额外密钥取证判定 | 见第 8 章逐条处理结果与文末「本次处理记录」 | `p120`~`p124` 脚本与日志（`%TEMP%`） |

---

## 5. 遇到的 Bug 与修复记录

| # | 现象 | 根因 | 修复 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | `wxdump info` 报 `ObjectNotFound` | 进程名 `WeChat.exe` → `Weixin.exe`；`WX_OFFS.json` 失效 | 进程名单统一为 `WeChat.exe + Weixin.exe`，按代次分流；废弃静态偏移 | 已修复（实测通过） |
| 2 | 内存明文密钥扫描 0 命中 | 4.1.15.13 不再明文驻留密钥 | 退守本地密钥文件 → 再升级为掩码反推 + HMAC 校验 | 已修复（实测 13 把） |
| 3 | 本方消息被显示为「对方发的」 | 4.x 不能用 `real_sender_id == 1` 判定；需 `Name2Id` 映射 | 经 `Name2Id` 反解 `real_sender_id → wxid` 后与当前账号比对 | 已修复（微信原图对照） |
| 4 | 部分消息正文空白 | `WCDB_CT_message_content == 4`（zstd 压缩） | 引入 `zstandard` 解压后回填 `StrContent` | 已修复 |
| 5 | 图片/链接/文件/位置显示异常 | 4.x XML 结构与 3.x 不同 | 适配 4.x XML 提取标题/描述；媒体统一类型标签 | 已修复（媒体仅保证类型标签，未做预览） |
| 6 | UI「微信信息 / 联系人画像」No Data、0 个联系人 | 未接联系人库 | `contact_decrypted.db → Contact`、`session_decrypted.db → Session` 映射 | 已修复 |
| 7 | `wxdump info` 报 `WeChat No Run` | 3.x 的内存扫描前提 | 4.x 改走读库，不报错退出 | 已修复 |
| 8 | `wxdump bias` 报缺少参数 | 参数与语义仍按 3.x | `bias` 语义变更为 `{数据库路径: enc_key}`，参数放宽 | 已修复 |
| 9 | `wxdump api` 报 `Port 5000 is already in use` | 默认端口被占 | 支持 `-p/--port` 指定端口（验证实例用 17950 / 17957）；启动前占用检测的实现细节**待补充** | 已缓解 |
| 10 | `wxdump ui` 初始化弹 `{"code":2001,"body":"未获取到数据库路径"}` | 前端仍走 3.x 的 `init_key`，等待手动填路径 | `init_key` 未收到明确 `db_path` 时自动调用 4.x 自动定位/解密逻辑 | 已修复（UI 可正常查看） |
| 11 | 顶部颜色图例显示为带 ✕ 的空白框 | 图例色块渲染/资源问题 | 修正静态资源与渲染 | 已修复（实测 7 个实色块） |
| 12 | 初始化页倒计时卡在「5 秒」 | 前端原为写死文案，无真实计时器 | 改为 1 秒步进的真定时器 | 已修复（未单独复测） |
| 13 | 联系人画像男女统计恒为 0 | **4.x `contact` 表无性别字段** | 无法修复 | **确认不可恢复** |
| 14 | 热力图看不到 2018 年 | 排查结论：前端无硬编码年份（全 bundle 无引号年份字面量、无 `minDate/maxDate`）、后端 SQL 无 2019 截断；**根因偏向数据侧缺分片** | P11 进一步改为「跨度并集 + 自适应」，对两种成因均成立 | 已修复 |
| 15 | 热力图只显示某一段、无法滚动/缩放 | 范围只看数据键首尾 + 列宽/高度写死 | 并集范围 + 按年分块 + 容器 `overflow:auto` + 缩放下拉 + Ctrl+滚轮 + 自动紧凑 | 已修复（多场景实测） |
| 16 | `wxdump wx_path` 报 `TypeError: path should be string... not NoneType` | `get_wx_db` 拿到 `None` 路径未判空 | 自动定位微信数据根目录 + 默认值兜底 | 已修复（后续未再报错，未单独复测） |
| 17 | `wxdump dbshow --db_path "D:\merged.db"` 报 `unrecognized arguments` | 参数名不匹配 | 兼容 `--db_path`（原参数为 `-p/--db_path`，以 `dbshow -h` 为准） | 已修复 |
| 18 | `pip install -e .` 报「不是 Python 项目」 | 清理时误删打包配置 | 补最小可用 `pyproject.toml`（项目名 `pywxdump`、版本 `4.0.0`、入口 `wxdump = pywxdump.cli:console_run`） | 已修复（文件 mtime 03:17） |
| 19 | `wxdump info` 报 `AttributeError: 'NoneType' object has no attribute 'text_factory'`，`nickname/account` 为 None | ① 旧解密目录已被清理；② `_wx4_guess_decrypted_dir()` 只认 5 个老路径 → 返回 `None`（真正的库在 `wxdump_work\decrypted_wx4\<账号>\`）；③ `contact_db` 为 None 时仍执行 `con.text_factory`；④ 「目录在但缺 contact」时旧代码 `return []` 会连密钥都打不出来 | ① 猜目录新增 `wxdump_work\decrypted_wx4\<当前账号>` + `wx4_autodecrypt`；② contact 缺失时判空降级；③ 删除提前 `return []`；④ 有密钥无库时按需解密最小集 | 已修复（本轮复测仍为 `柳岸` / `AbnerUP`） |
| 20 | **（本轮发现并修复）** 多处以 `D:\decrypted_wx_db`、`D:\xwechat_files\<账号目录名>` 等**写死路径**做默认值/自测路径 | 历史脚本遗留的固定路径，换盘或清理后会失效 | 7 个文件 10 处改为动态定位（含 `PYWXDUMP_WX4_ROOT` 覆盖），自测路径改为按目录扫描 | 已修复（见文末「本次处理记录」） |

---

## 6. 验收结果

### 6.0 38 项发布验收（本次处理补证）

发布流程的 38 项验收**有完整日志留档**（此前版本标注「未留存」，本次处理时已在备份目录定位到）：

| 日志文件 | 大小 / 时间 | 结论 |
| --- | --- | --- |
| `E:\PyWxDump_4x_Backup\_验收报告_4.0.0_清理前_20261004.log` | 7,826 B / 2026-10-04 02:57:27 | **总判定：38 项通过 / 0 项失败；全部通过 ✅ —— 可用于发布** |
| `_验收报告_4.0.0_清理后_20261004.log` | 7,731 B / 02:58:47 | 清理后复跑，同样通过 |
| `_验收报告_4.0.0_wx_path_dbshow修复后_20261004.log` | 1,323 B / 03:12:54 | 两个 CLI Bug 修复后复验通过 |

清单分 5 组（按日志逐条计数）：① 版本号 9 项、② 默认参数 10 项、③ 密钥落盘 7 项、④ 服务能起 9 项、⑤ 3.x 硬约束 3 项 —— 合计 **38 项，0 失败**。
其中值得记下的关键判定：`wxdump -h` 显示 `PyWxDump v4.0.0` 且明示「支持微信 4.x 内存取密钥」「只读（不注入 / 不 Hook）」；零参数 `info` 自动走 4.x 内存取密钥；密钥文件 46/46 条都能对到某账号真库（key 与 salt 同源）；服务启动 3.1 s、`/api/rs/is_init=True`；3.x 形态库 `is_wx4=False` 且 `merge_all.db` 的 `MSG` 表 **594,764 行 / 1524 会话** 仍可正常读取。

> 说明：颜色块修复（P10 后半）之后，用户明确指示「38 项验收不用再重跑了，跳过它，直接收尾」，故**未再重跑**；上表为跳跑前已有的验收产物。

### 6.1 CLI 与密钥（柳岸，本轮路径改造后复测）

| 验收项 | 命令 | 实测结果 |
| --- | --- | --- |
| 默认 info（含内存取密钥） | `python -m pywxdump info --mem_budget 25` | **13 把密钥**；`key_source: memory`；`nickname: 柳岸`；`account: AbnerUP`；`version: 4.1.15.13`；无 Traceback |
| 内存扫描过程 | 同上 | 273 个库第 1 页可读；pid=25060 扫 949 区段 / 294.6 MB / 10.7 s → **校验通过 13 把**；其余 4 个 pid 各 0 把 |
| 掩码 | 同上 | `450048844c2448488944254048584c24d2c7442458020000004889442450488b`（索引方式：明文 = 内存字节 ^ 掩码[绝对地址 % 32]） |
| 密钥落盘 | 同上 | `all_keys.json` 与内存一致（无需更新）；`extra_mem_keys.json` 累计 15 条、本次新增 0 |
| 未匹配串 | 同上 | 6 个串在 273 个可校验库里找不到对应 salt（其它账号 / 已轮换） |
| 降级 info（坏目录） | `info --no_scan_mem -dd <无 contact 的目录>` | exit 0；仅两条友好警告；无 `AttributeError`、无 Traceback |
| 语法与静态校验 | `py_compile` / `node --check` | 全部通过 |

### 6.2 数据与会话

| 指标 | 值 |
| --- | --- |
| 联系人 / 会话 / 群 | **9 715 人**、**549 个会话**、**197 个群**（P2 验收账号；对比验收日志中聚客猫：125 人 / 19 会话） |
| `contact_decrypted.db` | 10,212 KB；`contact` 表 **9 715 行** |
| 消息跨度（柳岸 4.x 库） | `2018-09-12 ~ 2026-10-04`；**2 389 个日期键**；非群聊 **270,647 条** |
| 逐年条数 | 2018:2604 / 2019:7272 / 2020:6233 / 2021:10933 / 2022:29980 / 2023:19901 / 2024:38440 / 2025:103674 / 2026:51610 |
| `/api/rs/date_range` | 0.65 s |
| `/api/rs/date_count` | 0.47 s |
| 早期 TXT 导出产物 | `我的全部聊天记录.txt` = **309,488,450 B**（2026-10-03 22:18） |

### 6.3 热力图（P11 验收）

| 场景 | 结果 |
| --- | --- |
| 柳岸真实数据（9 年） | 画布 **2,070×1,380**；年份下拉 2018–2026 共 **10 项**；容器 `scrollWidth 2,110` / `scrollHeight 1,420` |
| 注入 17 年（2010–2026） | 画布 **2,650×1,062**（自动紧凑 0.7）；**18 个年份项**；总耗时 **665 ms**（纯重渲染 162 ms） |
| 缩放=放大（17 年） | 画布 **5,030×1,804**；`scrollWidth 5,070` |
| 年份=2015（单年） | 单块日历 **550×1,804** |
| 零散数据（仅 2013、2019） | 渲染 **14 块日历**（2013…2026 并集；空档年份照渲染，不截断） |
| 硬编码取证 | 全 bundle **引号年份字面量 0 个**、`minDate/maxDate` **0 次**；日历 range 唯一写法为 `range:S.toString()`（动态） |
| 限制说明 | `dataZoom` 对 calendar 坐标系**无效**（ECharts 限制，实测不响应），改用「缩放下拉 + Ctrl+滚轮 + 年份下拉」等价替代 |

### 6.4 3.x 回归（零回归验证）

| 项 | 结果 |
| --- | --- |
| 3.x 基线库 | `E:\PyWxDump_4x_Backup\_keep\merge_all.db`（440,090,624 B；`MSG` **594,764 行 / 1524 会话**） |
| 判定 | `is_wx4 = False`、`tables_exist("MSG") = True` → 走老逻辑 |
| `get_date_range`（3.x） | `2018-09-12 ~ 2026-10-03`，跨度 9 年，**270,641 条**，与日聚合跨度**完全一致** |
| 交叉核对 | 3.x 库与 4.x 库逐年条数**吻合**（仅 2026 差 6 条，为新库更新所致） |

### 6.5 截图证据（文字描述）

1. 「日聊天数据」页补丁后：颜色设置行显示 **bg(白) + c1 黄 + c2 绿 + c3 蓝 + c4 红 + c5 灰 + c6 紫** 共 7 个实色块（此前为带 ✕ 的空白框）。
2. 「联系人画像」页补丁后：`bg` 显示为白色实心方块，不再带 ✕。
3. Katie 会话截图：红框圈出「本方消息被显示成对方消息」，与微信客户端原图（绿色气泡 = 本方）对照，用于 `IsSender` 误判取证。
4. 补丁后整屏证据：`Page.captureScreenshot(captureBeyondViewport=True)` 落盘 1400×900。

---

## 7. 最终交付物

| 交付项 | 路径 / 值 |
| --- | --- |
| 源码 | `D:\PyWxDump_Source\`（唯一改动目标） |
| 版本号 | **4.0.0**（`wxdump info` 首行输出 `[*] PyWxDump v4.0.0`） |
| 备份 | `E:\PyWxDump_4x_Backup\`（**本次已同步**：125 文件 / 565,468,042 B；源码镜像 86 文件与源目录 **SHA256 逐个一致**；`_keep\merge_all.db` 440,090,624 B 作 3.x 回归基线，未改动） |
| 配置 | `C:\Users\Administrator\wxdump_work\conf_auto.json`（`auto_setting.last = <本人wxid>`） |
| 解密库 | `C:\Users\Administrator\wxdump_work\decrypted_wx4\<账号目录名>\`（28 个库）、`...\<账号目录名>\`（17 个库） |
| 密钥 | `C:\Users\Administrator\.wechat-cli\all_keys.json`（45 条）、`extra_mem_keys.json`（15 条未匹配串，保留） |
| 已清理 | `C:\Users\Administrator\wxdump_work\_obsolete_gap\message_gap2018_2023_decrypted.db`（331 张 `Msg_` 表 / 73,847 行 / 2018-09-12 20:10:13 ~ 2024-04-10 09:06:00）—— 经逐行比对确认仅为**旧格式冗余副本**，**已按指示删除**（原文件 28,119,040 B / 修改时间 2026-10-04 05:31:21 / SHA256 `DA7716AD7B429E772A81059B6425B9E4199311B09D36A976AA4102A52D5D7FC8`；详见文末「七、补片库最终归属判定与清理」） |
| 主要改动文件 | `wx_core\wx_info.py`、`wx_core\get_bias_addr.py`、`wx_core\wx4_prepare.py`、`wx_core\wx4_xor_scan.py`、`wx_core\wx4_key_store.py`、`db\dbbase.py`、`db\dbMSG.py`、`db\dbContact.py`、`api\local_server.py`、`api\remote_server.py`、`ui\web\assets\StatisticsView-_MI6G2N3.js`、`cli.py`、`pyproject.toml` |
| 说明文档（桌面） | `wx4_热力图2018缺失_修复说明.md`、`wx4_热力图跨度自适应与缩放_修复说明.md`、`wx4_info联系人信息失败_修复说明.md`、本报告 |
| 验收证据（备份目录） | `_验收报告_4.0.0_清理前/清理后/wx_path_dbshow修复后_20261004.log`、`_cleanup_manifest_20261004.txt`、`_备份校验报告_20261004.txt`（均已保留） |

### 7.1 全局命令

```powershell
wxdump info            # 自动识别 4.x：进程识别 → 只读内存取密钥 → 读库输出账号/密钥
wxdump bias            # 4.x 语义：输出 { 数据库路径: enc_key }
wxdump ui              # 启动 UI：自动读密钥 → 账号定位 → 增量解密 → 加载会话
wxdump api -p 17957    # 仅起 API/静态服务并指定端口（默认 5000）
wxdump wx_path         # 定位微信数据目录
wxdump dbshow -p <db>  # 展示合并库（--db_path 已兼容）
```

### 7.2 使用方式与注意事项

1. **启动目录很重要**：`gc.work_path = os.getcwd()\wxdump_work`，需在 `C:\Users\Administrator` 下启动才会读到现有 `conf_auto.json`（当前账号由 `auto_setting.last` 决定）。
2. 微信需处于登录状态（内存取密钥依赖已登录进程；只读，不注入、不改文件）。
3. `wxdump info` 取到的密钥会自动回写 `all_keys.json`（写前备份），未匹配串另存 `extra_mem_keys.json`。
4. 4.x 数据根目录 / 解密库目录 / 密钥文件**均已动态定位**：可用 `--wx_root`、`-dd`、`--key_file` 显式指定；也可用环境变量 `PYWXDUMP_WX4_ROOT` 覆盖（本轮新增）。
5. UI 默认全自动：不需要手填 `merge_all.db` 路径，也不需要填微信文件夹路径。
6. 改前端（`ui\web\assets\*.js`）后浏览器需 **Ctrl+F5** 强刷；改后端 `.py` 需重启 `ui`/`api` 进程。

---

## 8. 遗留问题与后续建议（含本次处理结果）

| # | 遗留项 | 现状 / 说明 | 处理结果（本次） |
| --- | --- | --- | --- |
| 1 | `mobile` / `mail` 为空 | 4.x 库与内存均未取到，`info` 实测输出 `None`（已确认） | **已忽略**（按指示保持原样，未修改） |
| 2 | 联系人画像男女统计 | 4.x `contact` 表**无性别字段** → 功能不可恢复（已确证） | **已忽略**（按指示保持原样，未修改） |
| 3 | 38 项发布验收（重跑） | 最后一次重跑按指示跳过 | **已忽略**（按指示保持原样，未修改）。附注：本次在备份目录定位到 38 项验收日志，结论为 **38 通过 / 0 失败**（见 6.0），未改动本行结论 |
| 4 | 备份未同步 | `E:\PyWxDump_4x_Backup\` 曾落后于源目录（缺本轮 `wx_info.py`、热力图 3 个文件等） | **✅ 已解决**：同步 21 个文件（9 个新增 `.bak_*` + 12 个覆盖），**86/86 文件 SHA256 一致、0 缺失、0 不一致**；`_keep` 基线与 5 个验收/清单文件原样保留 |
| 5 | AI 训练数据导出 | `export_for_finetune.py`（桌面，18,731 B，04:36）目标输出 `D:\wx_finetune_data.jsonl`；是否成功产出**未确认** | **已忽略**（按指示保持原样，未修改、未运行） |
| 6 | 补片库 `message_gap2018_2023_decrypted.db` | 补片库 `message_gap2018_2023_decrypted.db` 已通过逐行比对确认：真库中 100% 存在对应消息，补片库仅为旧格式冗余副本。已按指示删除，未影响任何历史数据。（删除前留痕：28,119,040 B / 2026-10-04 05:31:21 / SHA256 `DA7716AD…5D7FC8`） | **✅ 已解决（已删除）** |
| 7 | 热力图 `dataZoom` | ECharts 对 calendar 坐标系不支持 | **保持原样**（本次未涉及；已用等价方案替代） |
| 8 | 柳岸最早数据 = 2018-09-12 | 数据侧事实（非显示截断）；注入 2010–2026 已验证跨度无上限 | **保持原样**（本次未涉及；**已排除补片库嫌疑** —— 补片库经逐行比对确认为旧格式冗余副本并已删除，真库 2018 年数据本就完整） |
| 9 | 周 / 月粒度聚合（用户称「第 8 条」） | 未启用（日历格子为「天」）；后端 `date_count` 已预留 `time_format` 参数 | **已忽略**（按指示保持原样，未修改） |
| 10 | 其它账号 / 辅助库密钥（`extra_mem_keys.json`） | 15 条「内存扫描命中、但在 273 个本机可校验库里找不到对应 salt」的串；本机真库 0/15 命中（另有 6 个串来自最新一次扫描） | **保留，不清理**（判定：无需清理）。理由：① 它不是缓存或重复数据，而是被明确标注「未匹配、勿直接用于解密」的**排查线索**；② 文件仅 6,957 B；③ 已实测不会污染密钥匹配流程（HMAC 校验门会拦住）；④ 若日后出现新库或密钥轮换，这 15 条是唯一可回溯的比对材料；删除无实际收益。当前 13 把已校验密钥已覆盖本账号在用库 |
| 11 | 历史路径已失效 | 早期脚本/代码引用 `D:\decrypted_wx_db`、`D:\xwechat_files\<账号目录名>` 等写死路径 | **✅ 已解决**：7 个文件 10 处改为**动态定位 + 回退**（详见文末「本次处理记录」）；`info`/`bias` 复测通过。**例外（有意保留）**：`ui\web\assets\DbInitComponent*.js`、`ChatView-*.js` 里 `C:\***\WeChat Files\wxid_*******` 属于**输入框占位符文字**，不参与路径解析、不会导致报错，且你要求不动已验证的 UI 代码，故未改；如需一并改成 `xwechat_files` 文案可单独提出 |
| 12 | 早期阶段日期 | 前 9 个阶段的精确日期此前未记录 | **✅ 已解决（已统一）**：全部阶段已按文件 mtime 统一到 **2026-10-03 19:59 ~ 2026-10-04**，逐阶段日期见第 4 章表格；唯一不可取证项为 `batch_decrypt.py` 自身时间戳（该文件已在 P9 清理阶段删除）→ 标注**未确认** |

---

## 本次处理记录（2026-10-04）

### 一、备份同步（对应第 4 条）

- 目标：`D:\PyWxDump_Source\` → `E:\PyWxDump_4x_Backup\`
- 排除规则（未同步、也未删除）：`wxdump_work\`、`__pycache__\`、`_keep\`、`*.db`、`*.pyc`、`*.pyo`、`all_keys.json`、`extra_mem_keys.json`
- 结果：**写入/覆盖 21 个文件**
  - 新增 9 个：`api/remote_server.py.bak_heatmap`、`db/dbMSG.py.bak_heatmap`、`ui/web/assets/DbInitComponent...js.bak_ui`、`StatisticsView-_MI6G2N3.js.bak / .bak2 / .bak_heatmap / .bak_zoomfix`、`wx_core/wx_info.py.bak_contactfix`、`wx_core/wx_info.py.bak_pre_contactfix`
  - 覆盖 12 个：`api/local_server.py`、`api/remote_server.py`、`cli.py`、`db/dbMSG.py`、`db/dbbase.py`、`wx_core/get_bias_addr.py`、`wx_core/wx4_key_store.py`、`wx_core/wx4_prepare.py`、`wx_core/wx4_xor_scan.py`、`wx_core/wx_info.py`、`ui/web/assets/DbInitComponent...js`、`ui/web/assets/StatisticsView-_MI6G2N3.js`
- **逐文件 SHA256 校验：一致 86 / 不一致 0 / 备份缺失 0 → 全部一致 ✅**
- 本批次未删除任何文件；`_keep\`（含 `merge_all.db` 440,090,624 B）、备份侧 `wxdump_work\`、以及 5 个证据文件（`_cleanup_manifest_20261004.txt`、`_备份校验报告_20261004.txt`、`_验收报告_4.0.0_清理前/清理后/wx_path_dbshow修复后_20261004.log`）**原样保留**
- 备份体积变化：116 文件 / 560,982,640 B → **125 文件 / 565,468,042 B**

### 二、删除的文件

**无。** 本批次（备份同步 + 路径修复）未删除任何文件（含用户目录与临时目录）：

- 补片库 `message_gap2018_2023_decrypted.db`：当时取证判定**保留待查**（见第 8 章第 6 行），未删除；**后续经逐行比对确认为旧格式冗余副本，已删除**（见文末「七、补片库最终归属判定与清理」）；
- `extra_mem_keys.json`：判定**保留**（见第 8 章第 10 行），未删除；
- `%TEMP%` 下 `p120`~`p124` 取证脚本与日志：作为可复核证据保留（路径见下）。

### 三、路径修复明细（对应第 11 条，共 7 个文件 10 处）

| 文件 | 位置 | 修改内容 |
| --- | --- | --- |
| `wx_core\wx_info.py` | 新增 `_wx4_default_root()`（第 559 行起） | 4.x 数据根目录动态定位：`wx4_prepare.default_wx_root()` → 各盘 `xwechat_files` → 我的文档 → 回退 `D:\xwechat_files` |
| `wx_core\wx_info.py` | `_wx4_autodecrypt_minimal`（第 602 行） | `root = wx_root or r"D:\xwechat_files"` → `root = wx_root or _wx4_default_root()` |
| `wx_core\wx4_prepare.py` | 新增 `_legacy_decrypt_candidates()`（第 399 行起） | 解密库目录候选列表：`<work>\decrypted_wx4\<账号>`、`<work>\wx4_autodecrypt`、各盘/用户目录/当前目录下的 `decrypted_wx_db` |
| `wx_core\wx4_prepare.py` | `choose_out_dir`（第 393 行） | 原来只认死路径 `D:\decrypted_wx_db` → 改为遍历候选列表，仍只在账号标记一致时复用 |
| `wx_core\wx4_xor_scan.py` | 新增 `_default_wx_root()`（第 598 行起） | 动态根目录解析，支持环境变量 `PYWXDUMP_WX4_ROOT` 覆盖 |
| `wx_core\wx4_xor_scan.py` | `collect_salts_from_db_files`（第 623 行）、`build_page1_map`（第 684 行） | 默认参数 `root=r"D:\xwechat_files"` → `root=None` + 内部动态解析 |
| `wx_core\wx4_xor_scan.py` | `selftest()`（第 870 行）、`__main__`（第 919 行） | 自测里写死的 `D:\xwechat_files\<账号目录名>\db_storage` → 改为扫描动态根目录；CLI `--wx_root` 默认改 `None` 并动态解析 |
| `wx_core\wx4_key_store.py` | 新增 `_wx_root_default()` + 常量（第 31/57 行） | `DEFAULT_WX_ROOT` 由写死字符串改为**动态解析结果**（保持“常量”用法不变，回退仍是 `D:\xwechat_files`） |
| `wx_core\get_bias_addr.py` | `run_wx4`（第 790 行） | `root = wx_root or r"D:\xwechat_files"` → 复用已有 `self._wx4_default_roots()[0]` |
| `wx_core\get_bias_addr.py` | `selftest_xor_decrypt`（第 885 行起） | 自测里写死的账号目录 → 改为在动态根目录下扫 `message_0.db` |
| `db\dbbase.py` | `__init__` 文档串（第 117–128 行） | 示例里的 `D:\decrypted_wx_db\message_0.db` → 改为 `<解密库目录>\message_0_decrypted.db` 并注明默认位置与自动定位 |
| `cli.py` | `info` / `bias` 的 `--wx_root` 帮助文案（2 处） | 「默认 D:\xwechat_files」→「留空自动定位（回退 D:\xwechat_files）」 |

### 四、验证记录

| 验证 | 方式 | 结果 |
| --- | --- | --- |
| 语法 | `py_compile` 7 个改动文件 | ALL OK |
| 导入 | 导入 `pywxdump` 及 6 个改动模块 | import ok |
| 动态定位生效 | 打印 4 个解析入口 | `wx_info` / `wx4_xor_scan` / `wx4_key_store` / `wx4_prepare` **均解析为 `D:\xwechat_files`**（与本机改动前行为一致，无回归） |
| 解密库候选 | 打印 `_legacy_decrypt_candidates` | 返回 4 个候选，首个为当前实际使用的 `C:\Users\Administrator\wxdump_work\decrypted_wx4\<账号目录名>` |
| **功能回归** | `python -m pywxdump info --mem_budget 25`（cwd=`C:\Users\Administrator`） | **13 把密钥**、掩码正确、`nickname: 柳岸`、`account: AbnerUP`、`key_source: memory`、密钥文件无需更新、**无 Traceback** |
| 备份一致性 | 逐文件 SHA256 比对 | 86 一致 / 0 不一致 / 0 缺失 |
| 未动核心逻辑 | 仅改路径解析与文案，`info`/`bias`/`ui`/`api` 的判定逻辑未改 | 由上述回归背书 |

### 五、本次证据文件（可复核）

| 文件 | 用途 |
| --- | --- |
| `%TEMP%\p120_sync_plan.py` / `sync_plan.json` | 同步前的差异比对（9 新增 / 6 改大小 / 71 同大小待哈希） |
| `%TEMP%\p121_legacy_probe.py` / `p121.log` | 补片库与 `extra_mem_keys.json` 初次取证（273 个真库第 1 页；45/45 命中 vs extra 0 命中） |
| `%TEMP%\p122_gap_subset.py` / `p122.log` | 补片库初次核对（按**行数**、只取**单个分片**）：当时得出「331 张表含真分片没有的行（73,847 vs 32,801）」——**该结论已被逐行比对推翻**，留作误判溯源证据（正确口径见 `p126` / `p127`） |
| `%TEMP%\p123_info.log` | 路径改造后的 `info` 回归输出（13 把密钥、柳岸 / AbnerUP） |
| `%TEMP%\p124_sync_backup.py` / `p124.log` | 同步执行 + 逐文件 SHA256 校验记录 |

### 六、本次未做的事（明确边界）

1. 未重跑 38 项发布验收（按指示跳过）；
2. 未修改任何 UI 前端文件（含 3.x 文案占位符）；
3. 未改动 `info` / `bias` / `ui` / `api` 的功能判定逻辑，仅改路径解析与帮助文案；
4. 本批次未删除任何文件（补片库的删除发生在后续追加的「七」节）；
5. 未运行 `export_for_finetune.py`、未动 AI 训练导出链路；
6. 未清理 `%TEMP%` 下本轮取证脚本与日志（保留以便复核）。

---

### 七、补片库最终归属判定与清理（追加，2026-10-04）

**已完成补片库最终归属判定与清理**

**1. 判定过程（只读，未改任何数据）**

- 比对对象：`C:\Users\Administrator\wxdump_work\_obsolete_gap\message_gap2018_2023_decrypted.db`（补片库，331 张 `Msg_` 表 + 1 张 `Name2Id`，**73,847 行**）
- 对照对象：柳岸（`<本人wxid>`）解密库 `message_0 ~ message_8_decrypted.db` 全部 `Msg_` 表（9 个分片合计 **1524 种表名 / 594,777 行**；与补片库同名表跨分片取**并集** = **408,924 行**）
- 方法：`sqlite3` 只读模式（`file:...?mode=ro`）逐行比对，三个脚本递进完成 —— 结构侦察、行内容比对（17 列元组集合）、列级下钻

| 判定口径 | 结果 |
| --- | --- |
| 17 列**严格重复**（完全相同的行） | **0 行** |
| 17 列判「真库没有这一行」 | 73,847 行（100%） |
| 按消息身份 `server_id` 判「真库缺这条消息」 | **0 行（73,847 / 73,847 全部命中）** |
| `create_time` 一致性 | 100% 一致 |

- 差异集中在少数列：`origin_source` / `source` / `packed_info_data` / `compress_content` / `WCDB_CT_source` 为 **100% 差异**（补片库统一为 `0` / `NULL`），`real_sender_id` 99.3%、`message_content` 与 `WCDB_CT_message_content` 各 24.8%（补片库**明文** vs 真库 **zstd 压缩**）、`local_id` 7.9% —— 属**存储形态**差异，不是消息内容缺失。
- 时间分布：2018 年 2,604 行、2019 年 1,655 行、2023 年 41,558 行、2024 年 28,030 行；**2020 / 2021 / 2022 三年为 0 行**；最早 `2018-09-12 20:10:13`，最晚 `2024-04-10 09:06:00`。
- **结论**：**旧格式冗余副本，不含真库之外的消息**；同时**更正**上一版第 8 章第 6 条基于「行数 + 单分片比对」得出的反向结论（根因：`p122` 对每张表只取了**一个分片**的真库行数，例如 `Msg_03b7c4b588aa430011139e6012d092b8` 取到的是 `message_1` 的 19 行，而该表跨分片并集实为 **20,101 行**，恰等于 UI 中 Katie 会话显示的 20,101 条）。

**2. 清理动作与留痕**

| 项目 | 内容 |
| --- | --- |
| 删除对象 | `C:\Users\Administrator\wxdump_work\_obsolete_gap\message_gap2018_2023_decrypted.db` |
| 删除前大小 | **28,119,040 B（26.82 MB）** |
| 删除前修改时间 | 2026-10-04 05:31:21 |
| 删除前 SHA256 | `DA7716AD7B429E772A81059B6425B9E4199311B09D36A976AA4102A52D5D7FC8` |
| 伴随文件 | `-wal` / `-shm` **均不存在**，无需清理 |
| 删除后核验 | 目录 `_obsolete_gap\` **0 个条目（已空）**，目录本身保留；目标文件确认不存在 |
| 空间释放 | C 盘可用空间 **+26.82 MB** |
| 影响面 | 未影响任何历史数据：柳岸真库、`decrypted_wx4` 解密库、`all_keys.json`、备份目录均未改动 |

**3. 证据文件（可复核）**

| 文件 | 用途 |
| --- | --- |
| `%TEMP%\p125_gap_inspect.py` / `p125.log` | 补片库与真库结构侦察（表清单、列结构、行数、时间范围） |
| `%TEMP%\p126_gap_rowdiff.py` / `p126.log` / `p126_result.json` | **行内容逐行比对**（严格重复 0 / 身份缺失 0 / 按年按月分布 / 逐表归属） |
| `%TEMP%\p127_gap_coldiff.py` / `p127.log` / `p127_result.json` | 列级下钻（逐列差异率、差异组合 Top、抽样对照） |
| `E:\Users\Administrator\Desktop\补片库逐行比对报告_20261004.md` | 对用户的比对报告（只读声明、逐表 Top 15 归属、更正说明） |

**4. 文档同步**

- 本报告（含本章）已同步至备份目录：`E:\PyWxDump_4x_Backup\PyWxDump_4.0.0_微信4x适配改造全记录.md`，SHA256 与桌面源一致。
