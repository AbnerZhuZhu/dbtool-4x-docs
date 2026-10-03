# wx4 UI 两个小问题：修复与结论

- 项目：`D:\PyWxDump_Source\`（PyWxDump 4.0.0 微信 4.x 适配版）
- 微信版本：4.1.15.13　当前账号：`<新号wxid>`（聚客猫）
- 本轮只动了 **1 个前端文件**；后端 Python 一行未改，38 项验收按你要求未重跑
- 时间：2026-10-04

---

## 一、问题 1：「颜色设置」那一排带叉号的空白框 —— 已修复

### 1.1 结论（先给答案）

**不是打包漏了图片，也不是静态文件路由不对。** 那一排根本不是图片，而是 **7 个 Element Plus 颜色选择器（`el-color-picker`）**：
`bg` + `c1…c6`（对应图表里 6 条 series 的配色）。颜色选择器在"当前没有颜色值"时的默认外观，就是**一个透明小方块 + Element Plus 自带的 ✕ 图标** —— 也就是你看到的"带叉空白框"。

前端这套产物里**压根没有任何色块图片**可丢，所以「漏图 / 路由错」两条假设都不成立。它跟 4.x 改造也无关：`ui/web` 里所有文件的修改时间都是 2026/10/3 19:59（解压时间），只有 `index.html`、`index-xAnPWFJT4x.js` 是 2026/10/4 0:40（早前 UI 适配改的），本次涉及的 `StatisticsView-*.js` 是从未被改过的原版构建产物。

### 1.2 逐项取证（都是实测，不是推测）

| 检查项 | 实测结果 |
| --- | --- |
| `pywxdump\ui\web` 文件清点 | 29 个文件 = 21 个 `.js` + 5 个 `.css` + 1 个 `.html` + 1 个 `.ico` + 1 个 `.bak`；**图片类只有 `favicon.ico`（270,398 B）** |
| `index.html` 引用了什么 | 只有 `./favicon.ico`、`./data.js`、`./assets/index-xAnPWFJT4x.js`、`./assets/index-KmTWrDAv.css`，无任何色块图 |
| 全前端 js/css 里的图片引用 | 只有 6 处，全是 ECharts 内部 `spinner||r.svg`、`registerMap` 的 `e.svg` 参数，与图例无关 |
| 页面里到底有没有 `<img>` | **`document.images.length === 0`**（"日聊天数据"页实测，一个都没有） |
| 静态资源路由 | `/s/index.html`、`/s/data.js`、`/s/favicon.ico`、`/s/assets/*.js|css` 全部 **200**；页面内失败请求 0 个（此前探到 404 是我用了错误前缀 `/assets/...`，真实前缀是 `/s/`） |
| 那排"框"的真身 | 7 个 `.el-color-picker`，7 个都带 `.el-color-picker__empty`，`__color-inner` 的 `background-color` 全是 `rgba(0,0,0,0)`（透明）→ 也就是空状态 |
| 渲染代码（编译产物 `StatisticsView-_MI6G2N3.js`） | `qZ=mr("strong",null,"颜色设置：",-1)` 后面紧跟 `_e(" bg: "),Bt(ch,{onUpdateColors:…})`，再循环 `h.value.series` 生成 `_e(" c"+da(I+1)+" ")` + 一个 `Bt(ch,…)`；`ch` 就是组件 `ColorSelect`（内部只放一个 `el-color-picker`），它 `const t=Wt("")` 且父组件没传初始色 → 永远是空状态 |

### 1.3 改了什么（`pywxdump\ui\web\assets\StatisticsView-_MI6G2N3.js`，5 处）

改前先整文件备份：`StatisticsView-_MI6G2N3.js.bak`（1,067,024 B，原样）。改后 1,067,217 B（+193 B）。

**① `ColorSelect` 组件接受初始色**

```js
// 旧
ch=gn({__name:"ColorSelect",emits:["updateColors"],setup(r,{emit:e}){const t=Wt(""),
// 新
ch=gn({__name:"ColorSelect",props:{modelValue:{type:String,default:""}},emits:["updateColors"],setup(r,{emit:e}){const t=Wt(r.modelValue||""),
```

**② 日聊天数据页：`bg` 色块绑定当前背景色**

```js
// 旧
Bt(ch,{onUpdateColors:S[2]||
// 新
Bt(ch,{modelValue:h.value.backgroundColor,onUpdateColors:S[2]||
```

**③ 日聊天数据页：`c1…c6` 各自绑定对应 series 的颜色**

```js
// 旧
Bt(ch,{onUpdateColors:L=>{L&&(h.value.series[I].itemStyle.color=L),p(!1)}},null,8,["onUpdateColors"])
// 新
Bt(ch,{modelValue:h.value.series[I].itemStyle.color,onUpdateColors:L=>{L&&(h.value.series[I].itemStyle.color=L),p(!1)}},null,8,["modelValue","onUpdateColors"])
```

**④ 聊天热力图页：`bg` 色块绑定当前背景色**

```js
// 旧
Bt(ch,{onUpdateColors:g[2]||
// 新
Bt(ch,{modelValue:u.value.backgroundColor,onUpdateColors:g[2]||
```

**⑤ 日聊天数据页默认背景色显式设为白色**（否则 `bg` 色块仍是空的；与「聊天热力图」页默认 `#ffffff` 保持一致）

```js
// 旧
f=Wt(""),h=Wt({backgroundColor:f.value,
// 新
f=Wt("#ffffff"),h=Wt({backgroundColor:f.value,
```

> 每处都用断言校验过：锚点在文件中**唯一出现 1 次**才替换；替换后复核 5/5 通过。`modelValue` 只作**初始值**用，点开选色、改色、重置的行为逻辑一行未动。

### 1.4 修复前后对比（浏览器 DOM 实测）

| 位置 | 修复前 | 修复后 |
| --- | --- | --- |
| 日聊天数据 `bg` | `rgba(0,0,0,0)`（透明 + ✕） | `rgb(255,255,255)` 白 |
| 日聊天数据 `c1` | 同上 | `rgb(255,234,182)`（#ffeab6） |
| 日聊天数据 `c2` | 同上 | `rgb(192,255,194)`（#c0ffc2） |
| 日聊天数据 `c3` | 同上 | `rgb(161,217,255)`（#a1d9ff） |
| 日聊天数据 `c4` | 同上 | `rgb(211,115,115)`（#D37373） |
| 日聊天数据 `c5` | 同上 | `rgb(228,236,246)`（#e4ecf6） |
| 日聊天数据 `c6` | 同上 | `rgb(185,4,245)`（rgba(185,4,245,.44)） |
| 聊天热力图 `bg` | 透明 + ✕ | `rgb(255,255,255)` |

其它同步确认：图表 canvas 正常绘制、左侧菜单三个页签切换正常、切换后无脚本报错。
修复后页面截图：`C:\Users\Administrator\AppData\Local\Temp\shot_daychat_after_patch.png`

### 1.5 怎么还原

```powershell
Copy-Item "D:\PyWxDump_Source\pywxdump\ui\web\assets\StatisticsView-_MI6G2N3.js.bak" `
          "D:\PyWxDump_Source\pywxdump\ui\web\assets\StatisticsView-_MI6G2N3.js" -Force
```

（还原后浏览器按 `Ctrl + F5` 强刷即可回到原样。）

### 1.6 一个没动的地方

「联系人画像」页顶部也有同一排 `颜色设置： bg: …`，它的 `bg` 色块**仍是空的**——因为那个页面默认背景色本来就是"未设置"（`backgroundColor:""`），而你只点了「日聊天数据」和「聊天热力图」两页，我就没动它。要让这页也显示成白色，说一声，同样 1 处改动。

---

## 二、问题 2：联系人画像"男 / 女"恒为 0 —— **4.x 库里没有性别数据，无法恢复**

### 2.1 结论（直说）

**不是字段名写错。4.x 的联系人库里根本没有存性别** —— 既没有 `gender`/`sex` 列，也没有别的性别来源。所以这个统计**在当前数据上无法恢复**：不管你换哪个账号（59 万条记录的老号 / 4 条记录的新号），男女都必然是 0。

### 2.2 取证

**a) `contact` 表结构：22 列，没有性别列**

```
id, username, local_type, alias, encrypt_username, flag, delete_flag, verify_flag,
remark, remark_quan_pin, remark_pin_yin_initial, nick_name, pin_yin_initial, quan_pin,
big_head_url, small_head_url, head_img_md5, chat_room_notify, is_in_chat_room,
description, extra_buffer, chat_room_type
```

对 22 列做 `sex|gender` 正则匹配 → **0 命中**（不是"叫 `gender` 而不是 `Sex`"，是压根没有）。

**b) 整个 `contact` 库 17 张表全查过，都没有性别列**

`contact`(125 行) / `name2id`(125) / `encrypt_name2id`(0) / `chat_room`(13) / `chat_room_info_detail`(12) / `stranger`(0) / `ticket_info`(0) / `stranger_ticket_info`(0) / `chatroom_member`(167) / `biz_info`(11) / `oplog`(0) / `sqlite_sequence`(1) / `openim_appid`(1) / `openim_acct_type`(1) / `openim_wording`(5) / `contact_label`(0) / `room_verify_application`(0)

**c) 3.x 那套"性别藏在 ExtraBuf 里"的路子在 4.x 也走不通**

- 用项目自带 `get_ExtraBuf()` 解 125 行里的 **74 个非空 `extra_buffer`**：`性别[1男2女]` **全部为空串**，其它字段（个性签名/手机号/国/省/市）也全空。
- 3.x 用的字段 id（`74752C06`=性别、`46CF10C4`=个性签名、`759378AD`=手机号、`A4D9024A`=国）在 74 个 blob 里**出现 0 次**。
- 用 `blackboxprotobuf` 直接解 blob：4.x 是**另一套 schema**，形如 `{'2':0,'3':0,'4':'微信团队官方帐号','5':{},…,'11':2,'41':1771146634}`，看不出 1/2 语义的性别位。

### 2.3 前端取值链路（说明为什么显示 0）

`StatisticsView-*.js` 里的 `ContactStats` 组件：

```js
e.value = await js();                      // /api/rs/user_list（联系人列表）
let l = {男:0, 女:0, 未知:0};
for (let u in e.value) {
  let h = e.value[u].ExtraBuf;
  if (h) { h["性别[1男2女]"]==1 ? l.男+=1 : h["性别[1男2女]"]==2 ? l.女+=1 : l.未知+=1 }
  else { l.未知+=1 }
}
```

后端 `db/dbMicro.py::get_user_list()` 已经把 `contact.extra_buffer` 交给 `get_ExtraBuf()` 解好放进 `ExtraBuf`；问题是 **4.x 的 blob 里没有这个键**，于是全部落进"未知"→ 男 0 / 女 0 / 未知 125。饼图只画男、女两个 series，所以看着是空的。

### 2.4 你可以选的处理方式（等你说，不改也不影响用）

1. **保持现状**：数据确实为 0，页面如实显示。
2. **把"未知"也画进饼图**：前端 1 处同类补丁，饼图会显示"未知 125"，不再是空图（不改数据、只改展示）。
3. **隐藏这个饼图**：也不难，但会让页面少一块。
4. 真要"恢复"性别，只能去逆 4.x `extra_buffer` 里未公开的 protobuf 语义（当前 74 个 blob 里看不出性别位），成本高、且不保证有，不建议。

---

## 三、重启 UI 验证

```powershell
# 1) 先停旧服务（没有在跑可跳过）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*pywxdump*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2) 启动（会自动开浏览器；不想自动开就加 --noOpenBrowser）
cd /d D:\PyWxDump_Source
wxdump ui
```

**验收清单（前端有改动，务必强刷一次：页面里按 `Ctrl + F5`）**

1. 进「日聊天数据」→ 顶部 `颜色设置：` 后面出现 **白色 bg + 6 个彩色小方块**（c1 浅黄 / c2 浅绿 / c3 浅蓝 / c4 红 / c5 浅灰蓝 / c6 紫），不再有带叉的空框；
2. 点其中任意一个色块 → 弹出取色面板，选个颜色 → 对应曲线立刻变色（功能与原来一致）；
3. 点「重置」→ 图表恢复默认配色；
4. 切「聊天热力图」→ 顶部 `颜色设置： bg:` 显示为白色方块；
5. 顺带看一眼：「联系人画像」页的男女仍是 0（这是数据缺失，不是没修好）。

> 若第 1 条没变化，就是浏览器缓存了旧 JS，再 `Ctrl + F5` 一次即可（服务端实测已吐出新文件：`/s/assets/StatisticsView-_MI6G2N3.js` 200 / 1,067,217 B，内容含补丁标记）。

---

## 四、状态与备份

- 我起的测试服务（端口 **17952**）已关闭，当前**无 pywxdump 残留进程**。
- 本轮改动文件 1 个 + 备份 1 个：
  - `D:\PyWxDump_Source\pywxdump\ui\web\assets\StatisticsView-_MI6G2N3.js`（已打补丁）
  - `D:\PyWxDump_Source\pywxdump\ui\web\assets\StatisticsView-_MI6G2N3.js.bak`（原文件，还原用）
- 后端 Python 本轮**零改动**；`pyproject.toml` 的 `package-data` 已覆盖 `pywxdump.ui = ["web/*", "web/assets/*"]`，补丁文件会被正常收纳（无需改打包配置）。
- 备份目录 `E:\PyWxDump_4x_Backup\` 现已落后源目录 **2 个文件**（逐 SHA256 比对）：
  - `pywxdump\api\local_server.py`（上一轮 init_key 修复）
  - `pywxdump\ui\web\assets\StatisticsView-_MI6G2N3.js`（本轮补丁）
  要重新同步的话说一声，我按之前那套（先移出 `_keep` → `/MIR` → 归位 + 逐文件校验）再跑一次。
