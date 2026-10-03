# PyWxDump 4.0.0 · `wxdump info` 取账号信息失败（nickname/account 为 None）修复说明

修复日期：2026-10-04　文件：`D:\PyWxDump_Source\pywxdump\wx_core\wx_info.py`

---

## 一、现象

```text
[+] 接收 contact 表失败: 'NoneType' object has no attribute 'text_factory'
Traceback ...
  File "...\wx_core\wx_info.py", line 645, in get_wx_info_from_db
    con.text_factory = lambda b: ...
AttributeError: 'NoneType' object has no attribute 'text_factory'
```
密钥 13 把正常，但 `nickname: None`、`account: None`。

## 二、根因（三件事，已逐条取证）

| # | 事实 | 取证 |
|---|---|---|
| 1 | `D:\decrypted_wx_db`（旧解密库目录）**已被清理**，`E:\`/`C:\` 下的同名目录也不存在 | 诊断脚本扫描结果：三个路径 `exists=False` |
| 2 | `_wx4_guess_decrypted_dir()` 只认这 5 个老路径 → **返回 None**；而真正有库的地方是 **`C:\Users\Administrator\wxdump_work\decrypted_wx4\<本人wxid>_fed4\`**（`contact_decrypted.db` 10.2 MB，9715 行），**它从来不找这个目录** | `_wx4_guess_decrypted_dir()` → `None`；该目录实测存在且有 contact 库 |
| 3 | `contact_db` 为 None 时，原代码仍执行 `con.text_factory`，于是抛 AttributeError；**另外还有一个更严重的隐患**：当"目录存在但缺 contact"时旧代码 `return []`，会让 `info` 连密钥都打不出来 | 源码 `contact_db` 判空缺失 + 提前 `return []` |

> 说明：那句 `接收 contact 表失败` 并非本文件输出的文案（本文件原为 `读取 contact 库失败`），疑为旧版本/自行改动过的副本留下的日志；**真正抛 AttributeError 的位置就是第 645 行**，本轮按你给的栈逐行核对并修掉。

## 三、四处改动（都在 4.x 分支，3.x 零影响）

### 改动 1（需求 1）contact 库缺失时不再抛 AttributeError

```python
    # 【4.0.1 修复】contact 库缺失时，原来会执行 con.text_factory 抛 AttributeError
    con = None
    if contact_db:
        try:
            con = sqlite3.connect(contact_db)
            con.text_factory = lambda b: b.decode("utf-8", "replace") if isinstance(b, bytes) else b
            row = None
            if my_wxid:
                row = con.execute("SELECT username, nick_name, alias, remark FROM contact "
                                  "WHERE username=? LIMIT 1", (my_wxid,)).fetchone()
            if not row:
                row = con.execute("SELECT username, nick_name, alias, remark FROM contact "
                                  "WHERE flag=2049 AND username LIKE 'wxid=_%' ESCAPE '=' "
                                  "AND username NOT LIKE '%@%' LIMIT 1").fetchone()
            if row:
                rd["wxid"], rd["nickname"], rd["account"] = row[0] or my_wxid, row[1] or None, row[2] or None
        except Exception as e:
            wx_core_loger.warning(f"[-] 读取 contact 库失败（昵称/微信号留空，密钥照常输出）: {e}")
        finally:
            try:
                if con:
                    con.close()
            except Exception:
                pass
    else:
        wx_core_loger.warning("[-] 没有可用的 contact 解密库 → 昵称/微信号留空，密钥列表照常输出")
```

### 改动 2（需求 2）删掉会"杀掉整个 info 输出"的提前 return

```python
    if decrypted_dir and not contact_db:
        # 【4.0.1 修复】原来这里直接 return []，会让 info 连密钥都打不出来
        wx_core_loger.warning(f"[-] {decrypted_dir} 里没找到 contact 库，"
                              f"本次只输出密钥（昵称/微信号留空）")
```
→ `version / wxid / key_source / keys` 以及 `key_store` 一定照常输出。

### 改动 3（需求 3）自动定位 + 按需补全 contact.db

```python
def _wx4_guess_decrypted_dir(my_wxid=None):
    cands = [r"D:\decrypted_wx_db", r"E:\decrypted_wx_db", r"C:\decrypted_wx_db", ...]
    # 【4.0.1 修复】老路径被清理后，真正的解密库在 wxdump_work\decrypted_wx4\<账号>\
    prio = []
    for base in (os.path.join(os.getcwd(), "wxdump_work"),
                 os.path.join(os.path.expanduser("~"), "wxdump_work")):
        root = os.path.join(base, "decrypted_wx4")
        ...
        if my_wxid:   # 当前账号优先
            subs = ([s for s in subs if str(s).startswith(my_wxid)]
                    + [s for s in subs if not str(s).startswith(my_wxid)])
        prio += [os.path.join(root, s) for s in subs]
    prio.append(_wx4_work_dir())              # info 自己的最小集解密目录
    for d in prio + cands:
        if d and os.path.isdir(d) and _find_decrypted_db(d, "contact"):
            return d
    return None
```

`get_wx_info_from_db` 里再加一层兜底：**有密钥但没有解密库时，现场解密最小集**（contact / session / head_image）：

```python
    if not contact_db:
        _km = {str(k): v for k, v in keys.items()} if isinstance(keys, dict) and keys else (
            read_wx4_keys_file(key_file) if key_file else {})
        if _km:
            _auto = _wx4_autodecrypt_minimal(_km, wx_root=wx_root, my_wxid=my_wxid,
                                             out_dir=decrypted_dir if ... else None)
            if _auto:
                _c = _find_decrypted_db(_auto, "contact")
                if _c:
                    decrypted_dir, contact_db = _auto, _c
```

同时把 `_wx4_autodecrypt_minimal` 的三处短板补齐：
1. **密钥条目路径两种形态都能定位源库**：`wxid_xxx_fed4\contact\contact.db`（带账号前缀）与 `contact\contact.db`（不带前缀）；
2. **多账号互不覆盖**：当前账号条目优先处理，同名库本次写过就不再被其它账号覆盖；
3. **跳过原因会打印**：`原始库不存在` / `内存密钥里没有 contact/session/head_image 的条目`，一眼知道为什么没补上。

### 改动 4（需求 4）三个调用点带上 `wx_root` 与 `my_wxid`

```python
    decrypted_dir = _wx4_guess_decrypted_dir(my_wxid)          # 3 处调用点
    result = get_wx_info_from_db(..., keys=mem_keys, wx_root=wx_root)
    result = get_wx_info_from_db(..., key_file=key_file, wx_root=wx_root)
```

## 四、验证记录（本机实测，柳岸）

| 用例 | 命令 | 结果 |
|---|---|---|
| A 只读解密库 | `python -m pywxdump info --no_scan_mem` | exit 0；**nickname: 柳岸**、**account: AbnerUP**、wxid `<本人wxid>`、version `4.1.15.13`；日志 `未指定解密库目录(-dd)，自动使用 C:\Users\Administrator\wxdump_work\decrypted_wx4\<本人wxid>_fed4`；**无 text_factory 报错、无 Traceback** |
| B 你平时的默认命令 | `wxdump info --mem_budget 25` | exit 0；**13 把密钥**（与你看到的一致）、`key_source: memory`、**nickname: 柳岸 / account: AbnerUP**；无报错、无栈 |
| C 降级（坏目录） | `wxdump info --no_scan_mem -dd <只有 message 库的临时目录>` | exit 0；仅两条友好警告（"没找到 contact 库，本次只输出密钥" / "没有可用的 contact 解密库 → 昵称/微信号留空"）；**无 AttributeError、无 Traceback**，`version` 正常打印 |
| D 自动补全（单元） | 只给 `all_keys.json`、指到一个空输出目录 | 成功解出 `contact_decrypted.db`（10212 KB，**contact 表 9715 行**）、`session_decrypted.db`、`head_image_decrypted.db` |
| E 自动补全失败诊断 | 密钥里没有 contact 条目 | 明确打印 `[-] 内存密钥里没有 contact/session/head_image 的条目，无法自动解密最小集`，函数干净返回 None |
| F 语法 | `python -m py_compile wx_info.py` | exit 0 |

## 五、复验与回滚

**复验（不需要重启 UI，`info` 是一次性命令）**

```powershell
cd C:\Users\Administrator
wxdump info
```
应看到 `nickname: 柳岸`、`account: AbnerUP`，且不再出现 `text_factory` 报错。

**回滚点（已核验：与当前文件的 15 处差异全部来自本轮修复）**

| 文件 | 说明 |
|---|---|
| `pywxdump\wx_core\wx_info.py.bak_pre_contactfix` | 本轮修改前（1091 行）← 回滚用 |
| `pywxdump\wx_core\wx_info.py.bak_contactfix` | 本轮修改后快照（1168 行，sha256 前 16 `827A889DEEE7D5DC`） |

```powershell
Copy-Item "D:\PyWxDump_Source\pywxdump\wx_core\wx_info.py.bak_pre_contactfix" `
          "D:\PyWxDump_Source\pywxdump\wx_core\wx_info.py" -Force
```

> 本轮改动全部位于 4.x「读解密库」分支，3.x 老逻辑（`MSG` 表路径、`get_info_details`）一行未动。
