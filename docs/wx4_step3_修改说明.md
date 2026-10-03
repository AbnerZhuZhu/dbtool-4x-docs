# PyWxDump 3.1.46 改造 · 第三步交付说明

**主题：zstd 正文解压 + 4.x 消息解析 + 媒体消息类型标签**

| 项目 | 位置 |
| --- | --- |
| 改造宿主（只改这一份副本） | `D:\PyWxDump_Source\` |
| 已解密数据库 | `D:\decrypted_wx_db\`（message_0..8 + contact + session + head_image + media_0/1 …） |
| 验收脚本 | `E:\Users\Administrator\Desktop\wx4_step3_verify.py` |
| 本次验收结果 | 默认模式 **41 项全 PASS**；加 `--full` **43 项全 PASS**（全量 594,764 条逐条检查） |

---

## 0. 一句话结论

4.x 的正文分两种形态，靠 `WCDB_CT_message_content` 区分：**CT=0 是明文，CT=4 是 zstd 压缩的 BLOB**。
本步做的就是：在 SQL 层把这一列原样取出来 → Python 侧按 zstd 帧头解压 → 剥掉群聊正文前面的「发送者wxid:」前缀 → 交给原来那套（3.x 就在用的）消息解析分支去展示；媒体消息不做预览，但**类型标签一定正确**。

全量 594,764 条实跑结果：

```
文本 459497 未解析 0      语音 51200 未解析 0      图片 30831 未解析 0
语音通话 10932 未解析 0    系统通知 8219 未解析 0    动画表情 7581 未解析 0
引用回复 7555 未解析 0     视频 5147 未解析 0      …其余 20+ 类型全部 0
[PASS] 全量扫描：所有消息都有可显示正文（无原始XML/空/未知）   未解析合计 0
[PASS] 全量扫描：消息总数与库内一致   594764 vs 594764
```

---

## 1. 先说三个「和原始设想不一样」的地方

| 你需求里写的 | 实测事实 | 本步实际做法 |
| --- | --- | --- |
| 检测 `WCDB_CT_message_content == 4`，**解压 compress_content 字段** | `compress_content` 这一列在全部 594,764 条里**恒为空字符串**；被压缩的是 `message_content` **这一列本身** | 解压 `message_content`：CT=4 走 zstd，CT=0 原样返回。`compress_content` 仍传进函数只作兜底保险 |
| 4.x 的 XML 结构与 3.x 完全不同，要按 4.x 新结构适配 | 4.x 正文**就是 3.x 那套 XML**（`<msg><img/>`、`<msg><appmsg>`、`<msgsource>`…），**没有新方言** | 不写「新 XML 解析器」，复用现有解析链，只补三处真实差异（见下） |
| （你没提到，但它是第三步最大的一颗雷） | 4.x 群聊正文前面拼了「发送者wxid:\n」，**这个前缀会让 `xml2dict` 直接返回 `{}`**（lxml 的 recover 也救不回来），于是图片/位置/群系统消息在界面上全变成空 | 新增 `wx4_strip_sender_prefix()`：**先剥前缀，再解析** |

4.x 与 3.x 的真实差异只有三点：① 整段被 zstd 压过；② 群聊正文前面有发送者前缀；③ 少数类型（如 42 名片）属性挂在 `<msg>` 根节点上。

### 另外两个实测事实

1. **`(WCDB_CT, typeof)` 精确分布**：`{(0,'text'): 364148, (4,'blob'): 230616}`，**零交叉**。CT=4 抽样 18,761 条全部命中 zstd 帧头 `28 b5 2f fd`，0 条例外。
2. **「只有富媒体才被压缩」是错的**：`(1,0)` 文本里也有 **100,385 条** 是 CT=4（压缩）——所以解压逻辑必须挂在「所有消息」的公共路径上，不能只挂在图片/语音分支里。
3. zstd 能解、且**只有 zstd 能解**：同一段数据 `lz4.block.decompress` 报 `ValueError: Invalid size: 0x4247762216`，标准 `zlib` 报 `incorrect header check`。

---

## 2. 改了哪些文件、哪些函数（逐个）

### 2.1 `pywxdump\db\dbbase.py`

**① `wx4_projection()` —— 两处改动**

```python
# 改动 1：去掉 CAST（原 "CAST(message_content AS TEXT) AS StrContent"）
#         原写法会把 zstd 的二进制字节在解压前就当成文本吐出来，数据当场就毁了。
            "message_content AS StrContent, "
            ...
            "compress_content AS CompressContent, "
            "packed_info_data AS BytesExtra, "
            "NULL AS BytesTrans, "
            "0 AS Reserved2, "
# 改动 2：把 CT 一起投影出来（0=明文 / 4=zstd），上层才能决定要不要解压。
#         必须写在子查询【内部】：外层 SQL 只能引用子查询已输出的列名，
#         直接在外层写 WCDB_CT_message_content 会报 no such column。
            "WCDB_CT_message_content AS WCDB_CT "
            f"FROM '{table}'"
```

**② 新增 `wx4_known_ids()`** —— 返回本套解密库所有消息库 `Name2Id` 的账号并集（本机 6,415 个），带缓存。
用途：判断群聊正文开头那段「xxx:\n」到底是不是一个真实账号（是 → 剥掉；不是 → 保留，避免误伤「12:30」这种正常文本）。

### 2.2 `pywxdump\db\dbMSG.py`

**③ 模块级新增常量**

```python
# zstd 帧头（28 B5 2F FD），用来判断「这份正文是不是压缩过的」
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# 4.x 群聊正文的「发送者前缀」，实测形如  <他人wxid>:\n@李剑飞 看到信息来个电话
WX4_SENDER_PREFIX_RE = re.compile(r"^([A-Za-z0-9_\-\.@]{3,64}):\r?\n")

# 4.x 媒体/功能消息的数字类型 -> 兜底标签：正文 XML 万一遇到没见过的写法，
# 也保证界面上至少是个正确的类型标签，不会出现「空白一片」
WX4_MEDIA_TAGS = {3: "图片", 34: "语音", 42: "名片", 43: "视频", 47: "动画表情",
                  48: "位置", 49: "链接", 50: "语音/视频通话", 66: "企业微信名片", 67: "企业微信名片"}

# 顶部可选依赖（没装不影响 3.x）
try:
    import zstandard
except Exception:
    zstandard = None
```

**④ 新增 `wx4_decode_message_content(data, ct=None, fallback=None)`** —— 本步核心

```python
def wx4_decode_message_content(data, ct=None, fallback=None):
    if isinstance(data, memoryview):
        data = bytes(data)
    if isinstance(data, str):          # CT=0：明文，直接返回
        return data
    if not isinstance(data, (bytes, bytearray)) or not data:
        if isinstance(fallback, (bytes, bytearray)) and fallback:
            data = bytes(fallback)     # 兜底列（本机恒空，留作保险）
        elif isinstance(fallback, str):
            return fallback
        else:
            return ""
    data = bytes(data)
    if data[:4] == ZSTD_MAGIC:         # ① zstd（4.x）
        if zstandard is None:
            db_loger.warning("4.x 正文是 zstd 压缩，但没装 zstandard：pip install zstandard")
            return ""
        try:
            out = zstandard.ZstdDecompressor().decompressobj().decompress(data)
            return out.decode("utf-8", "replace").rstrip("\x00")
        except Exception as e:
            db_loger.warning(f"4.x zstd 解压失败(CT={ct}, {len(data)} bytes): {e}")
            return ""
    return data.decode("utf-8", "replace")   # ② 不是 zstd：明文存成 bytes
```

> 判据用的是 **zstd magic**，不是 `CT==4` 这个数字。CT 只是当前版本的实测规律，按 magic 判断更稳。

**⑤ 新增 `wx4_strip_sender_prefix(text, sender_wxid, talker, known_ids)`** —— 图片/位置/群系统消息能显示的关键

```python
def wx4_strip_sender_prefix(text, sender_wxid=None, talker=None, known_ids=None):
    if not text or not isinstance(text, str):
        return text
    m = WX4_SENDER_PREFIX_RE.match(text)
    if not m:
        return text
    who = m.group(1)
    if sender_wxid and who == sender_wxid:      # 就是这条消息的发送者
        return text[m.end():]
    if talker and who == talker:                # 等于当前会话（群系统消息的前缀是群 id）
        return text[m.end():]
    if known_ids and who in known_ids:          # 在 Name2Id 账号集合里
        return text[m.end():]
    if who.startswith("wxid_") or who.endswith(("@openim", "@chatroom")):
        return text[m.end():]
    return text                                  # 否则原样保留（不误伤正常文本）
```

前缀的三种真实形态（本机 4.8 万条群消息）：

| 形态 | 例子 |
| --- | --- |
| 发送者本人 | `<他人wxid>:\n@李剑飞 看到信息来个电话` |
| 企业微信成员 | `<企业微信id>:\n今天可能有事没看到` |
| 群自己（系统消息） | `<群ID>:\n<sysmsg type="sysmsgtemplate">…` |

为不误伤「12:30」「注意:」这类正常文本，前缀必须**同时**满足：字符集是账号会用的 ASCII（字母数字 `_ - . @`）+ 紧跟换行 + 属于已知账号。

**⑥ `get_msg_list()` —— 只多取一列，3.x 列数顺序不变**

```python
extra_col = "WCDB_CT, " if getattr(self, "is_wx4", False) else ""
sel_cols = (..., "Reserved4,Reserved5,Reserved6,CompressContent,BytesExtra,BytesTrans,Reserved2," + extra_col)
n_biz = 27 if extra_col else 26          # 业务列数：3.x 仍是 26
...
result = [tuple(list(r[:n_biz]) + [start_index + i + 1]) for i, r in enumerate(rows)]
```

**⑦ `get_msg_detail()` —— 解包 CT + 解码 + 剥前缀**

```python
# 4.x 的行尾多一列 CT：先摘出来，后面与 3.x 共用同一个 27 列解包写法
WCDB_CT = None
if len(row) == 28:
    WCDB_CT = row[26]
    row = tuple(row[:26]) + (row[27],)
(..., MsgSequence, StrContent, ..., CompressContent, BytesExtra, BytesTrans, Reserved2, _id) = row

is_wx4 = bool(getattr(self, "is_wx4", False))
if is_wx4:      # 3.x 时整段不执行，StrContent/CompressContent 的处理与原来一字不差
    StrContent = wx4_decode_message_content(StrContent, WCDB_CT, CompressContent)
    if isinstance(StrContent, (bytes, bytearray)):
        StrContent = bytes(StrContent).decode("utf-8", "replace")
    StrContent = wx4_strip_sender_prefix(StrContent,
                                        sender_wxid=self.wx4_sender_wxid(StrTalker, TalkerId),
                                        talker=StrTalker, known_ids=self.wx4_known_ids())
```

**⑧ 新增 `_msg_body()`** —— 统一「正文从哪来」

```python
def _msg_body(self, StrContent, CompressContent):
    # 4.x：正文就是 StrContent（已解压）；3.x：还是走原来的 lz4
    if getattr(self, "is_wx4", False):
        return StrContent
    return decompress_CompressContent(CompressContent)
```

**⑨ 各类型分支的四处修补**（都是「界面上漏出原始 XML / 残缺文案」的根因）

| 位置 | 现象 | 修法 |
| --- | --- | --- |
| `(48,0)` 位置 | 根节点就是 `<location …>` 时只取到 `x`，label/poiname 全丢 | 改成 `{k: v for k, v in content_tmp.items() if not isinstance(v, (dict, list))}` |
| `(49,19)` 合并转发 | `title`/`des` 是 dict 时被直接拼进正文，界面显示成 `{}` | 非 str 一律置空 |
| `(49,57)` 引用回复 | 只认 3.x 的 `<?xml` 写法，被引用的 4.x 图片/语音 XML 会**整段漏到界面**（含 CDATA） | 4.x 先剥前缀，再用 `wx4_content_label` 统一抽标签 |
| `(34,0)` 语音取不到 voicelength | 出现「语音时长：秒」这种半截文案 | 4.x 退化成 `[语音]` |

**⑩ 媒体 src 安全阀（新增，兜住前端）**

```python
# 「图片 / 视频 / 动画表情 / 文件」这四类在前端是【只渲染媒体、不显示文字】的组件，
# 而 4.x 的图片视频本体在 media_*.db 里（消息库只留 md5）、表情是需要登录态的 CDN 直链、
# 文件给的是空路径 —— 交上去只会渲染成一个空白框，反而把「图片 / 文件：xxx」标签盖掉。
if is_wx4 and src and type_name in ("图片", "视频", "动画表情", "文件"):
    _s = str(src)
    if _s.lower().startswith(("http://", "https://")) or not os.path.isfile(_s):
        src = ""
```

**⑪ 新增兜底/工具函数**

- `wx4_need_label(msg)`：判断一条消息是否还需要再抽一次标签（空、还是原始 XML、`未知-xx`、`语音/视频通话[]` 都算需要）。
- `wx4_content_label(content, type_name="", type_id=None)`：把（已解压的）XML 抽成界面上能看的一句话。`type_id` 是后加的第 3 个参数，因为界面上拿到的是中文类型名（「位置」），里面没有数字，光靠 `type_name` 认不出经纬度那种根节点写法。
- `wx4_sysmsg_template(content, d=None)`：群系统消息按模板还原。模板里的 `$username$` 就是 `<link>` 的 `name`，把对应 `<memberlist>` 里的 `<nickname>` 填回去（多个用 `、` 连），没填上的占位符删掉。
  例：`"$username$"邀请"$names$"加入了群聊` → **`"铁血狼魂-吴军长"邀请"贺帥"加入了群聊`**
- `wx4_voip_text(content)`：通话消息取 `<VoIPBubbleMsg><msg>` 里的 CDATA（`已取消` / `对方忙线中` …）。
- `_wx4_link_text(link_node)`：从 `<link>` 里抽 `nickname` 列表，取不到再取 `<plain>`。
- `wx4_human_size(num)`：字节数 → `1.2MB`（文件消息显示用）。

**⑫ 明确未改动**：`decompress_CompressContent()`（3.x 的 lz4 解压）**一个字符都没动** —— 验收脚本用 AST 抽取源码与原版逐字符比对，`副本 551 字符 / 原版 551 字符` PASS。

### 2.3 `pywxdump\db\utils\common_utils.py`

**⑬ `type_converter()` 的 `type_name_dict` 补 6 个类型名**（原有条目全部保留）：

```python
(49, 62): "拍一拍",  (49, 76): "(分享)音乐", (49, 92): "(分享)音乐", (49, 93): "(分享)音乐",
(49, 2001): "微信红包", (67, 0): "企业微信名片"
```

### 2.4 `pywxdump\ui\web\`（前端构建产物，只动 4 处）

前端消息渲染是**按 `type_name` 分派组件**的，其中「图片 / 视频 / 动画表情」三个组件**只渲染 `src`、根本不显示 `msg` 文字** —— 所以 src 为空时界面只剩一个空白框，你要求的「类型标签必须正确显示」就落不了地。

| 文件 | 改动 |
| --- | --- |
| `ui\web\assets\index-xAnPWFJT.js` | 备份为 `index-xAnPWFJT.js.bak` |
| `ui\web\assets\index-xAnPWFJT4x.js` | **新文件**（2,064,240 B）：4 处 `R.type_name=="X"?` → `R.type_name=="X"&&R.src?` |
| `ui\web\index.html` | script 引用改指 `./assets/index-xAnPWFJT4x.js`（换文件名顺带绕开浏览器旧缓存） |

```js
// 改前：src 为空也进媒体组件 → 空白框，看不到任何文字
R.type_name=="图片"?(L(),ce(Nre,{...src:g(gf)(R.src)...})
// 改后：src 为空时三元链继续往下走，最终落到 key:6 的文字分支（渲染 content:R.msg）
R.type_name=="图片"&&R.src?(L(),ce(Nre,{...})
```

三元链尾（已从产物里读出核对）：`…:R.type_name=="语音"?(语音组件, 带 msg):(文字组件, content:R.msg)`。
**src 有值时行为完全不变**，3.x 不受影响。

---

## 3. 全量 594,764 条：各类型在界面上的显示效果

| 类型 | 条数 | 界面上显示为 |
| --- | ---: | --- |
| 文本 | 459,497 | 正文原文（含 10 万条 zstd 压缩的，已解压） |
| 语音 | 51,200 | `语音时长：2.64秒`（voicelength 是毫秒，已 /1000） |
| 图片 | 30,831 | `图片`（4.x 图片本体在 media_*.db，不做预览） |
| 语音通话 | 10,932 | `语音/视频通话[已取消]`、`[对方忙线中]`、`[已在其它设备拒绝]` |
| 系统通知 | 8,219 | 自然语言，如 `你已添加了🌷，以上是打招呼的消息。` |
| 动画表情 | 7,581 | `动画表情` |
| 引用回复 | 7,555 | 引用块 + 原消息（`[引用](时间)昵称:原文`） |
| 视频 | 5,147 | `视频` |
| (分享)视频号视频 | 3,515 | `视频号：闪电新闻` |
| 转账 | 2,036 | `转账：¥87.00 / 转账时间：2025-12-16 21:09:56` |
| (分享)卡片式链接 | 2,030 | 链接标题 + 描述 |
| 微信红包 | 1,718 | `微信红包` |
| 文件 | 1,486 | `文件：xxx.pdf（1.2MB）` |
| 位置 | 921 | `位置：厦门市翔安区 聚客猫` + 经纬度 |
| (分享)小程序 | 637 | 小程序标题 |
| 拍一拍 | 284 | `我拍了拍 "世婷"` |
| 接龙 | 244 | 接龙标题 |
| 合并转发的聊天记录 | 205 | `群聊 / …` |
| 推荐公众号 / 视频号直播 / GIF表情 / 笔记 / 音乐 / 群公告 / 视频号名片 / 粘贴的文本 / 企业微信名片 / 小说 / 位置共享 等 | 666 | 各自的标题或标签 |
| 未知（10000 类空正文 / 11000） | 67 | `[无内容消息]` |

**3.x 零回归**（`merge_all.db`，检测到 `MSG` 表必须走老路）：

```
[PASS] 3.x 库 is_wx4=False（没有走 4.x 新路）
[PASS] 3.x Katie 消息数 = 20101        [PASS] 3.x 消息正文未受影响（非空） 500/500
[PASS] 3.x 的 lz4 解压函数与原版逐字符一致（未被改动）  副本 551 字符 / 原版 551 字符
[PASS] 3.x 链接消息仍能解出正文（真实数据）
```

---

## 4. 重启改造版 UI（管理员 PowerShell）

```powershell
cd D:\PyWxDump_Source
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -m pywxdump ui
```

- 换端口（5000 被占时会自动往后找，也可以自己指定）：末尾加 `-p 8081`
- **一定要用 Python 3.12 那个绝对路径**：PATH 里的 `python` 是另一个版本，装了 `pywxdump` 的不是它；用 `python -m pywxdump ui` 只有在 3.12 是默认 `python` 时才对。
- 启动后浏览器打开 `http://127.0.0.1:5000`（或提示的端口），首次进「不使用 KEY」页填：

| 填什么 | 填值 |
| --- | --- |
| 合并后的数据库文件路径 | `D:\decrypted_wx_db\message_0_decrypted.db` |
| 微信文件夹路径 | `D:\decrypted_wx_db` |
| 我的wxid（可选） | `<本人wxid>` |

- 浏览器**强刷一次**（Ctrl + F5），确保加载的是新的前端产物。
- 只想重跑验收（不影响 UI）：

```powershell
cd D:\PyWxDump_Source
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" "E:\Users\Administrator\Desktop\wx4_step3_verify.py"
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" "E:\Users\Administrator\Desktop\wx4_step3_verify.py" --full
```

---

## 5. 验收清单（照着点就行）

| # | 做什么 | 应该看到 | 本次实测 |
| --- | --- | --- | --- |
| 1 | 打开 Katie（`<他人微信号>`）2024-10-09 附近 | 图片消息显示 **「图片」**，不是空白框 | 图片 30/30 显示「图片」 |
| 2 | 同会话找视频消息 | 显示 **「视频」** | 视频 1/1 |
| 3 | 同会话找语音消息 | 显示 **「语音时长：6.56秒」** | 语音 2/2 |
| 4 | 找一个文件消息（如微信支付交易明细 PDF） | 显示 **「文件：xxx.pdf（1.2MB）」** | 文件 20/20 |
| 5 | 找表情包 / 动画表情 | 显示 **「动画表情」** | 动画表情 7/7 |
| 6 | 找一条分享链接 | 显示**链接标题 + 描述**，不再空标题 | 标题正常取出 |
| 7 | 找一条位置 | 显示 **地名 + 经纬度**，不是「纬度:【】 经度:【】」空壳 | 位置 168 条全部完整，空壳 0 |
| 8 | 找一个 197 人的群，翻系统消息 | 显示 **「"铁血狼魂-吴军长"邀请"贺帥"加入了群聊」**，不是 `$username$` 和 CDATA 原文 | 模板已还原 |
| 9 | 群里任意一条成员发言 | 正文**不带**「wxid_xxx:」前缀 | 扫 52,900 条，漏网 0 |
| 10 | 找一条撤回消息 | `你撤回了一条消息` | 正常 |
| 11 | 转账 / 微信红包 / 名片 / 视频号 / 音乐 / 拍一拍 / 引用回复 | 各显示自己的文案，不出现原始 XML、`{}`、CDATA | 全部 0 异常 |
| 12 | 消息总数 | Katie `20101`、全库 `594764` | 一致 |
| 13 | 切回 3.x 老库（`merge_all.db`） | 与改造前完全一样（is_wx4=False） | 零回归 |
| 14 | 通读几屏消息 | **看不到任何** `<msg>`、`<?xml`、`$xxx$`、`<![CDATA[` | 全量 0 条 |

---

## 6. 边界与遗留（如实说明）

1. **媒体文件不做预览**（你的要求就是「不追求完美预览，但类型标签必须正确」）。4.x 的图片/视频本体在 `media_*.db` 里，消息库只留了 md5，要出缩略图得再做一步 media 解密——那是第四步的事。为了让标签一定看得见，这四类消息的 `src` 会被主动清空，界面退回文字分支。
2. **语音**的 `src` 是微信文件目录下的相对路径，界面里显示的是「语音时长：x.xx秒」这行文字；该 wav 文件真的存在时才能播放。
3. **表情的 CDN 直链被主动放弃**：4.x 动画表情给的是 `http://wxapp.tc.qq.com/...`，需要登录态，直接渲染出来是裂图/空白，不如显示「动画表情」。
4. **前端 4 处改动是按构建产物字节核验 + 后端接口数据核验**（四类媒体 `src` 全空：图片 30831 / 视频 5147 / 动画表情 7581 / 文件 1486 条；三元链尾已读出来确认落到文字分支），没有在真实浏览器里逐条点开截图确认——你强刷页面看一眼就是最终确认。
5. **11,000 类型的 65 条空正文**显示 `[无内容消息]`；`local_type=50` 通话消息的取消/忙线文案取自 XML 原文。
6. 本步仍未碰你明确拒绝的那几件事：**没有重新提取密钥、没有换工具、没有用 wechatauto-replica、没有填主密钥、没有重写整个项目**。

---

## 7. 复现与回归

```powershell
# 快检（约 30 秒，含真起一次服务打 UI 接口）
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" "E:\Users\Administrator\Desktop\wx4_step3_verify.py"

# 全量（约 1 分钟，逐条扫完 594,764 条）
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" "E:\Users\Administrator\Desktop\wx4_step3_verify.py" --full
```

- 快检日志：`C:\Users\Administrator\AppData\Local\Temp\wx4_step3_verify.log`
- 全量日志：`C:\Users\Administrator\AppData\Local\Temp\wx4_step3_verify_full.log`
- 验收脚本会临时在 `127.0.0.1:17900` 起一次 `wxdump api`，打完接口自己关掉；如果 17900 被占，脚本会自己换端口。
