# 微信 4.1.15.13 改造 PyWxDump · 第二步交付说明

> 改造目录（只改这里）：`D:\PyWxDump_Source\`
> 解密库目录：`D:\decrypted_wx_db\`　本人账号：`<本人wxid>`（柳岸）
> 本步验收脚本：`E:\Users\Administrator\Desktop\wx4_step2_verify.py`

---

## 0. 先纠正三处和实际对不上的地方

| 你提到的 | 实际情况 |
|---|---|
| `db/dbContact.py` | **没有这个文件**。联系人在4.x/3.x 都归 `db\dbMicro.py` 的 `MicroHandler` 管（`db\` 下是 dbbase / dbMSG / dbMicro / dbMedia / dbFavorite / dbPublicMsg / dbOpenIM* / dbSns） |
| `wxdump/core/wx_info.py`、`wx_bias.py` | **没有 `core` 目录，也没有 `wx_bias.py`**。实际是 `wx_core\wx_info.py`（info）和 `wx_core\get_bias_addr.py`（bias） |
| `D:\decrypted_wx_db\all_keys.json` | **这个文件不存在**（该目录下没有任何 json） |

因此 info / bias 我做成了「直接读已解密库 + 注册表」，**不依赖 keys 文件**；
真要有 keys 文件也能用（`--keys_file` 参数已留）。密钥只用于解密，已经解密完了就不用再碰。

---

## 1. 我对「重写内存扫描 vs 改读库」的表态：**选改读库**

理由：

1. 4.x 进程名是 `Weixin.exe`，**没有 WeChatWin.dll**，3.x 那套「按版本号查特征码 + 算 base bias + 结构体偏移」的地基不存在；
2. 内存扫描要 OpenProcess / ReadProcessMemory，**对新版微信风险高**，而且微信一升级偏移就全废，得重新逆向；
3. 你要的信息（wxid / 昵称 / 微信号 / 安装路径 / 版本）**从 `contact_decrypted.db` + 注册表就能等价拿到**，零风险、不随版本失效；
4. **3.x 一行没改**：只要找到 `WeChat.exe`（3.x 进程），仍然走原来的内存路径，老用户行为完全不变。

4.x 信息的具体来源：

- `HKCU\Software\Tencent\Weixin` → `InstallPath`（本机实测 `D:\Program Files\Tencent\Weixin`）
- `Weixin.exe` 的**文件版本号**（不读内存）
- `contact_decrypted.db` 的 `contact` 表 → 昵称 / 微信号(alias) / wxid（本人判据：`flag=2049` 且 username 形如 `wxid_xxx`）
- 数据目录：从 `xwechat_files` 下按 `<my_wxid>_` 前缀找（本机 `D:\xwechat_files\<本人wxid>_fed4`）

---

## 2. 改动清单：文件 / 函数 / 作用

### 新增文件

| 文件 | 作用 |
|---|---|
| `pywxdump\__main__.py` | 让 `python -m pywxdump xxx` 等价于 `wxdump xxx`。**原版没有这个文件**，所以 `python -m pywxdump ui` 以前会报 `No module named pywxdump.__main__` |

### `db\dbbase.py`

| 函数 / 常量 | 改动 |
|---|---|
| `MSG_FILE_PREFIX = "message"`（新增常量） | 4.x 消息库文件名前缀 `message_0..8_decrypted.db` |
| `_init_wx4()` | ① **只给一个消息库时，自动带上同目录其余 `message_*`**（排除 fts/resource/merge/wal/shm）；② 沿用它原有的「第一遍收集 Msg_ 分表 + Name2Id」流程 |
| `_init_wx4_side()`（新增） | 定位 4.x 的三个 side 库：`contact_decrypted.db` / `session_decrypted.db` / `head_image_decrypted.db`（支持 `db_config` 显式指定 `contact_db_path` / `session_db_path` / `head_img_db_path` / `decrypted_dir`） |
| `wx4_side_path(role)`（新增） | 按角色取 side 库路径 |
| `_extra_conn(path)`（新增） | 按路径缓存的额外库连接（不走连接池），`text_factory` 用宽容解码 |
| `_exec_contact()` / `_exec_session()`（新增） | 在 contact / session 库上执行 SQL |
| `_exec_session_join_contact()`（新增） | 在 session 库上 `ATTACH DATABASE` contact 库（别名 `wx4c`）后做跨库 JOIN，用 `PRAGMA database_list` 防重复 ATTACH |
| `_table_exists()` | 4.x 下把「3.x 逻辑表名」映射到 side 库：`Contact`/`ContactHeadImgUrl`/`ContactLabel`/`ChatRoom`/`ChatRoomInfo` → contact 库已定位则为真；`Session`/`ChatInfo` → session 库已定位则为真。**这样 `dbMicro.py` 里那些 `tables_exist(...)` 守卫一行都不用改** |
| `tables_exist()` | 4.x 时失败日志降噪（不再把几百张 `Msg_` 表名整个打出来） |
| `__init__` docstring | 改成 raw string，消掉 `SyntaxWarning: invalid escape sequence` |

### `db\dbMicro.py`（联系人 / 会话 / 群）

| 函数 | 改动 |
|---|---|
| `Micro_add_index()` | 4.x 直接 return。4.x 的 Contact/Session 是**只读独立库**，`tables_exist` 会把它们当"存在"，在主库上 `CREATE INDEX ON Contact` 会报错；且数据量小（9715 / 549 行）不需要索引，也避免写你的解密库 |
| `get_labels()` | 4.x → `SELECT label_id_, label_name_ FROM contact_label` |
| `get_session_list()` | 4.x → 跨库 JOIN（`SessionTable` LEFT JOIN `wx4c.contact`），输出 **28 列、顺序与 3.x 完全一致**（所以下面的按序解包一行未改）；`last_msg_sub_type` 顶替 3.x 的 `Reserved2 AS nMsgSubType`；`COALESCE(NULLIF(remark,''),NULLIF(nick_name,''),'')` 作 strNickName |
| `get_recent_chat_wxid()` | 4.x 无 `ChatInfo` → 改用 `SessionTable`（`last_timestamp*1000` 对齐 3.x 毫秒口径，过滤掉 1007911408000 以下的脏值） |
| `get_user_list()` | 4.x → 从 `contact` 取 16 列（顺序对 3.x）；`word` 搜索映射到 username/nick_name/remark/alias/quan_pin/pin_yin_initial/remark_quan_pin/remark_pin_yin_initial；`label_ids` 在 4.x 下**直接返回空 + warning**（见 §5 功能边界）；**群展开前先挑出真正含 `@` 的 key，空则不再查询**（这是 234 秒的根因，见 §6） |
| `get_room_list()` | 4.x → `chat_room` LEFT JOIN `chat_room_info_detail`，成员用 `(SELECT GROUP_CONCAT(C2.username,'^G') FROM chatroom_member M JOIN contact C2 ON C2.id=M.member_id WHERE M.room_id=R.id)`；第 7 列 `Reserved2` 给群主；`roomwxids` 兼容 `filter` 对象 |
| `_room_expand_depth`（新增守卫） | `get_user_list` ↔ `get_room_list` 会互相展开（群成员里出现群自己 / 群 A 含群 B），4.x 下会无限递归。只在最外层取群信息，内层跳过 |
| `_mini_parse_roomdata()` / `_read_varint()`（新增） | 内置的 RoomData protobuf 解析器（键统一用**字符串**，和 blackboxprotobuf 输出形状一致） |
| `ChatRoom_RoomData(RoomData, fast=False)` | 4.x 用 `fast=True` 走内置解析器；按内容 md5 缓存（见 §6） |
| `get_BytesExtra()` | 失败时只记一行，并改用内置解析器兜底（原来打整段栈 + 几 KB buffer） |

### `db\utils\common_utils.py` / `db\utils\__init__.py`

| 函数 | 改动 |
|---|---|
| `bytes2str_deep(obj)`（新增，已导出） | 递归把嵌套 bytes 解码成新对象。原来的 `bytes2str()` 只处理「字典里的 list 里的 dict」，而且 `item.decode()` 只是重绑循环变量、**没写回列表**，4.x 的 `dict → list → dict` 一个都解不出来，群昵称全丢 |

### `wx_core\wx_info.py`

| 函数 | 改动 |
|---|---|
| `wx4_get_install_path()`（新增） | 读注册表 `HKCU\Software\Tencent\Weixin` 的 `InstallPath` |
| `wx4_get_version()`（新增） | 读 `Weixin.exe` **文件版本号**（不碰内存） |
| `_find_decrypted_db()`（新增） | 在解密库目录里按前缀找库（排除 fts/resource/merge…） |
| `_read_keys_file()`（新增） | 兼容 dict / list 三种形态的 keys 文件（**可选**，本机没有该文件也能跑） |
| `_wx4_find_wx_dir()`（新增） | 从 `D:/E:/C:\xwechat_files` 等根目录按 `<my_wxid>_` 前缀找数据目录 |
| `_wx4_guess_my_wxid()`（新增） | ① 从 wx_path 目录名 `wxid_xxx_abcd` 去掉 4 位后缀；② 从 contact 表 `flag=2049` 的私聊账号推断 |
| `get_wx_info_from_db()`（新增，已导出） | **完全不读进程内存**，返回结构与 `get_wx_info` 一致并多一个 `"source": "db"` |
| `get_wx_info(...)` | 签名扩展 `+ decrypted_dir / my_wxid / wx_path / keys_file`。逻辑：找到 `WeChat.exe` → **走原 3.x 内存路径（一行未改）**；没有该进程但给了 `decrypted_dir` → 打一行 warning 后改走 `get_wx_info_from_db`；两者都没有 → 才报 `WeChat No Run` |
| `get_core_db()` | docstring 改 raw string（原版 `C:\*****` 有 escape 警告） |

### `cli.py`

| 命令类 | 改动 |
|---|---|
| `MainBiasAddr`（`bias`） | 去掉 `--mobile/--name/--account` 的 `required=True`（这就是「bias 报缺少参数」的直接根因）；新增 `-dd/--decrypted_dir`、`--my_wxid`、`--wx_path`、`--keys_file`。**给了 `-dd` 就走 4.x 读库模式**，并明确声明「不会写入/覆盖 WX_OFFS.json」；没给 `-dd` 且缺三个参数时打印 4.x 用法提示（不再 argparse 直接退出）；参数齐全则走原 `BiasAddr(...)` 老逻辑 |
| `MainWxInfo`（`info`） | 新增同样 4 个参数；偏移文件不存在时不再直接崩（`WX_OFFS = {}` + 提示「4.x 不需要偏移文件」） |
| `MainUi` / `MainApi` | 各新增 `--killPort`（`dest="kill_port"`），并把 `kill_port` 传给 `start_server` |

### `api\__init__.py`、`api\local_server.py`

| 函数 | 改动 |
|---|---|
| `find_free_port()`（新增） | 从给定端口起找第一个能 bind 的端口 |
| `find_port_pids()`（新增） | 查监听某端口的 pid / 进程名 / 命令行（**纯查询**）。**优先 `netstat -ano`**：本机实测 `psutil.net_connections()` 要 15 秒以上（它枚举全部连接），放在启动路径上就是把「端口被占用」变成卡死；netstat 0.03 秒出结果，psutil 仅作兜底 |
| `free_port()`（新增） | 先打印占用者；`kill_port=True` 且占用者是**本程序自己的 wxdump/uvicorn** 才 `terminate()`；否则自动换空闲端口 |
| `start_server(...)` | 新增 `kill_port=False`；端口占用分支从「print + `input("Press Enter to exit...")` + return」改成调 `free_port` 后继续启动 |
| `local_server.get_wxinfo()` | 先从 `gc` 取 my_wxid，再从 `db_config.path` 的目录推出 `decrypted_dir`、取 `wx_path`，然后 `get_wx_info(WX_OFFS, decrypted_dir=..., my_wxid=..., wx_path=...)`，这样 UI 的「微信信息」页在 4.x 下也有数据 |

> 还有一个容易漏的点：UI 的「不使用 KEY」页只会帮你把**一个**消息库写进 `db_config["path"]`。
> 4.x 同一个会话的消息是**拆在多个库**里的（Katie 一个人就散在 8 个库里、共 20101 条），
> 所以 `_init_wx4()` 里加了「自动带上同目录其余 `message_*`」的逻辑——
> 实测 UI 那条链路打 `/api/rs/msg_count` 拿到 `{"<他人微信号>": 20101, "total": 594764}`，与真值一致。

---

## 3. 关键代码片段

### 3.1 4.x side 库定位（`db/dbbase.py`）

```python
    def _init_wx4_side(self):
        cfg = self.config if isinstance(self.config, dict) else {}
        self.wx4_contact = cfg.get("contact_db_path") or ""
        self.wx4_session = cfg.get("session_db_path") or ""
        self.wx4_headimg = cfg.get("head_img_db_path") or ""
        # 候选目录 = 主库 + 各消息库所在目录（可用 db_config["decrypted_dir"] 指定）
        ...
        self.wx4_contact = pick(self.WX4_CONTACT_FILE_PREFIX)
        self.wx4_session = pick(self.WX4_SESSION_FILE_PREFIX)
        self.wx4_headimg = pick(self.WX4_HEADIMG_FILE_PREFIX)
```

### 3.2 逻辑表名映射（`db/dbbase.py`，让 dbMicro 的 `tables_exist` 守卫不用改）

```python
    WX4_CONTACT_TABLES = ("contact", "contactheadimgurl", "contactlabel")
    WX4_SESSION_TABLES = ("session", "chatinfo")
    WX4_CONTACT_SIDE_TABLES = ("chatroom", "chatroominfo")

    def _table_exists(self, table):
        if getattr(self, "is_wx4", False):
            t = table.lower()
            if t == "msg":
                return True
            if t in self.WX4_CONTACT_TABLES or t in self.WX4_CONTACT_SIDE_TABLES:
                return bool(self.wx4_contact)
            if t in self.WX4_SESSION_TABLES:
                return bool(self.wx4_session)
        ...
```

### 3.3 会话列表跨库 JOIN（28 列，顺序对 3.x）

```python
sql = ("SELECT S.username, S.sort_timestamp, S.unread_count,"
       " COALESCE(NULLIF(C.remark,''),NULLIF(C.nick_name,''),'') AS strNickName,"
       " S.status, 0, S.summary, S.last_msg_locald_id, 0, S.last_timestamp,"
       " S.last_msg_type, S.last_msg_sub_type, C.username, C.alias, C.delete_flag,"
       " C.local_type, C.verify_flag, 0, 0, C.remark, C.nick_name, '',"
       " C.chat_room_type, C.chat_room_notify, 0, C.description, C.extra_buffer, C.big_head_url"
       " FROM (SELECT username, MAX(last_timestamp) AS MaxnTime FROM SessionTable GROUP BY username) SubQuery"
       " JOIN SessionTable S ON S.username = SubQuery.username AND S.last_timestamp = SubQuery.MaxnTime"
       " LEFT JOIN wx4c.contact C ON C.username = S.username"
       " WHERE S.username != '@publicUser' ORDER BY S.last_timestamp DESC ;")
result = self._exec_session_join_contact(sql)
```

### 3.4 联系人（16 列，顺序对 3.x）

```python
sql = ("SELECT A.username, A.alias, A.delete_flag, A.local_type, A.verify_flag, 0, 0,"
       "A.remark, A.nick_name, '', A.chat_room_type, A.chat_room_notify, 0,"
       "A.description as describe, A.extra_buffer, A.big_head_url "
       "FROM contact A WHERE 1==1 ;")
result = self._exec_contact(sql)
```

### 3.5 群列表（成员来自 `chatroom_member`，成员 id 是 `contact.id`）

```python
sql = ("SELECT R.username,"
       "(SELECT GROUP_CONCAT(C2.username,'^G') FROM chatroom_member M"
       "   JOIN contact C2 ON C2.id = M.member_id WHERE M.room_id = R.id) AS UserNameList,"
       "'',0,0,'',R.owner,R.ext_buffer,"
       "D.announcement_,D.announcement_editor_,D.announcement_publish_time_"
       " FROM chat_room R LEFT JOIN chat_room_info_detail D ON D.room_id_ = R.id WHERE 1==1 ;")
```

### 3.6 递归守卫（`get_user_list` / `get_room_list` 互相展开）

```python
        if getattr(self, "_room_expand_depth", 0) > 0:
            extras = {}
        else:
            room_wxids = [x for x in users.keys() if "@" in x]
            if not room_wxids:
                extras = {}                       # 见 §6：空列表绝不能传进 get_room_list
            else:
                self._room_expand_depth = 1
                try:
                    extras = self.get_room_list(roomwxids=room_wxids) or {}
                finally:
                    self._room_expand_depth = 0
```

### 3.7 info 读库模式（`wx_core/wx_info.py`）

```python
def get_wx_info_from_db(decrypted_dir, my_wxid=None, wx_path=None, keys_file=None,
                        is_print=False, save_path=None):
    """完全不读微信进程内存：注册表拿安装路径、文件版本拿版本号、contact 库拿账号信息"""
    install_path = wx4_get_install_path()
    version = wx4_get_version(install_path)
    contact_db = _find_decrypted_db(decrypted_dir, WX4_CONTACT_FILE_PREFIX, ...)
    my_wxid = my_wxid or _wx4_guess_my_wxid(contact_db, wx_path)
    # 读 contact 表里本人那一行：nick_name / alias / username
    ...
    return [{"version": version, "wxid": my_wxid, "nickname": ..., "account": ...,
             "mobile": "", "mail": "", "key": "", "wx_dir": wx_dir, "source": "db"}]
```

### 3.8 bias / info 的 4.x 分支（`cli.py`）

```python
        if decrypted_dir:
            print("[*] 4.x 模式：跳过内存扫描，从已解密数据库读取微信信息")
            infos = get_wx_info_from_db(decrypted_dir=decrypted_dir, my_wxid=my_wxid,
                                        wx_path=wx_path, keys_file=keys_file, is_print=True)
            print("[*] 说明：4.x 没有 WeChatWin.dll，3.x 的 base bias（内存偏移）机制不适用，")
            print("[*]       因此这里【不会写入/覆盖】WX_OFFS.json 里的偏移记录。")
            return {infos[0].get("version") or "4.x": [0, 0, 0, 0, 0]}
        # ---------- 3.x：原逻辑完全不变 ----------
        if not (mobile and name and account):
            print("[-] 3.x 模式需要同时提供 --mobile --name --account")
            ...
            return None
        return BiasAddr(account, mobile, name, key, db_path).run(True, vlp)
```

### 3.9 端口占用自动处理（`api/__init__.py` + `cli.py`）

```python
    if is_port_in_use(host, port):
        new_port = free_port(host, port, kill_port=bool(kill_port))
        if new_port is None:
            return
        port = new_port
```

```python
    def free_port(host, port, kill_port=False):
        holders = find_port_pids(port)
        print(f"[!] 端口 {port} 已被占用：")
        for pid, name, cmdline in holders:
            print(f"    pid={pid}  name={name}  cmd={cmdline}")
        if kill_port and holders:      # 只结束「上一次没退干净的 wxdump / uvicorn」
            ...
        new_port = find_free_port(host, port)
        print(f"[+] 自动改用空闲端口 {new_port}（也可以用 -p {new_port} 固定下来）")
        return new_port
```

### 3.10 `pywxdump\__main__.py`（新增）

```python
from .cli import console_run

if __name__ == '__main__':
    console_run()
```

---

## 4. UI 到底用哪条命令启动 + 4.x 该怎么填

**命令：`wxdump ui`**（装了包的标准用法）。
`python -m pywxdump ui` 现在也**完全等价**——原版没有 `__main__.py`，所以这条命令以前会报
`No module named pywxdump.__main__`，本步已补上。
> 注意：验证「改后的源码」时要让 `D:\PyWxDump_Source` 优先，例如在该目录下 `python -m pywxdump ui`；
> 直接敲 `wxdump` 走的是 site-packages 里的**未改造**副本。

启动后（「不使用 KEY」页）这样填：

| 界面字段 | 4.x 该填什么 |
|---|---|
| 合并后的数据库 | `D:\decrypted_wx_db\message_0_decrypted.db`（**给一个就行**，会自动带上同目录 `message_1..8` 和 `contact/session/head_image`） |
| 微信文件夹 | `D:\xwechat_files\<本人wxid>_fed4` |
| my_wxid | `<本人wxid>` |

端口相关：

- 默认 5000；**被占用时会自动打印占用者并换一个空闲端口**继续启动（不再报 `Port 5000 is already in use` 后退出）
- 想固定端口：`wxdump ui -p 8081`
- 想直接结束上一次没退干净的自己：`wxdump ui --killPort`（只会杀命令行里带 wxdump/uvicorn 的 python 进程，不会误杀你别的程序）

---

## 5. 4.x 下的功能边界（如实说明）

| 功能 | 4.x 情况 |
|---|---|
| 标签筛选（按 ContactLabel 筛联系人） | **不可用**。4.x `contact` 表没有任何「谁打了哪个标签」字段（`contact_label` 只存标签定义）。代码在 4.x 下**明确返回空并记 warning**，不静默返回全部以免给出错误结果 |
| 微信信息页 | 有数据；版本号/安装路径来自注册表 + `Weixin.exe` 文件版本，`source=db` |
| 群成员 / 群昵称 | 有；来自 `chat_room.ext_buffer`（解码结构同 3.x RoomData）+ `chatroom_member` |
| 联系人头像 | 用 `contact.big_head_url` 直取 |
| zstd 消息体（`WCDB_CT_message_content == 4`）与 4.x 新 XML 解析 | **仍在第三步**，本步没动 |
| 3.x 库（`merge_all.db` 等有 `MSG` 表的） | 全部走老逻辑，一行未改 |

---

## 6. 顺带修掉的一个性能坑：群列表 234 秒 → 2.6 秒

**现象**：`get_room_list()` 197 个群要 234 秒，UI 点名册会卡死。

**根因（真机测出来的，不是猜的）**：

1. `get_user_list()` 里把「含 `@` 的 key」用 `filter` 传给 `get_room_list()`；
   而 `get_room_list()` 里是 `if roomwxids:`——**空列表 = 不过滤 = 取全部群**。
   于是 197 个群里那些没有 `@` 成员的群，每次都把 197 个群整表重新取一遍，
   197×197 全表重跑。插桩实测：`get_user_list` 被调用 **31520 次**，`ChatRoom_RoomData` **31520 次**。
2. `blackboxprotobuf` 解大 `ext_buffer` 很慢，且会抛它自己库里的
   `NameError: name 'field_tyepdef' is not defined`（`length_delim.py:232` 的拼写 bug），
   同一段 buffer 还被反复解码（`get_user_list` / `get_room_list` 互相调用）。

**改法**：

1. `get_user_list()`：先挑出真正含 `@` 的 key，**空就直接不查**（结果完全等价——空 extras 的 `.get()` 本来就全返回 None；3.x 同样受益且语义不变）；
2. `ChatRoom_RoomData(..., fast=True)`：4.x 用内置解析器，并按内容 **md5 缓存**解码结果。

**结果（同一台机器、同一批数据）**：

| 指标 | 改造前 | 改造后 |
|---|---|---|
| `get_room_list()` 197 个群 | 234.17 s | **2.58 s** |
| `get_user_list` 调用次数 | 31520 | **394** |
| `get_room_list` 调用次数 | 198 | **40** |
| `ChatRoom_RoomData` 调用次数 | 31520 | 394 |
| `get_user()` 9715 联系人 | 1.60 s | 1.36 s |

（插桩方式：在 `get_user_list` / `get_room_list` / `ChatRoom_RoomData` 外面套计时包装，
跑的是同一批库、同一台机器。日志见 `%TEMP%\p2_instr_room.log`。）

内置解析器与 blackboxprotobuf 对同一批 197 个群做了逐一对比：**195 个结果完全一致、2 个更多、0 个更差、1 个 blackboxprotobuf 直接抛异常**；
能拿到的群昵称合计 **1336 条**（blackboxprotobuf 会漏掉上面那 3 个群）。

---

## 7. 本步怎么验证

```powershell
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" "E:\Users\Administrator\Desktop\wx4_step2_verify.py"
```

脚本覆盖（最近一次实跑：**62 项通过 / 0 项失败**）：

- **①** 4.x 联系人/会话/群/头像接库（22 项）：`get_labels` 4 个、`get_user` **9715** 个（1.36s）、Katie 昵称 `Katie 💕`、群 **197** 个有 extra、群昵称合计 **1336** 条、最大群成员数正常、`get_session_list` **549** 个（昵称非空 543）、`get_recent_chat_wxid` **544** 条、搜索可用、标签筛选明确返回空
- **②** `get_wx_info_from_db`（8 项）：wxid `<本人wxid>` / 昵称 `柳岸` / `AbnerUP` / 版本 `4.1.15.13` / `source=db` / 数据目录；不给 `-dd` 时保持原行为（照旧返回空）
- **③** CLI（8 项）：`wxdump info -dd`、`wxdump bias -dd`、不带 4.x 参数的指引、`python -m pywxdump`
- **④** 端口占用（6 项）：占用者识别到具体 pid/命令行、自动换端口、**耗时 0.03s（原来 psutil 路径 15s+）**
- **⑤** 真起 `wxdump api` 服务，打 UI 实际调用的接口（13 项）：`/api/ls/init_nokey`、`/api/rs/user_session_list` **549**、`/api/rs/user_labels_dict` 4 个、`/api/rs/user_list` **9715**、`/api/ls/wxinfo`（含 `version 4.1.15.13` / `source: db`）、`/api/rs/msg_count` **Katie = 20101 / total = 594764**、`/api/rs/msg_list` 500 条且字段齐全（本页 `is_sender` 0/1 都有）、`GET /` UI 页面能打开
- **⑥** 3.x 老路径零回归（6 项）：`is_wx4=False`、Katie **20101**、总数 **594764**、`get_user` **11418**、`get_session_list` **1874**、`get_labels` 4、`get_room_list` **197**

---

## 8. 第二步改了哪些文件（备份清单）

```
D:\PyWxDump_Source\pywxdump\
├── __main__.py                     ← 新增
├── cli.py                          ← 改（bias / info / ui / api）
├── db\
│   ├── dbbase.py                   ← 改（4.x 连接、side 库、逻辑表映射、自动带上消息库）
│   ├── dbMicro.py                  ← 改（联系人 / 会话 / 群 / RoomData / 性能）
│   └── utils\
│       ├── common_utils.py         ← 改（bytes2str_deep）
│       └── __init__.py             ← 改（导出）
├── wx_core\
│   ├── wx_info.py                  ← 改（get_wx_info_from_db 等）
│   └── __init__.py                 ← 改（导出）
└── api\
    ├── __init__.py                 ← 改（free_port / start_server）
    └── local_server.py             ← 改（/wxinfo 带 decrypted_dir）
```

第一步已改、本步未动的：`db\dbMSG.py`。
