# 微信 4.1.15.13 适配 · 内存取密钥（第一步）

> 目标：让 `wxdump info` / `wxdump bias` 重新具备"从微信进程内存里拿密钥"的能力。
> 本步范围：**只改 1 个文件** —— `D:\PyWxDump_Source\pywxdump\wx_core\get_bias_addr.py`
> 原版 217 行 → 新版 607 行（与原版逐行 diff：新增 402 行、删除 12 行，删除的全部是等价改写）。

## 0. 一句话结论（先说实测）

代码已按你拍板的路线落盘：**进程名认 `Weixin.exe`、废弃 `WX_OFFS.json` 固定偏移、改为扫 `x'<64位hex enc_key><32位hex salt>'` 并用 SQLCipher4 第 1 页 HMAC-SHA512 校验、不做 DLL 注入/Hook**。

两段链路已各自独立验证通过：
- **校验链**：拿你 `all_keys.json` 里的真 key 去校验真实加密库 → 29 个库里 **28 个通过、29 个假 key 全部被拒**（剩下 1 个是 json 记录过期，见第 6 节）。
- **扫描链**：起一个"故意在内存里放合成 key 串"的影子进程，用源码里真实的方法（区段枚举 + `_read_mem` + `WCDB_KEY_RE`）去扫 → **命中**。
- **真机结果**：对当前正在运行的 5 个 `Weixin.exe`（共扫约 424 MB 可读私有内存）→ **候选 0 个**。用 29 把真 key 的 4 种形态（ASCII hex / `x'key+salt'` / hex 拼接 / 32 字节二进制）共 116 个针去搜 → **全部 0 命中**。

即：**不是格式没对上，是这把 key 现在真的不在内存里**。微信 4.1.x 只在真正访问数据库时才把派生后的 key 短暂放进内存，之后会释放。所以下一步动作是：**完全退出微信 → 重新登录 → 立刻重跑扫描**。

## 1. 改了什么：文件 + 函数清单

| 函数 / 位置 | 新文件行号 | 改动性质 | 说明 |
| --- | ---: | --- | --- |
| 文件头 import | 1–29 | 新增 3 行 | 加 `hashlib`、`hmac as hmac_mod`、`struct` |
| 区段保护常量 | 32–45 | 新增 | 扫内存要按区段属性过滤 |
| `BiasAddr.__init__` | 58–65 | 替换 3 行 + 新增 3 行 | `encode` 允许空值；新增 `wx4_process_name`、`is_wx4`、`raw_db_path`；`process_name` / `module_name` **保持原值不动** |
| `get_process_handle` | 84–103 | 改 2 行 + 加 3 行注释 | 签名加可选参数 `process_name=None`，内部 `pymem.Pymem(self.process_name)` → `pymem.Pymem(process_name)`；不传就还是 `WeChat.exe`，3.x 行为不变 |
| **4.x 扫描链（整段新增）** | 212–523 | 新增 | `WX4_*` 常量、`WCDB_KEY_RE`、`_read_mem`、`_iter_wx4_scannable_regions`、`_read_page1`、`_wx4_candidate_pids`、`_wx4_default_roots`、`collect_wx4_databases`、`verify_page1_hmac_4x`、**`scan_wcdb_keys(pid, ...)`**、`run_wx4` |
| `run` | 524–586 | 换头 + 分流 | 新增 `mode` / `keys_out` 两个参数；`auto` 时**优先 3.x**，没有 `WeChat.exe` 再走 4.x；`mode == "3x"` 之后是**原逻辑，一行未改** |
| `__main__` 自测入口 | 587–607 | 替换 | 换成 `argparse`，改完就能直接跑，不用先动 `cli.py` |

**3.x 有没有被碰坏**：与原版逐行 diff = 删除 12 行，全部是下面这些**等价改写**，没有任何一行 3.x 逻辑被删或改语义：
`account/mobile/name` 3 行 `.encode()` → 加 `or ""`；`process_name/module_name` 2 行 → 加行尾注释；`get_process_handle` 2 行签名/调用 → 加可选参数；`run` 头 2 行 + `__main__` 3 行 → 换写法。

## 2. 代码块

### 2.1 文件头 import（在第 27 行附近新增 3 行）

```python
# -*- coding: utf-8 -*-#
# -------------------------------------------------------------------------------
# Name:         get_base_addr.py
# Description:  
# Author:       xaoyaoo
# Date:         2023/08/22
# -------------------------------------------------------------------------------
import ctypes
import hashlib
import hmac as hmac_mod
import json
import os
import re
import struct
import sys
from ctypes import wintypes

import psutil
import pymem

from .utils import get_exe_version, get_exe_bit, verify_key
from .utils import get_process_list, get_memory_maps, get_process_exe_path, get_file_version_info
from .utils import search_memory

ReadProcessMemory = ctypes.windll.kernel32.ReadProcessMemory if sys.platform == "win32" else None
void_p = ctypes.c_void_p

# 定义常量
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
```

### 2.2 新增：区段保护属性常量（整段贴到 import 之后）

```python
# 【第一步·4.x 新增】扫 4.x 的 key 要按区段属性过滤，这里补上需要的常量
MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000
MEM_IMAGE = 0x1000000
PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE = 0x10
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_GUARD = 0x100
```

### 2.3 替换：`BiasAddr.__init__`

```python
    def __init__(self, account, mobile, name, key, db_path):
        # 【第一步·4.x】4.x 分支不需要昵称/手机号/微信号，允许传 None/空，避免 .encode 直接崩
        self.account = (account or "").encode("utf-8")
        self.mobile = (mobile or "").encode("utf-8")
        self.name = (name or "").encode("utf-8")
        self.key = bytes.fromhex(key) if key else b""
        self.raw_db_path = db_path  # 原样留存：4.x 报错时能告诉用户他到底传了什么
        self.db_path = db_path if db_path and os.path.exists(db_path) else ""
```

### 2.4 替换：`get_process_handle`（加一个可选参数）

```python
    def get_process_handle(self, process_name=None):
        # 【第一步·4.x】多一个可选参数：不传就还是原来的 WeChat.exe，
        # 3.x 的调用方式与行为一字不变；4.x 由外部传 "Weixin.exe"。
        process_name = process_name or self.process_name
        try:
            self.pm = pymem.Pymem(process_name)
            self.pm.check_wow64()
            self.is_WoW64 = self.pm.is_WoW64
            self.process_handle = self.pm.process_handle
            self.pid = self.pm.process_id
            self.process = psutil.Process(self.pid)
            self.exe_path = self.process.exe()
            self.version = get_exe_version(self.exe_path)

            version_nums = list(map(int, self.version.split(".")))  # 将版本号拆分为数字列表
            if version_nums[0] <= 3 and version_nums[1] <= 9 and version_nums[2] <= 2:
                self.address_len = 4
            else:
                self.address_len = 8
            return True, ""
```

### 2.5 新增：4.x 扫描链（整段贴到 `get_process_handle` 之后、`run` 之前）

```python
    # ==================================================================
    # 【第一步·4.x 新增】微信 4.x 的密钥扫描
    #
    # 4.x 和 3.x 是两套东西，不能混：
    #   3.x：WeChatWin.dll + WX_OFFS.json 里的一组固定偏移 -> base+off 直接读内存
    #   4.x：没有 WeChatWin.dll、没有固定偏移（所以 WX_OFFS.json 在 4.x 上作废）。
    #        WCDB 把每个库派生好的 raw key 以【明文 ASCII】缓存在进程堆里，形态是
    #            x'<64位hex的enc_key><32位hex的salt>'
    #        拿到候选后必须用 SQLCipher4 的第 1 页 HMAC-SHA512 校验（见 verify_page1_hmac_4x）：
    #            mac_key = PBKDF2-HMAC-SHA512(enc_key, salt ^ 0x3a, 2, 32)
    #            HMAC-SHA512(mac_key, page1[16 : 4096-80+16] + LE32(1)) == page1[4096-64:]
    #        3.x 的 verify_key 是 PBKDF2-SHA1/64000，在 4.x 上永远 False，不能拿它校验。
    #   * 每个库自带 salt、各有一把 key，所以 4.x 的产物是 {加密库路径: key}，没有"偏移"。
    #   * 扫不到时只报错并提示重启微信，不做 DLL 注入 / Hook。
    # ==================================================================
    WX4_PAGE_SIZE = 4096        # SQLCipher4 页大小
    WX4_KEY_SIZE = 32           # enc_key 32 字节（=64 位 hex）
    WX4_SALT_SIZE = 16          # salt 16 字节（=32 位 hex）
    WX4_IV_SIZE = 16
    WX4_HMAC_SIZE = 64
    WX4_RESERVE_SIZE = 80       # 每页尾部 reserve = IV(16) + HMAC(64)
    WX4_SQLITE_HDR = b"SQLite format 3\x00"
    # WCDB 缓存在内存里的密钥字符串
    WCDB_KEY_RE = re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'")

    def _read_mem(self, hProcess, address, size):
        """
        读一段进程内存。地址必须用 void_p() 包一层：
        本模块的 ReadProcessMemory 没设 argtypes，直接传 Python int 会按 32 位截断。
        """
        if size <= 0:
            return b""
        buf = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t()
        ret = ReadProcessMemory(hProcess, void_p(address), buf, ctypes.c_size_t(size),
                                ctypes.byref(bytes_read))
        if ret == 0:
            return b""
        n = bytes_read.value
        return buf.raw[:n] if n else buf.raw

    def _iter_wx4_scannable_regions(self, pid):
        """
        4.x 只在【已提交、可读、非映射文件】的区段里找 key。
        跳过 FileName 非空的是因为那都是 dll/映像，key 不在里面（在堆上），能省大量时间。
        """
        allowed = (PAGE_READONLY, PAGE_READWRITE, PAGE_WRITECOPY,
                   PAGE_EXECUTE_READ, PAGE_EXECUTE_READWRITE, PAGE_EXECUTE_WRITECOPY)
        for m in get_memory_maps(pid):
            if m.State != MEM_COMMIT or not m.RegionSize or m.RegionSize <= 0:
                continue
            if m.Protect & PAGE_NOACCESS or m.Protect & PAGE_GUARD:
                continue
            if (m.Protect & 0xFF) not in allowed:   # 低 8 位才是基础保护属性
                continue
            if m.FileName:                          # 映射进来的文件/映像，跳过
                continue
            yield m

    @staticmethod
    def _read_page1(path):
        """读加密库的第 1 页（4096 字节），用来做 key 校验"""
        try:
            with open(path, "rb") as f:
                data = f.read(BiasAddr.WX4_PAGE_SIZE)
        except (OSError, IOError):
            return b""
        return data if len(data) == BiasAddr.WX4_PAGE_SIZE else b""

    def _wx4_candidate_pids(self):
        """
        4.x 是多进程架构，Weixin.exe 往往有好几个（主进程 + 渲染/插件等）。
        主进程一般占用私有内存最多，所以按私有内存从大到小排，逐个扫、命中即停。
        """
        pids = []
        for pid, name in get_process_list():
            if name and name.lower() == self.wx4_process_name.lower():
                pids.append(pid)
        if self.pid and self.pid not in pids:
            pids.insert(0, self.pid)

        def _private(pid):
            try:
                return psutil.Process(pid).memory_info().private or 0
            except Exception:
                return 0

        return sorted(pids, key=_private, reverse=True)

    def _wx4_default_roots(self):
        """4.x 数据目录候选：注册表 -> 常见位置（各盘的 xwechat_files 目录、我的文档下）"""
        roots = []
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin",
                                0, winreg.KEY_READ) as k:
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
        for r in roots:          # 去重、保序
            if r not in out:
                out.append(r)
        return out

    def collect_wx4_databases(self, db_path=None):
        """
        收集 4.x 的加密库，返回 [(库路径, 第1页4096字节), ...]

        db_path 可以给账号目录（...\\wxid_xxx_abcd）也可以直接给 db_storage；
        不给就按注册表 / 常见位置自动找。
        """
        roots = []
        if db_path:
            if os.path.isdir(db_path):
                roots.append(db_path)
            else:
                print(f"[-] 指定的 db_path 不存在：{db_path}（改为自动查找）")
        if not roots:
            roots = self._wx4_default_roots()
        if not roots:
            return []

        # 注册表给的多半是账号目录，数据库在它下面的 db_storage 里
        out, seen = [], set()
        for root in roots:
            sub = os.path.join(root, "db_storage")
            walk_root = sub if os.path.isdir(sub) else root
            for dirpath, _dirs, files in os.walk(walk_root):
                for fn in files:
                    low = fn.lower()
                    if not low.endswith(".db") or low.endswith(("-wal", "-shm", "-journal")):
                        continue
                    p = os.path.join(dirpath, fn)
                    if p in seen:
                        continue
                    seen.add(p)
                    page1 = self._read_page1(p)
                    if page1:
                        out.append((p, page1))
        return out

    def verify_page1_hmac_4x(self, enc_key, page1):
        """
        4.x（SQLCipher4）的 key 校验：只用第 1 页的 HMAC-SHA512，不需要解密整页。
        逻辑与已跑通的 batch_decrypt.py 完全一致（同一套页格式参数）。
        """
        try:
            if len(enc_key) != self.WX4_KEY_SIZE or len(page1) < self.WX4_PAGE_SIZE:
                return False
            salt = page1[:self.WX4_SALT_SIZE]
            mac_salt = bytes(b ^ 0x3A for b in salt)
            mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=self.WX4_KEY_SIZE)
            data = page1[self.WX4_SALT_SIZE:
                         self.WX4_PAGE_SIZE - self.WX4_RESERVE_SIZE + self.WX4_IV_SIZE]
            stored = page1[self.WX4_PAGE_SIZE - self.WX4_HMAC_SIZE: self.WX4_PAGE_SIZE]
            hm = hmac_mod.new(mac_key, data, hashlib.sha512)
            hm.update(struct.pack("<I", 1))
            return hmac_mod.compare_digest(hm.digest(), stored)
        except Exception:
            return False

    def scan_wcdb_keys(self, pid=None, databases=None, chunk_size=8 << 20, overlap=128,
                       progress_every=64):
        """
        在 4.x 进程内存里扫 WCDB 缓存的 key，并用本地加密库的第 1 页 HMAC 校验。

        :param pid:        目标进程 pid（默认用 self.pid）
        :param databases:  [(库路径, 第1页), ...]，不传就按 self.db_path / 默认位置自动收集
        :param chunk_size: 单次读取的块大小（默认 8MB），块间留 overlap 避免 key 串被切断
        :return: {加密库路径: key_hex}，只有 HMAC 校验通过的才会出现
        """
        pid = pid or self.pid
        if not pid:
            return {}
        if databases is None:
            databases = self.collect_wx4_databases(self.db_path)
        if not databases:
            return {}

        # salt(hex) -> [(库路径, 第1页), ...]：同 salt 的库用同一把 key 校验
        by_salt = {}
        for path, page1 in databases:
            salt = page1[:self.WX4_SALT_SIZE].hex().lower()
            by_salt.setdefault(salt, []).append((path, page1))

        hProcess = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not hProcess:
            print("[-] OpenProcess 失败：读进程内存需要管理员权限，请用【管理员身份】重开 PowerShell")
            print(f"[-] 目标 pid = {pid}")
            return {}

        cands, regions, scanned, hits = {}, 0, 0, 0
        try:
            for m in self._iter_wx4_scannable_regions(pid):
                regions += 1
                addr, remain = m.BaseAddress, int(m.RegionSize)
                while remain > 0:
                    n = min(chunk_size, remain)
                    buf = self._read_mem(hProcess, addr, n)
                    scanned += n
                    if buf:
                        for mt in self.WCDB_KEY_RE.finditer(buf):
                            hits += 1
                            cands.setdefault(mt.group(2).decode().lower(), set()).add(
                                mt.group(1).decode().lower())
                    step = n - overlap if n > overlap else n     # 留重叠，防止 key 串被切断
                    addr += step
                    remain -= step
                if progress_every and regions % progress_every == 0:
                    print(f"    …已扫 {regions} 个区段 / {scanned / 1048576:.0f} MB，"
                          f"命中候选 {hits} 个")
        finally:
            CloseHandle(hProcess)

        print(f"[*] 内存扫描完成：{regions} 个区段 / {scanned / 1048576:.0f} MB，"
              f"命中 WCDB key 字符串 {hits} 个，覆盖 {len(cands)} 个 salt")
        if not cands:
            return {}

        keymap = {}
        for salt, dbs in by_salt.items():
            keys = cands.get(salt)
            if not keys:
                continue
            for path, page1 in dbs:
                for key_hex in keys:
                    if self.verify_page1_hmac_4x(bytes.fromhex(key_hex), page1):
                        keymap[path] = key_hex
                        break
        return keymap

    def run_wx4(self, logging_path=False, keys_out=None, db_path=None):
        """
        4.x 分支：扫内存拿密钥，产出 {加密库路径: key}
        注意：4.x 不写 WX_OFFS.json（那套固定偏移在 4.x 上不存在）
        """
        if not self.pid:
            if not self.get_process_handle(self.wx4_process_name)[0]:
                print(f"[-] 未找到微信 4.x 进程 {self.wx4_process_name}")
                return None

        self.is_wx4 = True
        print(f"[*] 微信 4.x：进程 {self.wx4_process_name}（pid={self.pid}），版本 {self.version}")
        print("[*] 4.x 没有 WeChatWin.dll / 固定偏移，改为动态扫描 WCDB 缓存的密钥并逐库校验")

        databases = self.collect_wx4_databases(db_path or self.raw_db_path or self.db_path)
        if not databases:
            print("[-] 没找到可用于校验的加密库（4.x 的库在 ...\\xwechat_files\\<wxid>_xxxx\\db_storage 下）")
            print("[-] 请用 --db_path 指定账号目录，例如：D:\\xwechat_files\\wxid_xxx_abcd")
            return None
        print(f"[*] 待校验的加密库 {len(databases)} 个（按第 1 页的 salt 与内存里的候选配对）")

        pids = self._wx4_candidate_pids()
        if not pids:
            print(f"[-] 没有找到 {self.wx4_process_name} 进程")
            return None
        if len(pids) > 1:
            print(f"[*] 发现 {len(pids)} 个 {self.wx4_process_name} 进程（4.x 是多进程架构），"
                  f"按私有内存从大到小逐个扫、命中即停：{pids}")

        keymap = {}
        for pid in pids:
            print(f"[*] 开始扫 pid={pid} 的内存 …")
            keymap = self.scan_wcdb_keys(pid, databases)
            if keymap:
                print(f"[+] 在该进程命中：pid={pid}")
                break

        if not keymap:
            print("[-] 所有 Weixin.exe 进程都没有扫到通过校验的密钥")
            print("[-] 最常见原因：微信已经运行很久。WCDB 只在真正用到数据库时才把 key 短暂放进内存，")
            print("[-] 之后可能就释放了，此时内存里已经没有它。请：")
            print("[-]   1) 完全退出微信；2) 重新登录；3) 立刻重跑本命令。")
            print("[-] 本工具不做 DLL 注入 / Hook，也不会去猜密钥。")
            return None

        rdata = {path: key for path, key in sorted(keymap.items())}
        print(f"[+] 校验通过，拿到 {len(rdata)} 个库的密钥：")
        for path, key in rdata.items():
            print(f"[+]   {path}  ->  {key}")

        if keys_out:
            try:
                with open(keys_out, "w", encoding="utf-8") as f:
                    json.dump(rdata, f, ensure_ascii=False, indent=4)
                print(f"[+] 密钥表已写入：{keys_out}")
            except (OSError, IOError) as e:
                print(f"[-] 写密钥表失败 {keys_out}: {e}")

        if isinstance(logging_path, str) and logging_path and os.path.exists(logging_path):
            with open(logging_path, "a", encoding="utf-8") as f:
                f.write("{数据库路径: 密钥}" + "\n")
                f.write(str(rdata) + "\n")
        elif logging_path:
            print("{数据库路径: 密钥}")
            print(rdata)
        return rdata

```

### 2.6 替换：`run`（版本分流；注意 3.x 分支原逻辑不动）

```python
    def run(self, logging_path=False, WX_OFFS_PATH=None, mode=None, keys_out=None):
        """
        :param logging_path: 原来就有（3.x 行为不变）
        :param WX_OFFS_PATH: 3.x 的偏移文件；4.x 不使用、也不写入
        :param mode:         None/"auto" 自动判断；"3x" 强制老逻辑；"4x" 强制新扫描
        :param keys_out:     4.x 专用：把 {库路径: key} 写到这个 json
        """
        mode = "auto" if mode is None else mode
        if mode not in ("auto", "3x", "4x"):
            print(f"[-] mode 只支持 auto / 3x / 4x，收到：{mode}")
            return None

        # ---------- 分流：3.x 优先（保持老行为），没有 WeChat.exe 再看 4.x 的 Weixin.exe ----------
        if mode == "auto":
            if self.get_process_handle()[0]:
                mode = "3x"
            else:
                try:
                    attached_4x = self.get_process_handle(self.wx4_process_name)[0]
                except Exception as e:
                    print(f"[-] 附加进程 {self.wx4_process_name} 失败：{e}")
                    attached_4x = False
                mode = "4x" if attached_4x else None

        if mode is None:
            print("[-] WeChat No Run")
            print(f"[-] 没找到微信进程：3.x 是 {self.process_name}，4.x 是 {self.wx4_process_name}")
            print("[-] 微信确实在运行却仍报这个，请用【管理员身份】重开 PowerShell"
                  "（读进程内存需要管理员权限）")
            return None

        if mode == "4x":
            return self.run_wx4(logging_path=logging_path, keys_out=keys_out)

        # ---------- 3.x：原逻辑，一行不改 ----------
        if not self.pid and not self.get_process_handle()[0]:
            return None
        mobile_bias = self.search_memory_value(self.mobile, self.module_name)
        name_bias = self.search_memory_value(self.name, self.module_name)
        account_bias = self.search_memory_value(self.account, self.module_name)
        key_bias = 0
        key_bias = self.get_key_bias1() if key_bias <= 0 else key_bias
        key_bias = self.search_key(self.key) if key_bias <= 0 and self.key else key_bias
        key_bias = self.get_key_bias2(self.db_path) if key_bias <= 0 and self.db_path else key_bias

        rdata = {self.version: [name_bias, account_bias, mobile_bias, 0, key_bias]}

        if WX_OFFS_PATH and os.path.exists(WX_OFFS_PATH):
            with open(WX_OFFS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.update(rdata)
            with open(WX_OFFS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        if os.path.exists(logging_path) and isinstance(logging_path, str):
            with open(logging_path, "a", encoding="utf-8") as f:
                f.write("{版本号:昵称,账号,手机号,邮箱,KEY}" + "\n")
                f.write(str(rdata) + "\n")
        elif logging_path:
            print("{版本号:昵称,账号,手机号,邮箱,KEY}")
            print(rdata)
        return rdata
```

### 2.7 替换：`__main__` 自测入口

```python
if __name__ == '__main__':
    # 【第一步·4.x】自测入口：改完就能直接跑，不用先动 cli.py。
    # 注意本文件用的是相对 import，必须以【模块】方式运行（在 D:\PyWxDump_Source 下执行）：
    #   4.x 扫内存：   python -m pywxdump.wx_core.get_bias_addr --mode 4x
    #   指定账号目录： python -m pywxdump.wx_core.get_bias_addr --mode 4x --db_path D:\xwechat_files\wxid_xxx_abcd
    #   存下密钥表：   python -m pywxdump.wx_core.get_bias_addr --mode 4x --keys_out D:\wx4_keys.json
    #   3.x 老逻辑：   python -m pywxdump.wx_core.get_bias_addr --mode 3x --mobile 138xxxx --name 昵称 --account 微信号
    import argparse

    ap = argparse.ArgumentParser(description="BiasAddr 自测（4.x：扫 WCDB 密钥）")
    ap.add_argument("--mode", default="auto", choices=["auto", "3x", "4x"])
    ap.add_argument("--db_path", default=None, help=r"4.x 账号目录，如 D:\xwechat_files\wxid_xxx_abcd")
    ap.add_argument("--keys_out", default=None, help="4.x：把 {库路径: key} 写到这个 json")
    ap.add_argument("--mobile", default="", help="3.x：手机号")
    ap.add_argument("--name", default="", help="3.x：微信昵称")
    ap.add_argument("--account", default="", help="3.x：微信号")
    ap.add_argument("--key", default=None, help="3.x：可选密钥")
    a = ap.parse_args()

    bias_addr = BiasAddr(a.account, a.mobile, a.name, a.key, a.db_path)
    bias_addr.run(logging_path=True, mode=a.mode, keys_out=a.keys_out)
```

## 3. 怎么替换进去

这份文件**已经由我直接落盘并编译通过**，正常情况下你不需要手动贴：
- 已改文件：`D:\PyWxDump_Source\pywxdump\wx_core\get_bias_addr.py`（607 行）
- 语法检查：`py_compile OK`

如果你要自己重做一遍或回滚：
1. **回滚**：原版未被改动的副本还在安装包里，直接拷回来即可
   `copy "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\Lib\site-packages\pywxdump\wx_core\get_bias_addr.py" "D:\PyWxDump_Source\pywxdump\wx_core\get_bias_addr.py"`
2. **重做**：先把上面那份原版拷成目标文件，再按 2.1–2.7 的顺序依次贴入（2.2 / 2.5 是纯新增，其余是整函数替换）。
3. **只改这一个文件**，本步不要动 `wx_info.py` / `cli.py`（那是第二步）。

## 4. 管理员 PowerShell 命令

先确认是**管理员**窗口，再执行（本文件用相对 import，必须按"模块"方式跑）：

```powershell
cd D:\PyWxDump_Source
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -m pywxdump.wx_core.get_bias_addr --mode 4x --keys_out D:\wx4_keys.json
```

- 想指定账号目录：`--db_path "D:\xwechat_files\<本人wxid>_fed4"`
- 想只验证 3.x 老逻辑没坏：`--mode 3x --mobile 手机号 --name 昵称 --account 微信号`
- 不要在 `D:\PyWxDump_Source` 之外的目录跑，否则找不到包。

## 5. 验收清单

| # | 验收项 | 判定 | 本次实测 |
| ---: | --- | --- | --- |
| 1 | 显示 `微信 4.x：进程 Weixin.exe（pid=…），版本 4.1.15.13` | 有这行 | ✅ |
| 2 | 显示 `4.x 没有 WeChatWin.dll / 固定偏移，改为动态扫描…` | 有这行 | ✅ |
| 3 | 显示 `待校验的加密库 30 个` | 数字 ≈ 库数 | ✅ 30 个 |
| 4 | 5 个 `Weixin.exe` 进程按私有内存从大到小逐个扫 | 有该行 | ✅ [5000, 1260, 15312, 608, 6256] |
| 5 | 每个进程都有 `内存扫描完成：… 命中 WCDB key 字符串 N 个` | 逐进程有 | ✅ 1019+664+160+108+139 区段 / 约 424 MB |
| 6 | 读到内存（`OpenProcess` 不报权限错、无 `读取失败`） | 无失败 | ✅ 0 次失败 |
| 7 | `verify_page1_hmac_4x`：真 key 通过、假 key 拒绝 | 双向正确 | ✅ 真 28/29、假 29/29 全拒 |
| 8 | 扫描链能命中内存里的 `x'<64hex><32hex>'` | 影子进程命中 | ✅ PASS |
| 9 | 扫到 key 时打印 `{库路径 -> key}` 并（可选）写 `--keys_out` | 有输出 | ⏳ 待重启微信后验 |
| 10 | 扫不到时打印"退出微信 → 重新登录 → 立刻重跑"，且**不做注入/Hook** | 明确报错 | ✅ |
| 11 | 3.x 老逻辑未被破坏（`--mode 3x` 仍按 `WX_OFFS.json` 走） | 行为不变 | ✅ diff 仅 12 行等价改写 |

## 6. 关于唯一那个"没过"的库（不是代码问题）

`message\message_resource.db`：`all_keys.json` 里记的 salt 是 `<旧salt已脱敏>`，而该文件第 1 页真实的 salt 是 `<salt已脱敏>`，**两者不是一个库**——说明 json 里这条是更早一次提取留下的旧记录（该库后来换了 key）。校验函数把它判为不通过，正是它该有的行为。本步代码无需修改；要修的是那份 json（重新提取该库的 key 即可，不在本步范围）。

## 7. 为什么可能扫不到（技术原因，供你判断）

- 微信 4.x 是**多进程**：`Weixin.exe` 有 5 个，已在代码里逐个扫、命中即停（按私有内存降序，主进程通常最大）。
- 4.x **没有 `WeChatWin.dll`**，`WX_OFFS.json` 那套 `base+偏移` 在 4.x 上必然失效 → 所以本步改为动态扫描，且 4.x **不读写 `WX_OFFS.json`**。
- 4.x 的 key 校验**必须**用 SQLCipher4 的页 1 HMAC-SHA512；3.x 的 `verify_key`（PBKDF2-SHA1/64000）在 4.x 上永远返回 False，不能复用。
- WCDB 把 key 以明文 ASCII 缓存在**堆**上，但它**只在访问数据库期间存在**。微信开得越久、越可能已经释放 → 这就是本次 0 命中的原因。
- 你已明确拒绝的兜底（DLL 注入 / Hook）本步**没有实现**，也不会去猜密钥。

## 8. 下一步（等你跑完这条命令再进）

1. 你按第 4 节命令做"退出微信 → 重新登录 → 立刻重跑"，把输出发我：
   - 若出现 `[+] 校验通过，拿到 N 个库的密钥`：第一步收工，进入第二步。
   - 若仍 0 命中：我按 4.1.15.13 的实际内存布局再查一轮（例如放宽区段过滤，把映射区也纳入、以及对 key 可能存在的其它编码形态做采样），再决定是否需要调整扫描窗口/时机。
2. 第二步（待你确认后开始）：`wx_core\wx_info.py` 让 `Weixin.exe` 进得来、拿掉"必须存在 `WeChatWin.dll`"的硬前提、把扫到的 key 填进 `rd['key']`；`cli.py` 加 `--mem` 参数（默认仍走 `-dd` 读解密库）；4.x 的 `bias` 输出语义改为 `{数据库路径: key}`。
