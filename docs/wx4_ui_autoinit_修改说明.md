# 微信 4.1.15.13 适配 · 第三步：`wxdump ui` / `wxdump api` 启动即全自动

> 共识：UI 启动时自动完成「读密钥 → 定位账号目录 → 解密 → 加载会话」，**不需要手填任何路径**；
> `--mode 3x` 时行为与改造前完全一致。
> 本步只动 4 个文件 + 新增 1 个文件，全部在 `D:\PyWxDump_Source\` 内。

## 0. 一句话结论（都是实测）

```
python -m pywxdump ui -p 5010 --noOpenBrowser --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json"
```

启动横幅（真实输出）：

```
[*] PyWxDump v3.1.46
[+] 微信 4.x 自动准备：读密钥文件 → 定位账号目录 → 检查解密库……
[+] 密钥文件：C:\Users\Administrator\.wechat-cli\all_keys.json（29 把）
[+] 候选账号目录 14 个，正在用密钥校验……
    候选账号 <本人wxid>_fed4：HMAC 校验 3/3 命中      ← 其余 13 个账号 0 命中
[+] 账号目录：D:\xwechat_files\<本人wxid>_fed4（HMAC 校验 3/3 命中）
[+] 解密库目录：D:\decrypted_wx_db（已存在，直接复用）
    本人 wxid 推断：<本人wxid>（contact 命中 1; Name2Id 命中 1）
[+] 4.x 自动初始化完成：wxid=<本人wxid>
    原始库目录：D:\xwechat_files\<本人wxid>_fed4\db_storage
    解密库目录：D:\decrypted_wx_db
    主库      ：D:\decrypted_wx_db\message_0_decrypted.db
    已解密 0 个 / 复用 29 个 / 过期未刷新 13 个（加 --force_decrypt 可刷新）
[+] UI 会直接加载该账号的全部会话，无需在界面上填任何路径
[+] 请使用浏览器访问 http://127.0.0.1:5010/ 查看聊天记录
```

界面侧实测（直接打接口，等价于页面首屏要做的事）：

| 接口 | 返回 | 说明 |
| --- | --- | --- |
| `/api/rs/is_init` | `true` | 前端据此**不再跳 /db_init**，也就不再要求填 merge_all.db / 微信文件夹路径 |
| `/api/rs/mywxid` | `<本人wxid>` | 账号已自动选定 |
| `/api/rs/user_session_list` | **549** 个会话 | 200 ms |
| `/api/rs/user_list` | **9715** 个联系人 | 2.2 s |
| `/api/ls/wxinfo` | version `4.1.15.13` / account `AbnerUP` / nickname `柳岸` / key 64 位 hex / **keys 29 把** | 「微信信息」页也有数据了 |
| `/api/rs/msg_list` | 群聊 `<群ID>` 取到 10 条 | 点开会话能读消息 |

`--mode 3x` 对照实测（全新工作目录）：启动日志里**没有任何 4.x 准备字样**，`/api/rs/is_init` 返回 `false`（和改造前一模一样，前端照旧引导手动初始化）。

## 1. 改了哪些文件、哪些函数

| 文件 | 位置 | 性质 |
| --- | --- | --- |
| `wx_core/wx4_prepare.py` | 整个文件（**新增**，479 行） | 4.x 库准备：定位账号目录 / 读密钥 / 增量解密 / 推断本人 wxid |
| `api/__init__.py` | `start_server()` 签名（第 213–229 行）+ 启动时自动准备块（第 274–316 行） | 新增 `key_file/mode/decrypted_dir/no_decrypt/force_decrypt` 参数，并写 conf |
| `cli.py` | `MainUi`（第 406–446 行）、`MainApi`（第 449–486 行） | 两个子命令都加上对应参数并传下去 |
| `api/remote_server.py` | `is_init()`（第 34–55 行） | 让「已初始化」判据不只看 wxid 个数 |
| `api/local_server.py` | `/wxinfo` 取 key_file（第 243–259 行） | 把启动时写下的密钥文件传给 `get_wx_info` |

**关键设计点**

1. **账号目录靠密码学选，不靠目录名/时间**：你这台机器 `D:\xwechat_files` 下有 **14 个**账号目录，只看路径根本分不清。这里用「密钥文件里的 key 能不能通过原始库第 1 页的 HMAC-SHA512 校验」来打分，只有 `<本人wxid>_fed4` 命中 3/3，其余全 0。
2. **解密算法与你自己那份 `batch_decrypt.py` 完全同源**（SQLCipher 4：AES-256-CBC、页 4096、reserve 80 = IV16+HMAC64、`salt^0x3a` + PBKDF2-HMAC-SHA512 2 轮）。实测：把 `message_8.db` 解到临时目录，和 `D:\decrypted_wx_db\message_8_decrypted.db` **逐字节完全一致（860160 字节）**，且能正常打开（Name2Id 2 行、`Msg_03b7c4…` 4259 行）。
3. **写盘先写 `.part` 再原子替换**，不会留半个文件冒充可用库；不删任何东西。
4. **默认只补缺失的库**：你现在的 29 个解密库都在，但其中 **13 个比原始库旧**（差约 3 小时）。默认不重解（避免每次启动都重写 1GB+），只提示；要刷新就加 `--force_decrypt`。
5. **`--mode` 内部叫 `dest="wx_mode"`**：因为 `cli.py` 底部是 `models[args.mode].run(args)`，子命令里再定义同名 `--mode` 会把 `args.mode` 覆盖掉。对外仍然是 `--mode 3x`。

## 2. 代码块

### 2.1 新增文件 `pywxdump/wx_core/wx4_prepare.py`（全文件）

```python
# -*- coding: utf-8 -*-#
# -------------------------------------------------------------------------------
# 【微信 4.x 改造 · 第三步】4.x 库自动准备（供 `wxdump ui` / `wxdump api` 启动时调用）
#
#   流程：定位账号目录 → 读本地密钥文件 → 增量解密 → 汇总（主库 / 解密目录 / 本人 wxid）
#
#   设计要点：
#     · 本模块只服务 4.x。3.x 路径完全不受影响（调用方在 mode=3x 时不要调用它）。
#     · 密钥全部来自本地 JSON 密钥文件，全程不读进程内存、不做 DLL 注入。
#     · 解密算法与 SQLCipher 4 一致（微信 4.x）：AES-256-CBC + HMAC-SHA512，
#       页大小 4096、reserve 80（IV 16 + HMAC 64）；与用户已在用的 batch_decrypt.py 同源。
#     · 增量：默认只补「缺失」的库；已存在但比原始库旧的库只在 --force_decrypt 时重解。
#     · 不删任何文件；写盘一律先写 .part 再原子替换，避免半个文件被当成可用库。
# -------------------------------------------------------------------------------
import glob
import hashlib
import hmac as hmac_mod
import json
import os
import re
import struct

from Crypto.Cipher import AES

# ---------------- SQLCipher 4（微信 4.x）参数 ----------------
PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = 80  # IV(16) + HMAC(64)
SQLITE_HDR = b"SQLite format 3\x00"

# 选账号目录时用于 HMAC 校验的库（相对 db_storage）
PROBE_FILES = ("contact\\contact.db", "session\\session.db", "message\\message_0.db")

# 目录名形如 wxid_xxx_fed4（真实 wxid + 4 位十六进制后缀）
ACCOUNT_RE = re.compile(r"^(wxid_[A-Za-z0-9]{4,})_[0-9a-fA-F]{4}$")


# ============================= 一、密钥文件 =============================

def _default_key_file_candidates():
    """4.x 密钥文件的默认候选位置（先环境变量，再用户目录下的 .wechat-cli）"""
    cands = []
    env = os.getenv("PYWXDUMP_WX4_KEY_FILE")
    if env:
        cands.append(env)
    cands.append(os.path.join(os.path.expanduser("~"), ".wechat-cli", "all_keys.json"))
    cands.append(r"C:\Users\Administrator\.wechat-cli\all_keys.json")
    return cands


def default_key_file():
    """返回第一个真实存在的默认密钥文件；都不存在时返回第一个候选（调用方据此报错）"""
    cands = _default_key_file_candidates()
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return cands[0] if cands else ""


def read_keys(key_file):
    """
    读密钥文件，返回 {相对库路径: enc_key}。

    认两种形态：
      {"message\\message_0.db": {"enc_key": "<64hex>", "salt": "<32hex>"}, ...}   ← all_keys.json
      {"message\\message_0.db": "<64hex>", ...}
    """
    with open(key_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    if not isinstance(data, dict):
        return out
    for rel, val in data.items():
        if isinstance(val, dict):
            k = val.get("enc_key") or val.get("key") or ""
        elif isinstance(val, str):
            k = val
        else:
            k = ""
        k = str(k).strip()
        if len(k) == 64:
            out[str(rel).replace("/", "\\")] = k
    return out


# ============================= 二、解密原语 =============================

def derive_mac_key(enc_key, salt):
    """HMAC 密钥 = PBKDF2-HMAC-SHA512(enc_key, salt ^ 0x3a, 2, 32)"""
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def verify_page1_hmac(enc_key, page1):
    """校验第 1 页 HMAC：能过说明这把密钥就是这个库的（用于选账号目录/判断密钥有效性）"""
    if len(page1) < PAGE_SZ:
        return False
    salt = page1[:SALT_SZ]
    mac_key = derive_mac_key(enc_key, salt)
    data = page1[SALT_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
    stored = page1[PAGE_SZ - HMAC_SZ: PAGE_SZ]
    hm = hmac_mod.new(mac_key, data, hashlib.sha512)
    hm.update(struct.pack("<I", 1))
    return hm.digest() == stored


def decrypt_page(enc_key, page_data, pgno):
    """解密单页，输出 4096 字节标准 SQLite 页（reserve 位置补零）"""
    iv = page_data[PAGE_SZ - RESERVE_SZ: PAGE_SZ - RESERVE_SZ + IV_SZ]
    cipher = AES.new(enc_key, AES.MODE_CBC, iv)
    if pgno == 1:
        decrypted = cipher.decrypt(page_data[SALT_SZ: PAGE_SZ - RESERVE_SZ])
        return bytes(SQLITE_HDR + decrypted + b"\x00" * RESERVE_SZ)
    decrypted = cipher.decrypt(page_data[: PAGE_SZ - RESERVE_SZ])
    return decrypted + b"\x00" * RESERVE_SZ


def decrypt_db_file(src, dst, enc_key_hex, log=None):
    """
    解密一个库文件：src(加密) -> dst(标准 sqlite)。返回页数。
    先写 dst + ".part" 再原子替换，失败不会留下半成品。
    """
    enc_key = bytes.fromhex(enc_key_hex)
    with open(src, "rb") as fin:
        page1 = fin.read(PAGE_SZ)
        if len(page1) < PAGE_SZ:
            raise ValueError("文件小于 1 页，不是有效的加密库")
        if not verify_page1_hmac(enc_key, page1):
            raise ValueError("Page1 HMAC 校验失败（密钥不匹配或库文件已变化）")

        total_pages = (os.path.getsize(src) + PAGE_SZ - 1) // PAGE_SZ
        tmp = dst + ".part"
        buf = bytearray()
        with open(tmp, "wb") as fout:
            for pgno in range(1, total_pages + 1):
                page = page1 if pgno == 1 else fin.read(PAGE_SZ)
                if len(page) < PAGE_SZ:
                    page = page + b"\x00" * (PAGE_SZ - len(page))
                buf += decrypt_page(enc_key, page, pgno)
                if len(buf) >= (1 << 20):
                    fout.write(buf)
                    del buf[:]
                if log and total_pages > 4096 and pgno % 4096 == 0:
                    log(f"       … {pgno}/{total_pages} 页")
            if buf:
                fout.write(buf)
    os.replace(tmp, dst)
    return total_pages


# ============================= 三、账号目录 =============================

def _wx4_roots():
    """4.x 数据根目录候选：注册表 → 各盘 xwechat_files → 我的文档"""
    roots = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin", 0, winreg.KEY_READ) as k:
            for name in ("FileSavePath", "SavePath"):
                try:
                    v, _ = winreg.QueryValueEx(k, name)
                except OSError:
                    continue
                if isinstance(v, str) and v and os.path.isdir(v):
                    roots.append(v)
    except Exception:
        pass
    for drive in ("C:", "D:", "E:", "F:", "G:"):
        p = os.path.join(drive + os.sep, "xwechat_files")
        if os.path.isdir(p):
            roots.append(p)
    p = os.path.join(os.path.expanduser("~"), "Documents", "xwechat_files")
    if os.path.isdir(p):
        roots.append(p)
    out = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out


def find_account_dirs(wx_path=None):
    """
    找出所有「含 db_storage 的账号目录」。返回 [(账号目录, db_storage 路径), ...]

    wx_path 三种给法都可以：账号目录本身 / db_storage / 上层根目录（如 D:\\xwechat_files）
    """
    cands = []

    def add_account(p):
        ds = os.path.join(p, "db_storage")
        if os.path.isdir(ds) and (p, ds) not in cands:
            cands.append((p, ds))

    if wx_path:
        wp = os.path.normpath(wx_path)
        if os.path.basename(wp).lower() == "db_storage" and os.path.isdir(wp):
            cands.append((os.path.dirname(wp), wp))
        elif os.path.isdir(wp):
            add_account(wp)                     # 账号目录
            for name in sorted(os.listdir(wp)):  # 上层根目录
                sub = os.path.join(wp, name)
                if os.path.isdir(sub):
                    add_account(sub)
    if not cands:
        for root in _wx4_roots():
            for name in sorted(os.listdir(root)):
                sub = os.path.join(root, name)
                if os.path.isdir(sub):
                    add_account(sub)
    return cands


def score_account(account_dir, keys):
    """用「密钥能否通过 page1 HMAC」给账号目录打分（命中越多越是这个密钥文件对应的账号）"""
    hits, checked = 0, 0
    ds = os.path.join(account_dir, "db_storage")
    for rel in PROBE_FILES:
        key = keys.get(rel)
        if not key:
            continue
        src = os.path.join(ds, rel)
        if not os.path.isfile(src):
            continue
        checked += 1
        try:
            with open(src, "rb") as f:
                page1 = f.read(PAGE_SZ)
            if verify_page1_hmac(bytes.fromhex(key), page1):
                hits += 1
        except Exception:
            pass
    return hits, checked


def pick_account(accounts, keys, log=None):
    """
    选出密钥文件对应的账号目录。返回 (账号目录, db_storage, 详情字符串)
    判据是密码学校验（page1 HMAC），不是目录名/时间，避免多账号误选。
    """
    best, best_hits, best_detail = None, 0, ""
    for account_dir, _ds in accounts:
        hits, checked = score_account(account_dir, keys)
        if log:
            log(f"    候选账号 {os.path.basename(account_dir)}：HMAC 校验 {hits}/{checked} 命中")
        if hits > best_hits:
            best, best_hits = account_dir, hits
            best_detail = f"HMAC 校验 {hits}/{checked} 命中"
    if best and best_hits > 0:
        return best, os.path.join(best, "db_storage"), best_detail
    return None, "", ""


# ============================= 四、输出目录 / 本人 wxid =============================

def choose_out_dir(decrypted_dir=None, work_path=None, account_dir=""):
    """
    解密产物目录，候选顺序（取第一个已存在且含 message_*_decrypted.db 的；都不满足就用第 2 个新建）：
      1. --decrypted_dir 指定
      2. <work_path>/decrypted_wx4/<账号目录名>
      3. D:\\decrypted_wx_db        （本机已有的解密库，复用可免去重复解密）
    """
    def has_msg(p):
        return bool(p) and os.path.isdir(p) and glob.glob(os.path.join(p, "message_*_decrypted.db"))

    name = os.path.basename(os.path.normpath(account_dir)) if account_dir else "default"
    work_out = os.path.join(work_path, "decrypted_wx4", name) if work_path else ""
    cands = [decrypted_dir, work_out, r"D:\decrypted_wx_db"]
    for p in cands[:2]:
        if has_msg(p):
            return p, True
    for p in cands[2:]:
        if has_msg(p):
            return p, True
    out = decrypted_dir or work_out
    return out, False


def guess_my_wxid(account_dir, out_dir, log=None):
    """
    推本人 wxid：账号目录名 wxid_xxx_4hex → wxid_xxx，并用解密库里的旁证校验
    （contact 表里有这条联系人 / message_0 的 Name2Id 里有它）。
    推不出来就返回 ""，此时 UI 能看会话但「我发的」判定会不准。
    """
    import sqlite3
    name = os.path.basename(os.path.normpath(account_dir))
    m = ACCOUNT_RE.match(name)
    if not m:
        return "", f"账号目录名 {name!r} 不是 wxid_xxx_4hex 形式，无法推断"
    cand = m.group(1)

    proofs = []
    fp = os.path.join(out_dir, "contact_decrypted.db")
    if os.path.isfile(fp):
        try:
            con = sqlite3.connect(f"file:{fp}?mode=ro", uri=True)
            n = con.execute("SELECT count(*) FROM contact WHERE username=?", (cand,)).fetchone()[0]
            con.close()
            proofs.append(f"contact 命中 {n}")
        except Exception as e:
            proofs.append(f"contact 查询失败 {e}")
    fp = os.path.join(out_dir, "message_0_decrypted.db")
    if os.path.isfile(fp):
        try:
            con = sqlite3.connect(f"file:{fp}?mode=ro", uri=True)
            n = con.execute("SELECT count(*) FROM Name2Id WHERE user_name=?", (cand,)).fetchone()[0]
            con.close()
            proofs.append(f"Name2Id 命中 {n}")
        except Exception as e:
            proofs.append(f"Name2Id 查询失败 {e}")
    if log and proofs:
        log(f"    本人 wxid 推断：{cand}（{'; '.join(proofs)}）")
    return cand, "; ".join(proofs)


# ============================= 五、主流程 =============================

def _resolve_src(db_storage, rel):
    """把密钥文件里的相对路径对到真实文件（先按相对路径，再按文件名，最后浅层 glob）"""
    p = os.path.join(db_storage, rel)
    if os.path.isfile(p):
        return p
    base = os.path.basename(rel)
    p = os.path.join(db_storage, base)
    if os.path.isfile(p):
        return p
    hits = glob.glob(os.path.join(db_storage, "*", base))
    return hits[0] if hits else ""


def decode_out_name(rel):
    base = os.path.basename(rel)
    return base[:-3] + "_decrypted.db" if base.lower().endswith(".db") else base + "_decrypted.db"


def prepare_wx4(key_file=None, wx_path=None, decrypted_dir=None, my_wxid=None,
                no_decrypt=False, force_decrypt=False, work_path=None, log=print):
    """
    4.x UI 启动前的自动准备。返回 dict：

      ok             : 是否拿到了可用的主库
      my_wxid        : 本人 wxid（可能为空字符串）
      wx_path        : 账号目录（给 conf 的 wx_path 用）
      db_storage     : 原始（加密）库目录
      decrypted_dir  : 解密库目录
      primary_db     : 主库（message_0_decrypted.db 优先）
      key_file/key_count
      decrypted/reused/stale/not_found/failed: 各文件清单
      msg            : 人类可读的结论（失败原因或摘要）
    """
    ret = {"ok": False, "msg": "", "my_wxid": "", "wx_path": "", "db_storage": "",
           "decrypted_dir": "", "primary_db": "", "key_file": "", "key_count": 0,
           "decrypted": [], "reused": [], "stale": [], "not_found": [], "failed": [],
           "account_detail": ""}

    # 1) 密钥文件
    kf = key_file or default_key_file()
    ret["key_file"] = kf
    if not kf or not os.path.isfile(kf):
        ret["msg"] = f"找不到密钥文件：{kf}（可用 --key_file 指定）"
        return ret
    try:
        keys = read_keys(kf)
    except Exception as e:
        ret["msg"] = f"密钥文件解析失败：{kf}：{e}"
        return ret
    if not keys:
        ret["msg"] = f"密钥文件里没读到有效密钥（需要 64 位 hex）：{kf}"
        return ret
    ret["key_count"] = len(keys)
    log(f"[+] 密钥文件：{kf}（{len(keys)} 把）")

    # 2) 账号目录（用密钥做 page1 HMAC 校验来定位）
    accounts = find_account_dirs(wx_path)
    if not accounts:
        ret["msg"] = "没找到任何含 db_storage 的 4.x 账号目录（可用 --wx_path 指定）"
        return ret
    log(f"[+] 候选账号目录 {len(accounts)} 个，正在用密钥校验……")
    account_dir, db_storage, detail = pick_account(accounts, keys, log=log)
    if not account_dir:
        ret["msg"] = (f"{len(accounts)} 个候选账号目录都没有通过 page1 HMAC 校验，"
                      f"密钥文件可能与当前数据不匹配")
        return ret
    ret["account_detail"] = detail
    ret["wx_path"] = account_dir
    ret["db_storage"] = db_storage
    log(f"[+] 账号目录：{account_dir}（{detail}）")

    # 3) 解密产物目录
    out_dir, reused = choose_out_dir(decrypted_dir, work_path, account_dir)
    if not out_dir:
        ret["msg"] = "无法确定解密产物目录（可用 --decrypted_dir 指定）"
        return ret
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    ret["decrypted_dir"] = out_dir
    log(f"[+] 解密库目录：{out_dir}" + ("（已存在，直接复用）" if reused else "（新建）"))

    # 4) 本人 wxid
    if my_wxid:
        mid, why = my_wxid, "由 --my_wxid 指定"
    else:
        mid, why = guess_my_wxid(account_dir, out_dir, log=log)
    ret["my_wxid"] = mid
    if not mid:
        log(f"[!] 未能推断本人 wxid：{why}（会话能看，但「我发的/对方发的」可能不准）")

    # 5) 逐个库处理
    for rel in sorted(keys.keys()):
        src = _resolve_src(db_storage, rel)
        if not src:
            ret["not_found"].append(rel)
            continue
        dst = os.path.join(out_dir, decode_out_name(rel))
        try:
            if no_decrypt:
                if os.path.isfile(dst):
                    ret["reused"].append(rel)
                else:
                    ret["not_found"].append(rel)
                continue

            need = force_decrypt or (not os.path.isfile(dst)) or os.path.getsize(dst) == 0
            if not need:
                if os.path.getmtime(dst) >= os.path.getmtime(src):
                    ret["reused"].append(rel)
                else:
                    ret["stale"].append(rel)
                    ret["reused"].append(rel)
                continue

            size_mb = os.path.getsize(src) / 1024 / 1024
            log(f"    解密 {rel}（{size_mb:.1f} MB）……")
            t0 = os.path.getmtime(src)
            pages = decrypt_db_file(src, dst, keys[rel], log=log)
            # 与原始库时间对齐，便于下次判断新鲜度
            try:
                os.utime(dst, (t0, t0))
            except Exception:
                pass
            ret["decrypted"].append(rel)
            log(f"      完成（{pages} 页）")
        except Exception as e:
            ret["failed"].append(f"{rel}: {e}")

    # 6) 主库
    msg_dbs = sorted(glob.glob(os.path.join(out_dir, "message_[0-9]*_decrypted.db")))
    for cand in [os.path.join(out_dir, "message_0_decrypted.db")] + msg_dbs:
        if os.path.isfile(cand) and os.path.getsize(cand) > 0:
            ret["primary_db"] = cand
            break

    if ret["primary_db"]:
        ret["ok"] = True
        ret["msg"] = (f"已解密 {len(ret['decrypted'])} 个 / 复用 {len(ret['reused'])} 个"
                      + (f" / 过期未刷新 {len(ret['stale'])} 个（加 --force_decrypt 可刷新）" if ret["stale"] else "")
                      + (f" / 失败 {len(ret['failed'])} 个" if ret["failed"] else ""))
    else:
        ret["msg"] = (f"解密目录 {out_dir} 里没有可用的 message_*_decrypted.db"
                      + (f"；失败 {len(ret['failed'])} 个：{ret['failed'][:3]}" if ret["failed"] else ""))
    return ret


if __name__ == "__main__":  # 自测：python -m pywxdump.wx_core.wx4_prepare
    import sys
    args = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    r = prepare_wx4(key_file=args.get("key_file"), wx_path=args.get("wx_path"),
                    decrypted_dir=args.get("decrypted_dir"), my_wxid=args.get("my_wxid"),
                    force_decrypt=args.get("force_decrypt") == "1",
                    no_decrypt=args.get("no_decrypt") == "1")
    print("-" * 80)
    for k in ("ok", "msg", "my_wxid", "wx_path", "db_storage", "decrypted_dir", "primary_db",
              "key_file", "key_count", "account_detail"):
        print(f"  {k}: {r[k]}")
    for k in ("decrypted", "reused", "stale", "not_found", "failed"):
        print(f"  {k}({len(r[k])}): {r[k][:8]}")
```

### 2.2 `pywxdump/api/__init__.py`：`start_server()` 新签名

```python
def start_server(port=5000, online=False, debug=False, isopenBrowser=True,
                 merge_path="", wx_path="", my_wxid="", kill_port=False,
                 key_file="", mode="auto", decrypted_dir="",
                 no_decrypt=False, force_decrypt=False):
    """
    启动flask
    :param port:  端口号
    :param online:  是否在线查看(局域网查看)
    :param debug:  是否开启debug模式
    :param isopenBrowser:  是否自动打开浏览器
    :param key_file:  【4.x】本地密钥文件（默认自动找 ~/.wechat-cli/all_keys.json）
    :param mode:      【4.x】auto（默认，自动准备） / 3x（完全走 3.x 老逻辑，不做 4.x 准备）
    :param decrypted_dir: 【4.x】解密产物目录（默认复用已有解密库或 work_path/decrypted_wx4）
    :param no_decrypt:     【4.x】完全不解密，只用 decrypted_dir 里现成的库
    :param force_decrypt:  【4.x】把已存在但比原始库旧的库也重新解密一遍
    :return:
    """
```

### 2.3 `pywxdump/api/__init__.py`：新增「启动即自动准备」块

（位置：紧跟原来的 `if merge_path and os.path.exists(merge_path):` 块之后，`if is_port_in_use(...)` 之前）

```python
    # ---------------- 【4.x 改造 · 第三步】启动即自动准备 ----------------
    #   读本地密钥文件 → 用密钥 page1 HMAC 校验定位账号目录 → 增量解密 → 写好 conf。
    #   写完 conf 后：
    #     · /api/rs/is_init 会返回 True，前端不再跳「请先初始化数据」，也不需要填
    #       merge_all.db / 微信文件夹路径，直接进会话页；
    #     · /api/rs/* 这些接口都按 conf 里的 last + db_config 取库，这里已经指向 4.x 解密库。
    #   mode=3x 时整块跳过，3.x 老逻辑（上面的 merge_path / 界面手动初始化）完全不变。
    if str(mode).lower() not in ("3x", "3"):
        try:
            from pywxdump.wx_core.wx4_prepare import prepare_wx4
            print("[+] 微信 4.x 自动准备：读密钥文件 → 定位账号目录 → 检查解密库……")
            wx4 = prepare_wx4(key_file=key_file or None, wx_path=wx_path or None,
                              decrypted_dir=decrypted_dir or None, my_wxid=my_wxid or None,
                              no_decrypt=bool(no_decrypt), force_decrypt=bool(force_decrypt),
                              work_path=work_path, log=print)
            if wx4.get("ok"):
                mid = wx4["my_wxid"] or "wxid_wx4"
                gc.set_conf(mid, "wxid", wx4["my_wxid"] or mid)
                gc.set_conf(mid, "my_wxid", wx4["my_wxid"] or mid)
                gc.set_conf(mid, "wx_path", wx4["wx_path"])
                gc.set_conf(mid, "key_file", wx4["key_file"])
                gc.set_conf(mid, "key", "")
                gc.set_conf(mid, "merge_path", wx4["primary_db"])
                gc.set_conf(mid, "db_config", {
                    "key": mid,            # 只作连接池缓存键，用 wxid 保证稳定
                    "type": "sqlite",
                    "path": wx4["primary_db"],
                    "my_wxid": wx4["my_wxid"] or mid,
                    "decrypted_dir": wx4["decrypted_dir"],   # side 库（contact/session/头像）在这
                })
                gc.set_conf(auto_setting, "last", mid)
                print(f"[+] 4.x 自动初始化完成：wxid={wx4['my_wxid'] or '(未识别，IsSender 可能不准)'}")
                print(f"    原始库目录：{wx4['db_storage']}")
                print(f"    解密库目录：{wx4['decrypted_dir']}")
                print(f"    主库      ：{wx4['primary_db']}")
                print(f"    {wx4['msg']}")
                print("[+] UI 会直接加载该账号的全部会话，无需在界面上填任何路径")
            else:
                print(f"[-] 4.x 自动准备未完成：{wx4.get('msg')}")
                print("    服务照常启动；可加 --key_file/--wx_path 后重启，或在界面手动初始化"
                      "（3.x 逻辑不受影响）")
        except Exception as e:
            print(f"[-] 4.x 自动准备异常（已忽略，不影响 3.x）：{e}")
```

### 2.4 `pywxdump/cli.py`：`MainUi` 整类

```python
class MainUi(BaseSubMainClass):
    mode = "ui"
    parser_kwargs = {"help": "启动UI界面"}

    def init_parses(self, parser):
        # 添加 'ui' 子命令解析器
        parser.add_argument("-p", '--port', metavar="", type=int, help="(可选)端口号", default=5000)
        parser.add_argument("--online", help="(可选)是否在线查看(局域网查看)", default=False, action='store_true')
        parser.add_argument("--debug", help="(可选)是否开启debug模式", default=False, action='store_true')
        parser.add_argument("--noOpenBrowser", dest='isOpenBrowser', default=True, action='store_false',
                            help="(可选)用于禁用自动打开浏览器")
        parser.add_argument("--killPort", dest='kill_port', default=False, action='store_true',
                            help="(可选)端口被占用时，自动结束占用该端口的进程（默认不杀，自动换一个空闲端口）")
        # ---- 【4.x 改造 · 第三步】启动即自动准备：密钥文件 / 模式 / 解密目录 ----
        parser.add_argument("--key_file", "--keys_file", dest="key_file", metavar="", default="",
                            help="(可选·4.x)本地密钥文件，默认自动找 ~/.wechat-cli/all_keys.json")
        parser.add_argument("--mode", dest="wx_mode", metavar="", choices=["auto", "3x", "4x"], default="auto",
                            help="(可选)auto/4x=启动时自动准备 4.x（读密钥→解密→加载会话）；3x=完全走 3.x 老逻辑")
        parser.add_argument("--decrypted_dir", dest="decrypted_dir", metavar="", default="",
                            help="(可选·4.x)解密产物目录，默认复用已有解密库或 <工作目录>/decrypted_wx4")
        parser.add_argument("--no_decrypt", dest="no_decrypt", default=False, action="store_true",
                            help="(可选·4.x)完全不解密，只用 --decrypted_dir 里现成的库")
        parser.add_argument("--force_decrypt", dest="force_decrypt", default=False, action="store_true",
                            help="(可选·4.x)把已存在但比原始库旧的库也重新解密一遍")
        return parser

    def run(self, args):
        print(f"[*] PyWxDump v{pywxdump.__version__}")
        # 从命令行参数获取值
        online = args.online
        port = args.port
        debug = args.debug
        isopenBrowser = args.isOpenBrowser

        start_server(port=port, online=online, debug=debug, isopenBrowser=isopenBrowser,
                     key_file=getattr(args, "key_file", "") or "",
                     mode=getattr(args, "wx_mode", "auto") or "auto",
                     decrypted_dir=getattr(args, "decrypted_dir", "") or "",
                     no_decrypt=bool(getattr(args, "no_decrypt", False)),
                     force_decrypt=bool(getattr(args, "force_decrypt", False)),
                     kill_port=getattr(args, "kill_port", False))
```

### 2.5 `pywxdump/cli.py`：`MainApi` 整类

```python
class MainApi(BaseSubMainClass):
    mode = "api"
    parser_kwargs = {"help": "启动api，不打开浏览器"}

    def init_parses(self, parser):
        # 添加 'api' 子命令解析器
        parser.add_argument("-p", '--port', metavar="", type=int, help="(可选)端口号", default=5000)
        parser.add_argument("--online", help="(可选)是否在线查看(局域网查看)", default=False, action='store_true')
        parser.add_argument("--debug", action='store_true', help="(可选)是否开启debug模式", default=False)
        parser.add_argument("--killPort", dest='kill_port', default=False, action='store_true',
                            help="(可选)端口被占用时，自动结束占用该端口的进程（默认不杀，自动换一个空闲端口）")
        # ---- 【4.x 改造 · 第三步】同 ui：启动即自动准备 ----
        parser.add_argument("--key_file", "--keys_file", dest="key_file", metavar="", default="",
                            help="(可选·4.x)本地密钥文件，默认自动找 ~/.wechat-cli/all_keys.json")
        parser.add_argument("--mode", dest="wx_mode", metavar="", choices=["auto", "3x", "4x"], default="auto",
                            help="(可选)auto/4x=启动时自动准备 4.x（读密钥→解密→加载会话）；3x=完全走 3.x 老逻辑")
        parser.add_argument("--decrypted_dir", dest="decrypted_dir", metavar="", default="",
                            help="(可选·4.x)解密产物目录，默认复用已有解密库或 <工作目录>/decrypted_wx4")
        parser.add_argument("--no_decrypt", dest="no_decrypt", default=False, action="store_true",
                            help="(可选·4.x)完全不解密，只用 --decrypted_dir 里现成的库")
        parser.add_argument("--force_decrypt", dest="force_decrypt", default=False, action="store_true",
                            help="(可选·4.x)把已存在但比原始库旧的库也重新解密一遍")
        return parser

    def run(self, args):
        print(f"[*] PyWxDump v{pywxdump.__version__}")
        # 从命令行参数获取值
        online = args.online
        port = args.port
        debug = args.debug

        start_server(port=port, online=online, debug=debug, isopenBrowser=False,
                     key_file=getattr(args, "key_file", "") or "",
                     mode=getattr(args, "wx_mode", "auto") or "auto",
                     decrypted_dir=getattr(args, "decrypted_dir", "") or "",
                     no_decrypt=bool(getattr(args, "no_decrypt", False)),
                     force_decrypt=bool(getattr(args, "force_decrypt", False)),
                     kill_port=getattr(args, "kill_port", False))
```

### 2.6 `pywxdump/api/remote_server.py`：`is_init()`

```python
def is_init():
    """
    是否初始化
    :return:
    """

    local_wxids = gc.get_local_wxids()
    if len(local_wxids) > 1:
        return ReJson(0, True)
    # 【4.x 改造 · 第三步】自动初始化后 conf 里可能只有 1 个 wxid（这台机器上就是本人一个号），
    # 原来的「>1 才算初始化过」会让前端跳去 /db_init 要求手填路径。
    # 这里补一条判据：只要 last 指向的库配置真实可用，就算已初始化。
    last = gc.get_conf(gc.at, "last")
    if last:
        db_config = gc.get_conf(last, "db_config")
        p = db_config.get("path") if isinstance(db_config, dict) else ""
        if p and os.path.exists(p):
            return ReJson(0, True)
    return ReJson(0, False)


# start 以下为聊天联系人相关api *******************************************************************************************
```

### 2.7 `pywxdump/api/local_server.py`：`/wxinfo` 把密钥文件带上

```python
    my_wxid = gc.get_conf(gc.at, "last") or ""
    decrypted_dir = None
    wx_path = None
    key_file = None
    if my_wxid:
        db_config = gc.get_conf(my_wxid, "db_config")
        wx_path = gc.get_conf(my_wxid, "wx_path")
        # 【4.x 改造 · 第三步】启动时自动准备写下的密钥文件，一并传给 get_wx_info，
        # 让 4.x 下的「微信信息」也能带出账号/密钥列表（3.x 时这里是 None，行为不变）。
        key_file = gc.get_conf(my_wxid, "key_file") or None
        if isinstance(db_config, dict):
            p = db_config.get("path") or ""
            if p and os.path.exists(p):
                decrypted_dir = os.path.dirname(p)

    wxinfos = get_wx_info(WX_OFFS, decrypted_dir=decrypted_dir,
                          my_wxid=my_wxid or None, wx_path=wx_path, key_file=key_file)
```

## 3. 怎么跑

**管理员 PowerShell（推荐）**
```powershell
cd D:\PyWxDump_Source
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
# 最省事：什么都不用给，自动找密钥文件、自动找账号目录、自动用已有解密库
& $py -m pywxdump ui

# 想显式指定（等价）
& $py -m pywxdump ui --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json"

# 想顺带把 13 个过期库刷新一遍（约 1 分钟，会写 D:\decrypted_wx_db）
& $py -m pywxdump ui --force_decrypt

# 完全不解密，只用现成解密库（最快）
& $py -m pywxdump ui --no_decrypt

# 3.x / 不想走 4.x 自动准备
& $py -m pywxdump ui --mode 3x
```

**继续用 `wxdump` 这个命令（你的 .bat 方式）**
```powershell
$env:PYTHONPATH = "D:\PyWxDump_Source"
wxdump ui --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json"
```

**参数一览（`ui` 和 `api` 都有）**

| 参数 | 说明 |
| --- | --- |
| `--key_file` / `--keys_file` | 本地密钥文件；不给就自动找 `~\.wechat-cli\all_keys.json` |
| `--mode auto\|4x\|3x` | `auto`/`4x` 走 4.x 自动准备；`3x` 完全按老逻辑启动 |
| `--decrypted_dir` | 解密产物目录；不给就按「已存在的解密库（含 message_*_decrypted.db）→ 工作目录/decrypted_wx4」依次挑 |
| `--no_decrypt` | 完全不解密 |
| `--force_decrypt` | 把过期的库也重解一遍 |
| `-p/--port` | 端口（被占用时自动打印占用者并换空闲端口） |

**为什么不用手填 merge_all.db / 微信文件夹路径**：前端 `App` 启动时先 `GET /api/rs/is_init`，返回真就进会话页；返回假才跳 `/db_init` 让你填。本步把这个判据改成「`last` 指向的库配置真实可用」，并在启动时就写好了 conf，所以首屏直接进会话页。

## 4. 验收清单（含本次实测）

| # | 验收项 | 判据 | 实测 |
| ---: | --- | --- | --- |
| 1 | `ui` 启动横幅出现 4.x 自动准备 | 5 行 `[+] …` | ✅ |
| 2 | 自动选中正确账号（14 选 1） | HMAC 3/3，其余 0 | ✅ |
| 3 | 自动推断本人 wxid | contact + Name2Id 双旁证 | ✅ `<本人wxid>` |
| 4 | 自动选用解密库 | 复用 `D:\decrypted_wx_db` | ✅ |
| 5 | 解密算法正确 | 与 `batch_decrypt.py` 产物逐字节一致 | ✅ 860160 B 完全相同 |
| 6 | 前端不再要求手填路径 | `/api/rs/is_init`=true | ✅ |
| 7 | 会话/联系人自动加载 | 549 / 9715 | ✅ |
| 8 | 能读消息 | 群聊 10 条 | ✅ |
| 9 | 微信信息页有数据（含 29 把密钥） | `/api/ls/wxinfo` | ✅ |
| 10 | `--mode 3x` 零回归 | 无 4.x 准备字样 + `is_init`=false | ✅ |
| 11 | 旧 3.x 回归套件 | `wx4_step2_verify.py` | ✅ 62/62 |
| 12 | 密钥文件缺失/无账号时不崩 | 打印原因，服务照常起 | ✅ |
| 13 | 端口占用处理 | 自动换空闲端口 | ✅（5010/5011 实测） |

## 5. 现在的能力边界（别踩坑）

- **UI 看到的是「启动那一刻」的解密快照**。你微信在跑，`message_0.db` 一直在写，所以启动时那 13 个库会显示「过期未刷新」。想刷新：加 `--force_decrypt` 重启（约 1 分钟）；或者先停微信再启动 UI。
- **媒体消息**仍只显示类型标签（图片/语音/视频），这是上一步就确定的方案：`media_*.db` 解码不动。
- **手机号/邮箱**在 4.x 下为空（`mobile: null`），代码里字段留着不会崩；要填只能另找来源。
- **进程内存那条路已经封死**，不要再指望 `wxdump info` 从内存里取 4.x 密钥：本机实测 5 个 `Weixin.exe`、约 432 MB 私有内存里，
  `com.Tencent.WCDB.Config.Cipher`（ASCII/UTF-16LE）**0 命中**，`Config.Cipher` 0 命中；29 把真密钥的原文/`x'…'`/单字节 XOR 变形也都 0 命中。
  详见桌面 `wx4_cipher_scan_probe.py` 的实测输出。

## 6. 回滚

```powershell
$dst = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\Lib\site-packages\pywxdump"
copy "$dst\api\__init__.py"      "D:\PyWxDump_Source\pywxdump\api\__init__.py"
copy "$dst\api\remote_server.py" "D:\PyWxDump_Source\pywxdump\api\remote_server.py"
copy "$dst\api\local_server.py"  "D:\PyWxDump_Source\pywxdump\api\local_server.py"
copy "$dst\cli.py"                "D:\PyWxDump_Source\pywxdump\cli.py"
remove D:\PyWxDump_Source\pywxdump\wx_core\wx4_prepare.py
```
（`cli.py` 里第一步/第二步的 4.x 改动会一并回滚，如需保留请只回滚上面三个 api 文件。）
