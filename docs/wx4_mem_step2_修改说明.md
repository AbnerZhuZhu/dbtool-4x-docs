# 微信 4.1.15.13 适配 · 第二步：info / bias 改用本地密钥文件（B 计划）

> 共识：**彻底放弃内存扫描，不做 DLL 注入/Hook**，密钥一律来自本地 JSON 密钥文件。
> 本步只改 2 个文件：`pywxdump\wx_core\wx_info.py`、`pywxdump\cli.py`（都在 `D:\PyWxDump_Source\`）。

## 0. 一句话结论（先说实测）

`info` 和 `bias` 现在已经能直接用你的 `all_keys.json`：

- `wxdump info --mode 4x --key_file <all_keys.json>` → 输出昵称「柳岸」、微信号 `AbnerUP`、wxid，
  以及 **29 个库的密钥**（`rd['key']` 填的是 `message\message_0.db` 那把，另有 `keys`/`key_count`/`key_db`）。
- `wxdump bias --mode 4x --key_file <all_keys.json>` → 输出 **`{库路径: 密钥}`** 字典（29 条），不再输出 `[0,0,0,0,0]`。
- 4.x 分支**完全不碰进程内存**，也**不要求存在 `WeChatWin.dll`**；`bias` 也不再写 `WX_OFFS.json`。
- 3.x 老逻辑零回归：旧的 62 项回归套件重跑 **62 项全通过**（含 `merge_all.db` 的 Katie 20101 条 / 全库 594764 条 / 联系人 11418 / 会话 1874 / 群 197）。

## 1. 改了哪些文件、哪些函数

| 文件 | 函数 / 位置 | 新行号 | 改动性质 |
| --- | --- | ---: | --- |
| `wx_info.py` | `read_wx4_keys_file()`、`_wx4_pick_main_db()`、`_wx4_guess_decrypted_dir()` | 395–471 | **整段新增**（读 4.x 密钥文件 / 选代表 key / 自动找解密库目录） |
| `wx_info.py` | `get_wx_info_from_db()` | 521–630 | **整函数替换**（新增 `key_file`/`keys` 参数；没有解密库也能只输出密钥；打印密钥表） |
| `wx_info.py` | `get_wx_info()` | 633–731 | **整函数替换**（新增 `mode`/`key_file`；`4x` 强制走读库+密钥文件，不读内存） |
| `cli.py` | 顶部 import | 15 | 新增 1 行 |
| `cli.py` | `MainBiasAddr` | 100–189 | **整类替换**（`--mode`/`--key_file`；4.x 输出密钥字典；3.x 钉死 `mode="3x"`，不再回落内存扫描） |
| `cli.py` | `MainWxInfo` | 192–232 | **整类替换**（`--mode`/`--key_file`；`4x` 不打印"偏移文件不存在"这类无关提示） |

**为什么 `--mode` 要写成 `dest="wx_mode"`**：`cli.py` 底部的分发是 `models[args.mode].run(args)`，而子命令本身用的就是 `dest="mode"`（值 = info/bias/ui…）。如果子命令里再定义同名 `--mode`，会把 `args.mode` 覆盖成 `4x`，直接 `KeyError`。所以对外仍叫 `--mode 4x`，内部落到 `args.wx_mode`。

## 2. 代码块（可精确替换）

### 2.1 `wx_info.py` 新增：读 4.x 密钥文件 + 选代表 key + 自动找解密库

```python
def read_wx4_keys_file(key_file):
    """
    【第二步·4.x · B 计划】读取 4.x 形态的本地密钥文件（完全不读进程内存）：
        {
          "message\\\\message_0.db": {"enc_key": "<64位hex>", "salt": "<32位hex>", "size_mb": 84.3},
          "contact\\\\contact.db":   {"enc_key": "<64位hex>", "salt": "<32位hex>"},
          ...
        }
    也兼容 [{"db": "...", "enc_key": "..."}, ...] 这种列表写法。

    :param key_file: 密钥文件路径（如 C:\\Users\\Administrator\\.wechat-cli\\all_keys.json）
    :return: {库路径: enc_key}；文件不存在 / 格式不认 / 没读到 key 时返回 {}
    """
    if not key_file or not os.path.exists(key_file):
        return {}
    try:
        with open(key_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        wx_core_loger.warning(f"[-] 读取 4.x 密钥文件失败 {key_file}: {e}")
        return {}

    items = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                d = dict(v)
                d.setdefault("db", k)          # 键名本身就是库路径，带上
                items.append(d)
            elif isinstance(v, str):           # 兼容 {"库路径": "64位hex"}
                items.append({"db": k, "enc_key": v})
    elif isinstance(data, list):
        items = [d for d in data if isinstance(d, dict)]

    out = {}
    for d in items:
        db = d.get("db") or d.get("path") or d.get("file") or d.get("db_path")
        k = d.get("enc_key") or d.get("key") or d.get("Key")
        if isinstance(k, bytes):
            k = k.hex()
        if not isinstance(k, str):
            continue
        k = k.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", k):   # 只要 32 字节(64位hex)的 enc_key
            continue
        out[str(db) if db else f"db_{len(out)}"] = k
    return out


def _wx4_pick_main_db(keymap):
    """从 {库路径: key} 里挑一个作为 rd['key'] 的代表（优先 message\\message_0.db，其次 contact.db）"""
    keys = sorted(keymap)
    prefs = (r"message\message_0.db", "message_0.db", "message_0", "contact.db", "contact")
    for p in prefs:
        low = p.lower()
        cands = [k for k in keys if k.replace("/", "\\").lower().endswith(low)]
        if not cands:
            cands = [k for k in keys if low in k.replace("/", "\\").lower()]
        if cands:
            return min(cands, key=len)   # 路径最短的那个，避免命中 biz_message_0 之类
    return keys[0]


def _wx4_guess_decrypted_dir():
    """【4.x】没传 -dd 时，按常见位置找一个像"已解密库目录"的目录（含 contact 库且不含 -wal）"""
    cands = [r"D:\decrypted_wx_db", r"E:\decrypted_wx_db", r"C:\decrypted_wx_db",
             os.path.join(os.path.expanduser("~"), "decrypted_wx_db"),
             os.path.join(os.getcwd(), "decrypted_wx_db")]
    for d in cands:
        try:
            if d and os.path.isdir(d) and _find_decrypted_db(d, "contact"):
                return d
        except Exception:
            continue
    return None
```

### 2.2 `wx_info.py` 替换：`get_wx_info_from_db`

```python
def get_wx_info_from_db(decrypted_dir: str = None, my_wxid: str = None, wx_path: str = None,
                        keys_file: str = None, is_print: bool = False, save_path: str = None,
                        key_file: str = None, keys: dict = None):
    """
    4.x 专用：不读内存，从已解密的数据库 + 本地密钥文件还原微信信息。
    :param decrypted_dir: 解密后的数据库目录（如 D:\\decrypted_wx_db），可缺省
    :param my_wxid:       当前登录账号，不传会自动推断
    :param wx_path:       4.x 数据目录（如 D:\\xwechat_files\\wxid_xxx_fed4），用于填 wx_dir
    :param keys_file:     （旧名，兼容保留）密钥文件路径
    :param key_file:      【第二步】本地密钥文件路径，形如 {"库路径": {"enc_key": "64位hex", "salt": "..."}}
    :param keys:          也可以直接传 {库路径: enc_key}
    :return: 与 get_wx_info 同样的结构 [{"pid","version","account",...,"source"}, ...]
    """
    key_file = key_file or keys_file

    if not decrypted_dir or not os.path.isdir(decrypted_dir):
        if key_file and os.path.exists(key_file):
            # 【第二步·B 计划】只给了密钥文件、没有解密库：不报错，照样输出密钥与库路径
            wx_core_loger.warning(f"[-] 解密目录不可用（{decrypted_dir}），本次只输出密钥文件里的密钥")
            decrypted_dir = None
        else:
            wx_core_loger.warning(f"[-] get_wx_info_from_db: 解密目录不存在 {decrypted_dir}")
            return []

    contact_db = _find_decrypted_db(decrypted_dir, "contact") if decrypted_dir else None
    if decrypted_dir and not contact_db:
        wx_core_loger.warning(f"[-] {decrypted_dir} 里没找到 contact 库")
        return []

    if decrypted_dir and not my_wxid:
        my_wxid = _wx4_guess_my_wxid(contact_db, wx_path)
    if decrypted_dir and not my_wxid:
        wx_core_loger.warning("[-] 未能推断本人 wxid，请用 --my_wxid 指定")

    rd = {'pid': None, 'version': wx4_get_version(), "account": None, "mobile": None, "nickname": None,
          "mail": None, "wxid": my_wxid, "key": None,
          "wx_dir": _wx4_find_wx_dir(my_wxid, wx_path), "source": "db"}
    try:
        con = sqlite3.connect(contact_db) if contact_db else None
        con.text_factory = lambda b: b.decode("utf-8", "replace") if isinstance(b, bytes) else b
        row = None
        if con and my_wxid:
            row = con.execute(
                "SELECT username, nick_name, alias, remark FROM contact WHERE username=? LIMIT 1",
                (my_wxid,)).fetchone()
        if con and not row:
            # 兜底：flag=2049 的私聊账号就是本人
            row = con.execute(
                "SELECT username, nick_name, alias, remark FROM contact "
                "WHERE flag=2049 AND username LIKE 'wxid=_%' ESCAPE '=' AND username NOT LIKE '%@%' LIMIT 1"
            ).fetchone()
        if row:
            rd["wxid"] = row[0] or my_wxid
            rd["nickname"] = row[1] or None
            rd["account"] = row[2] or None  # 微信号(alias)
        if con:
            con.close()
    except Exception as e:
        wx_core_loger.error(f"[-] 读取 contact 库失败: {e}", exc_info=True)

    # 【第二步·B 计划】密钥：从本地密钥文件读，不读进程内存
    keymap = {}
    if isinstance(keys, dict) and keys:
        keymap = {str(k): v for k, v in keys.items()}
    elif key_file:
        keymap = read_wx4_keys_file(key_file)
    if keymap:
        main_db = _wx4_pick_main_db(keymap)
        rd["key"] = keymap[main_db]
        rd["key_db"] = main_db
        rd["keys"] = keymap
        rd["key_count"] = len(keymap)
    else:
        # 兼容 3.x 形态的密钥文件（{"key": "...", "wxid": "..."}）
        cands = ([key_file] if key_file else [])
        if decrypted_dir:
            cands += [os.path.join(decrypted_dir, n) for n in ("all_keys.json", "keys.json")]
        for cand in cands:
            k, kwxid = _read_keys_file(cand)
            if k:
                rd["key"] = k
                if not rd.get("wxid") and kwxid:
                    rd["wxid"] = kwxid
                break

    result = [rd]
    if is_print:
        print("=" * 32)
        print("[+] 数据来源: 已解密数据库 + 本地密钥文件（4.x，未读取微信进程内存）")
        for k, v in rd.items():
            if k == "keys" and isinstance(v, dict):
                print(f"[+] {k:>8}: {len(v)} 个库的密钥")
                continue
            print(f"[+] {k:>8}: {v if v else 'None'}")
        if isinstance(rd.get("keys"), dict) and rd["keys"]:
            print("    {数据库路径: 密钥}")
            for db, kk in rd["keys"].items():
                print(f"    {db} -> {kk}")
        print("=" * 32)

    if save_path:
        try:
            infos = json.load(open(save_path, "r", encoding="utf-8")) if os.path.exists(save_path) else []
        except Exception:
            infos = []
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(infos + result, f, ensure_ascii=False, indent=4)
    return result
```

### 2.3 `wx_info.py` 替换：`get_wx_info`

```python
def get_wx_info(WX_OFFS: dict = None, is_print: bool = False, save_path: str = None,
                decrypted_dir: str = None, my_wxid: str = None, wx_path: str = None, keys_file: str = None,
                mode: str = None, key_file: str = None):
    """
    读取微信信息(account,mobile,nickname,mail,wxid,key)
    :param WX_OFFS:  版本偏移量
    :param is_print:  是否打印结果
    :param save_path:  保存路径
    :param decrypted_dir: 【4.x】解密库目录；给了就走「读库」模式
    :param my_wxid:       【4.x】当前登录账号
    :param wx_path:       【4.x】微信数据目录
    :param keys_file:     【4.x】（旧名，兼容保留）密钥文件路径
    :param mode:          【第二步】auto / 3x / 4x
                          - auto：能附加 WeChat.exe 就走 3.x 老逻辑，否则走 4.x 读库
                          - 4x  ：【B 计划】完全不读进程内存，也不要求存在 WeChatWin.dll，
                                  密钥直接来自 --key_file 指定的本地密钥文件
                          - 3x  ：强制老逻辑
    :param key_file:      【第二步·4.x】本地密钥文件，形如 {"库路径": {"enc_key": "64位hex", "salt": "..."}}
    :return: 返回微信信息 [{"pid": pid, "version": version, "account": account,
                          "mobile": mobile, "nickname": nickname, "mail": mail, "wxid": wxid,
                          "key": key, "wx_dir": wx_dir}, ...]
    """
    if WX_OFFS is None:
        WX_OFFS = {}

    mode = (mode or "auto").lower()
    if mode not in ("auto", "3x", "4x"):
        wx_core_loger.warning(f"[-] mode 只支持 auto/3x/4x，收到 {mode}，按 auto 处理")
        mode = "auto"
    key_file = key_file or keys_file

    wechat_pids = []
    result = []

    processes = get_process_list()
    for pid, name in processes:
        if name == "WeChat.exe":
            wechat_pids.append(pid)

    if mode == "4x":
        # 【第二步·B 计划】强制「解密库 + 本地密钥文件」路线：
        # 不扫内存、不要求 WeChatWin.dll，只读 contact 库 + 密钥文件。
        if not decrypted_dir:
            decrypted_dir = _wx4_guess_decrypted_dir()
            if decrypted_dir:
                wx_core_loger.warning(f"[-] 未指定解密库目录(-dd)，自动使用 {decrypted_dir}")
        result = get_wx_info_from_db(decrypted_dir=decrypted_dir, my_wxid=my_wxid, wx_path=wx_path,
                                     key_file=key_file)
    elif mode == "3x" or (mode == "auto" and len(wechat_pids) > 0):
        for pid in wechat_pids:
            rd = get_info_details(pid, WX_OFFS)
            result.append(rd)
        if not result:
            wx_core_loger.error("[-] WeChat No Run")
            return result
    elif decrypted_dir or key_file:
        # 4.x：进程名是 Weixin.exe，而且没有 WeChatWin.dll 这套偏移可用，
        # 所以这里不报「WeChat No Run」直接退出，改成从已解密的库里读信息（完全不读进程内存）。
        wx_core_loger.warning("[-] 未发现微信 3.x 进程（WeChat.exe），改用已解密数据库读取微信信息")
        if not decrypted_dir:
            decrypted_dir = _wx4_guess_decrypted_dir()
        result = get_wx_info_from_db(decrypted_dir=decrypted_dir, my_wxid=my_wxid, wx_path=wx_path,
                                     key_file=key_file)
    else:
        wx_core_loger.error("[-] WeChat No Run")
        return result

    if is_print:
        print("=" * 32)
        if isinstance(result, str):  # 输出报错
            print(result)
        else:  # 输出结果
            if result and isinstance(result[0], dict) and result[0].get("source") == "db":
                print("[+] 数据来源：已解密数据库 + 本地密钥文件（微信 4.x，未读取微信进程内存）")
            for i, rlt in enumerate(result):
                for k, v in rlt.items():
                    if k == "keys" and isinstance(v, dict):
                        print(f"[+] {k:>8}: {len(v)} 个库的密钥（见下方 库路径:密钥 列表）")
                        continue
                    print(f"[+] {k:>8}: {v if v else 'None'}")
                if isinstance(rlt.get("keys"), dict) and rlt["keys"]:
                    print("    {库路径: 密钥}")
                    for db, kk in rlt["keys"].items():
                        print(f"    {db} -> {kk}")
                print(end="-" * 32 + "\n" if i != len(result) - 1 else "")
        print("=" * 32)

    if save_path:
        try:
            infos = json.load(open(save_path, "r", encoding="utf-8")) if os.path.exists(save_path) else []
        except:
            infos = []
        with open(save_path, "w", encoding="utf-8") as f:
            infos += result
            json.dump(infos, f, ensure_ascii=False, indent=4)
    return result


@wx_core_error
```

### 2.4 `cli.py` 顶部新增 1 行 import

```python
from pywxdump import *
import pywxdump
from pywxdump.wx_core.wx_info import read_wx4_keys_file
```

### 2.5 `cli.py` 替换：`MainBiasAddr`（bias 输出密钥字典）

```python
class MainBiasAddr(BaseSubMainClass):
    mode = "bias"
    parser_kwargs = {"help": "获取微信基址偏移"}

    def init_parses(self, parser):
        # 添加 'bias_addr' 子命令解析器
        # 注意：3.x 需要 --mobile/--name/--account 去内存里搜；4.x 完全不需要，
        #       所以这里把 required 去掉，只在真正走 3.x 内存路径时才要求。
        parser.add_argument("--mobile", type=str, help="(3.x)手机号", metavar="", required=False)
        parser.add_argument("--name", type=str, help="(3.x)微信昵称", metavar="", required=False)
        parser.add_argument("--account", type=str, help="(3.x)微信账号", metavar="", required=False)
        parser.add_argument("--key", type=str, metavar="", help="(可选)密钥")
        parser.add_argument("--db_path", type=str, metavar="", help="(可选)已登录账号的微信文件夹路径")
        parser.add_argument("-vlp", '--WX_OFFS_PATH', type=str, metavar="",
                            help="(可选)微信版本偏移文件路径,如有，则自动更新",
                            default=None)
        parser.add_argument("-dd", "--decrypted_dir", type=str, metavar="", default=None,
                            help="(4.x)已解密数据库目录；给了就完全跳过内存扫描，直接从库里读信息")
        parser.add_argument("--key_file", "--keys_file", dest="key_file", type=str, metavar="", default=None,
                            help="(4.x)本地密钥文件路径，形如 {库路径: {enc_key, salt}}，如 all_keys.json")
        parser.add_argument("--mode", dest="wx_mode", type=str, metavar="", default="auto",
                            choices=["auto", "3x", "4x"],
                            help="(4.x)auto/3x/4x；4x=完全不读进程内存，改用解密库+本地密钥文件")
        parser.add_argument("--my_wxid", type=str, metavar="", default=None, help="(4.x)当前登录账号 wxid")
        parser.add_argument("--wx_path", type=str, metavar="", default=None,
                            help="(4.x)微信数据目录，如 D:\\xwechat_files\\wxid_xxx_abcd")
        return parser

    def run(self, args):
        print(f"[*] PyWxDump v{pywxdump.__version__}")
        # 从命令行参数获取值
        mobile = args.mobile
        name = args.name
        account = args.account
        key = args.key
        db_path = args.db_path
        vlp = args.WX_OFFS_PATH
        mode = (getattr(args, "wx_mode", "auto") or "auto").lower()
        decrypted_dir = getattr(args, "decrypted_dir", None)
        my_wxid = getattr(args, "my_wxid", None)
        wx_path = getattr(args, "wx_path", None)
        key_file = getattr(args, "key_file", None)

        if mode == "4x" or key_file:
            # ---------- 4.x（B 计划）：密钥来自本地密钥文件，完全跳过内存扫描 ----------
            print("[*] 4.x 模式：跳过内存扫描，密钥来自本地密钥文件")
            keymap = read_wx4_keys_file(key_file) if key_file else {}
            if not keymap and decrypted_dir:
                for cand in ("all_keys.json", "keys.json"):
                    p = os.path.join(decrypted_dir, cand)
                    keymap = read_wx4_keys_file(p)
                    if keymap:
                        key_file = p
                        break
            if not keymap:
                print("[-] 没有从密钥文件里读到 4.x 形态的密钥")
                print('[-] 需要的格式：{"库路径": {"enc_key": "<64位hex>", "salt": "<32位hex>"}}')
                print(f"[-] 当前 --key_file：{key_file}")
                return None
            print(f"[+] 密钥文件：{key_file}")
            print(f"[*] 共读到 {len(keymap)} 个库的密钥")
            print("[*] 说明：4.x 没有 WeChatWin.dll，也不写 WX_OFFS.json；bias 输出改为 库路径:密钥")
            print("{库路径: 密钥}")
            print(keymap)
            return keymap

        if decrypted_dir:
            # ---------- 4.x：跳过内存扫描，直接读已解密数据库 ----------
            print("[*] 4.x 模式：跳过内存扫描，从已解密数据库读取微信信息")
            infos = get_wx_info_from_db(decrypted_dir=decrypted_dir, my_wxid=my_wxid,
                                        wx_path=wx_path, key_file=key_file, is_print=True)
            if not infos:
                return None
            print("[*] 说明：4.x 没有 WeChatWin.dll，3.x 的 base bias（内存偏移）机制不适用，")
            print("[*]       因此这里【不会写入/覆盖】WX_OFFS.json 里的偏移记录。")
            print("[*]       想拿 4.x 的密钥列表，请加 --key_file <all_keys.json>")
            return {infos[0].get("version") or "4.x": []}

        # ---------- 3.x：原逻辑完全不变（强制 mode="3x"，不再回落到 4.x 内存扫描）----------
        if not (mobile and name and account):
            print("[-] 3.x 模式需要同时提供 --mobile --name --account")
            print("[-] 若微信是 4.x：请用 wxdump bias --mode 4x --key_file <all_keys.json>")
            print("[-]     或 wxdump bias -dd <解密库目录>（只读已解密数据库）")
            return None
        # 调用 run 函数，并传入参数
        rdata = BiasAddr(account, mobile, name, key, db_path).run(True, vlp, mode="3x")
        if rdata is None:
            print("[-] 3.x 模式未成功：没找到 WeChat.exe（微信 3.x 进程）")
            print("[-] 微信是 4.x 的话，请用：wxdump bias --mode 4x --key_file <all_keys.json>")
        return rdata
```

### 2.6 `cli.py` 替换：`MainWxInfo`（info 接受 --key_file）

```python
class MainWxInfo(BaseSubMainClass):
    mode = "info"
    parser_kwargs = {"help": "获取微信信息"}

    def init_parses(self, parser):
        # 添加 'wx_info' 子命令解析器
        parser.add_argument("-vlp", '--WX_OFFS_PATH', metavar="", type=str,
                            help="(可选)微信版本偏移文件路径", default=WX_OFFS_PATH)
        parser.add_argument("-s", '--save_path', metavar="", type=str, help="(可选)保存路径【json文件】")
        parser.add_argument("--mode", dest="wx_mode", type=str, metavar="", default="auto",
                            choices=["auto", "3x", "4x"],
                            help="(4.x)auto/3x/4x；4x=完全不读进程内存，改用解密库+本地密钥文件")
        parser.add_argument("-dd", "--decrypted_dir", type=str, metavar="", default=None,
                            help="(4.x)已解密数据库目录；4.x 用这个，完全不读微信进程内存")
        parser.add_argument("--key_file", "--keys_file", dest="key_file", type=str, metavar="", default=None,
                            help="(4.x)本地密钥文件路径，形如 {库路径: {enc_key, salt}}，如 all_keys.json")
        parser.add_argument("--my_wxid", type=str, metavar="", default=None, help="(4.x)当前登录账号 wxid")
        parser.add_argument("--wx_path", type=str, metavar="", default=None,
                            help="(4.x)微信数据目录，如 D:\\xwechat_files\\wxid_xxx_abcd")
        return parser

    def run(self, args):
        print(f"[*] PyWxDump v{pywxdump.__version__}")
        # 读取微信各版本偏移
        path = args.WX_OFFS_PATH
        save_path = args.save_path
        mode = (getattr(args, "wx_mode", "auto") or "auto").lower()
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                WX_OFFS = json.load(f)
        else:
            if mode != "4x":
                print(f"[-] 偏移文件不存在/未指定：{path}（4.x 不需要偏移文件，可忽略）")
            WX_OFFS = {}
        result = get_wx_info(WX_OFFS, True, save_path,
                             decrypted_dir=getattr(args, "decrypted_dir", None),
                             my_wxid=getattr(args, "my_wxid", None),
                             wx_path=getattr(args, "wx_path", None),
                             key_file=getattr(args, "key_file", None),
                             mode=mode)  # 读取微信信息
        return result
```

## 3. 怎么替换进去

这两个文件**已经由我直接落盘并跑通测试**，正常不需要你手动贴：
- 已改文件：`D:\PyWxDump_Source\pywxdump\wx_core\wx_info.py`、`D:\PyWxDump_Source\pywxdump\cli.py`
- 语法检查：两个都 `py_compile OK`
- 手工重做/回滚：原版未改动的副本在安装包里，拷回来即可
  ```powershell
  copy "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\Lib\site-packages\pywxdump\wx_core\wx_info.py" "D:\PyWxDump_Source\pywxdump\wx_core\wx_info.py"
  copy "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\Lib\site-packages\pywxdump\cli.py" "D:\PyWxDump_Source\pywxdump\cli.py"
  ```

## 4. 怎么跑（重要：命令形式）

**别直接敲 `wxdump info`**——系统里的 `wxdump.exe` 指向的是 site-packages 里的**原版**（`hasattr(pywxdump,'get_wx_info_from_db')` = False），拿不到本次改动。两种能生效的形式（实测都通）：

**形式一（推荐，管理员 PowerShell）**
```powershell
cd D:\PyWxDump_Source
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -m pywxdump info --mode 4x --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json" -dd D:\decrypted_wx_db
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -m pywxdump bias --mode 4x --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json"
```

**形式二（想继续用 `wxdump` 这个命令）**
```powershell
$env:PYTHONPATH = "D:\PyWxDump_Source"
wxdump info --mode 4x --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json"
wxdump bias --mode 4x --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json"
```

参数说明：
- `--mode 4x`：强制 4.x 路线（不扫内存）。不写也行——只要给了 `--key_file` 或 `-dd` 就自动走 4.x。
- `--key_file`（等价旧名 `--keys_file`）：密钥文件；格式 `{"库路径": {"enc_key": "<64位hex>", "salt": "<32位hex>"}}`。
- `-dd`：已解密库目录；不写的话会自动去找 `D:\decrypted_wx_db`。
- `--my_wxid` / `--wx_path`：可选，用于精确指定账号与数据目录。
- `-s <json>`：（info）把结果存成 json。

## 5. 验收清单（含本次实测结果）

| # | 验收项 | 判定依据 | 实测 |
| ---: | --- | --- | --- |
| 1 | `info --mode 4x --key_file ... -dd ...` 不报错、不扫内存 | 输出无 `内存扫描` | ✅ 无 |
| 2 | 输出昵称/微信号/wxid | 柳岸 / AbnerUP / <本人wxid> | ✅ |
| 3 | `rd['key']` 被填上密钥 | `key:` 是 64 位 hex | ✅ |
| 4 | 密钥列表完整 | `key_count: 29` + `keys` 列表 | ✅ 29 |
| 5 | 代表 key 选的是主库 | `key_db: message\message_0.db` | ✅ |
| 6 | `bias --mode 4x --key_file ...` 输出字典 | `{库路径: 密钥}` 29 条 | ✅ |
| 7 | `bias` 不再输出 `[0,0,0,0,0]` | 输出里没有 5 个零 | ✅ |
| 8 | 不再要求 `WeChatWin.dll` | 机器上只有 `Weixin.exe` 仍成功 | ✅ |
| 9 | 不写 `WX_OFFS.json` | `bias` 明确声明不写 | ✅ |
| 10 | 没有 `-dd` 也能跑 | 自动使用 `D:\decrypted_wx_db` | ✅ |
| 11 | 密钥文件不存在时给明确报错、不崩 | 打印所需格式与当前路径 | ✅ |
| 12 | 3.x 老路径零回归 | 旧 62 项套件 | ✅ 62/62 |
| 13 | 旧参数名 `--keys_file` 仍可用 | `key_count: 29` | ✅ |
| 14 | 没微信 3.x 时 `bias` 不误扫 4.x 内存 | 立刻提示改用 `--mode 4x` | ✅ |

## 6. 一处语义变化（说明，不是回归）

- **`wxdump bias -dd <目录>`（不给 key 文件）**：以前会打印 `{版本号: [0,0,0,0,0]}`，现在改为打印微信信息 + 提示"想拿密钥请加 `--key_file`"，返回 `{版本号: []}`。这正是本次要求（bias 不再输出 5 个偏移）。
- 原 `wx4_step2_verify.py` 里与 bias 相关的 4 条断言**仍然全部通过**（已实测），因为它只检查"跳过内存扫描 / 柳岸 / 不写 WX_OFFS"这些字样。
- `bias` 走 3.x 分支时已固定 `mode="3x"`：不会再回落到 4.x 内存扫描（这是上一轮实测发现的问题，已修）。

## 7. 下一步

第二步到此可验收。如果要继续，候选方向（你定）：
1. 把 `--key_file` 收进 `wxdump ui` / `wxdump api`，让 UI 启动时自动加载密钥文件（UI 目前读的是 `-dd` 解密库，已能看全部聊天记录）。
2. `info` 里的 `mobile`/`mail` 在 4.x 下必然为空——若你要，"从 contact.db 里挖其他字段"我可以再补一版。
