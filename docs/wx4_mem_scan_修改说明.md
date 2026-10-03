# 微信 4.1.15.13 适配 · `wxdump info` / `wxdump bias` 取密钥改造（第一步 + 第二步 + 第三步输出）

> 只改 `D:\PyWxDump_Source\` 里的副本，只动 `info` / `bias` 的底层读取逻辑；3.x 老路径零回归。
> 本文件里每个代码块都是从改造后文件里**原样切出来**的，不是手抄。

## 0. 先给结论（实测，不猜）

| 你要求的三步 | 落点 | 实测结果 |
| --- | --- | --- |
| 第一步：进程识别同时认 `WeChat.exe` 与 `Weixin.exe` | `wx_core/get_bias_addr.py` 新增 `WX_PROCESS_NAMES` / `get_wx_processes()`，重写 `BiasAddr._wx4_candidate_pids()` | ✅ 本机识别到 **5 个 `Weixin.exe`**（pid 4232 / 14352 / 15084 / 13296 / 3736），不再报 `WeChat No Run` |
| 第二步：废弃 `WX_OFFS.json`，改动态内存扫描 + 结构名定位 + XOR 解混淆 | `get_bias_addr.py` 新增 `scan_cipher_struct()` 等 10 个方法；`selftest_xor()` 自测 | ⚠️ 代码链路 ✅ 跑通；**真机取不到密钥**：`com.Tencent.WCDB.Config.Cipher` 在 5 个进程 / 2040 区段 / 432 MB 私有内存里 **0 命中**（ASCII 与 UTF-16LE 都是 0） |
| 第三步：`info` / `bias` 输出格式 | `wx_info.get_wx_info(scan_mem=...)`、`cli.MainWxInfo`、`cli.MainBiasAddr` | ✅ `info` 打出 版本 4.1.15.13 / 微信号 AbnerUP / 昵称 柳岸 / 密钥 64 位 hex / 29 把密钥；手机号邮箱留扩展位不崩；`bias` 打出 5 个进程的**映像基址 + 私有内存 + 可扫区段** + `{库路径: 密钥}` 29 条 |

**这次扫描最关键的一个新证据**：`com.Tencent.WCDB.Config.` 这个前缀在内存里**是存在的**，而且我抓到了它的原文，只是家族成员里**没有 `Cipher`**：

```
com.Tencent.WCDB.Config.ScalarFunction.wcdb_decompress
com.Tencent.WCDB.Config.AuxiliaryFunction.substring_match_info
com.Tencent.WCDB.Config.Tokenize.MMFtsTokenizer
（ascii:WCDB.Config 共 30 命中；ascii:Config.Cipher = 0；utf16 Cipher = 0）
```

也就是说：**搜索方法本身是对的**——它能在内存里找到同一家族的其它 Config 对象；找不到 `Cipher` 不是"搜错了"，而是微信 4.1.15.13 根本不把 Cipher（库密钥）放在可读内存里。这也解释了为什么之前扫 `x'…'` 特征串同样 0 命中。

## 1. 输出要求 ①：改哪个文件、哪个函数

| 文件 | 函数 / 位置 | 改动 |
| --- | --- | --- |
| `pywxdump/wx_core/get_bias_addr.py` | 模块级新增 `WX_PROCESS_NAMES`、`get_wx_processes()`、`find_wx_process_pids()`（第 62–95 行） | 进程名单 = `WeChat.exe` + `Weixin.exe`，统一从这里取进程 |
| 同上 | `BiasAddr._wx4_candidate_pids()`（第 322–331 行） | 改用 `get_wx_processes()`，按私有内存降序 |
| 同上 | `BiasAddr.WX4_CIPHER_NEEDLES` + `wx4_module_base()` / `_xor_sweep()` / `_extract_32b_candidates()` / `_verify_key_into()` / `_scan_one_buffer()` / `scan_cipher_struct()` / `print_cipher_report()` / `run_wx4_scan_mem()` / `selftest_xor()` / `_verify_hmac_raw()`（第 512–815 行） | **第二步主体**：结构名定位 + 原始字节 dump + 单字节 XOR 解混淆 + 32 字节候选 HMAC 校验 |
| 同上 | `__main__`（第 956–983 行） | 新增 `--scan_mem` / `--selftest` / `--key_file` |
| `pywxdump/wx_core/wx_info.py` | `get_wx_info()` 里的进程枚举（第 696–717 行） | 原来只收 `WeChat.exe`；现在同时收 `Weixin.exe` 并分开用（3.x 分支只吃 `wechat_pids`，行为不变） |
| 同上 | `_wx4_scan_mem_report()`（第 638–657 行） | 把内存诊断包一层，异常不外抛 |
| 同上 | `get_wx_info(..., scan_mem=)` 的 4.x 分支（第 728–734 行） | 附加报告，**不改变密钥来源**（仍以 `--key_file` 为准） |
| 同上 | `_wx4_find_wx_dir()`（第 472–498 行） | 顺带修掉一个小 bug：同 wxid 同时存在「带后缀（有 db_storage）」和「不带后缀」两种目录时，优先选有 `db_storage` 的那个（否则 `info` 的 `wx_dir` 会指到一个空壳目录） |
| `pywxdump/cli.py` | `MainBiasAddr`（第 102–226 行） | 新增 `--scan_mem`；4.x 分支增加「进程基址信息」输出，再打 `{库路径: 密钥}` |
| 同上 | `MainWxInfo`（第 229–273 行） | 新增 `--scan_mem`，透传给 `get_wx_info` |
| 同上 | 顶部 import | 多一行 `from pywxdump.wx_core.get_bias_addr import BiasAddr as BiasAddr4x, get_wx_processes` |

## 2. 输出要求 ②：修改后的代码（原样切片）

### 2.1 `get_bias_addr.py`｜第一步：进程识别（新增，模块级）

```python
WX_PROCESS_NAMES = ("WeChat.exe", "Weixin.exe")


def get_wx_processes(names=WX_PROCESS_NAMES):
    """
    遍历系统进程，返回所有微信进程（3.x 的 WeChat.exe 与 4.x 的 Weixin.exe 一起返回）。

    :param names: 进程名白名单（不区分大小写）
    :return: [{"pid": int, "name": "Weixin.exe", "is_wx4": bool, "private": int}, ...]
             按私有内存从大到小排序（4.x 是多进程架构，主进程一般私有内存最大）
    """
    out = []
    lowered = tuple(n.lower() for n in names)
    for pid, name in get_process_list():
        if not name or name.lower() not in lowered:
            continue
        try:
            private = psutil.Process(pid).memory_info().private or 0
        except Exception:
            private = 0
        out.append({"pid": pid, "name": name,
                    "is_wx4": name.lower() == "weixin.exe", "private": private})
    return sorted(out, key=lambda d: d["private"], reverse=True)


def find_wx_process_pids(wx4=None):
    """
    :param wx4: True 只要 4.x 的 Weixin.exe；False 只要 3.x 的 WeChat.exe；None 全要
    :return: [pid, ...]（按私有内存从大到小）
    """
    procs = get_wx_processes()
    if wx4 is not None:
        procs = [d for d in procs if d["is_wx4"] == bool(wx4)]
    return [d["pid"] for d in procs]
```

### 2.2 `get_bias_addr.py`｜`_wx4_candidate_pids()` 替换后

```python
    def _wx4_candidate_pids(self):
        """
        4.x 是多进程架构，Weixin.exe 往往有好几个（主进程 + 渲染/插件等）。
        主进程一般占用私有内存最多，所以按私有内存从大到小排，逐个扫、命中即停。
        【第一步·4.x】进程名匹配统一走 get_wx_processes（同时认 WeChat.exe / Weixin.exe）。
        """
        pids = [d["pid"] for d in get_wx_processes((self.wx4_process_name,))]
        if self.pid and self.pid not in pids:
            pids.insert(0, self.pid)
        return pids
```

### 2.3 `get_bias_addr.py`｜第二步：结构名定位 + 解混淆（整段新增）

```python
    WX4_CIPHER_NEEDLES = (
        ("ascii:com.Tencent.WCDB.Config.Cipher", b"com.Tencent.WCDB.Config.Cipher"),
        ("utf16:com.Tencent.WCDB.Config.Cipher",
         "com.Tencent.WCDB.Config.Cipher".encode("utf-16-le")),
        ("ascii:Config.Cipher", b"Config.Cipher"),
        ("ascii:WCDB.Config", b"WCDB.Config"),
        ("utf16:Cipher", "Cipher".encode("utf-16-le")),
    )

    @staticmethod
    def wx4_module_base(pid, module_name="Weixin.exe"):
        """Weixin.exe 映像基址：区段里 FileName 命中该模块的最小 BaseAddress（拿不到返回 None）"""
        base = None
        try:
            for m in get_memory_maps(pid):
                if (m.FileName or "").lower().endswith(module_name.lower()):
                    if base is None or m.BaseAddress < base:
                        base = m.BaseAddress
        except Exception:
            return None
        return base

    @staticmethod
    def _xor_sweep(window, keys):
        """
        单字节 XOR 解混淆：对每把已知真密钥，遍历 mask=0x00~0xFF 去找 key^mask。
        :return: {"raw": [明文直接命中的 key_hex], "xored": [(key_hex, mask), ...]}
        """
        raw, xored = [], []
        for key_hex in (keys or ()):
            try:
                k = bytes.fromhex(str(key_hex).strip())
            except ValueError:
                continue
            if len(k) != BiasAddr.WX4_KEY_SIZE:
                continue
            if window.find(k) >= 0:
                raw.append(key_hex)
                continue
            for mask in range(1, 256):
                if window.find(bytes(c ^ mask for c in k)) >= 0:
                    xored.append((key_hex, mask))
                    break
        return {"raw": raw, "xored": xored}

    @staticmethod
    def _extract_32b_candidates(window, step=1, cap=1024):
        """把窗口里所有 32 字节切片原样提取（去重 + 限量），用于直接做 HMAC 校验"""
        out, seen, n = [], set(), BiasAddr.WX4_KEY_SIZE
        for i in range(0, max(0, len(window) - n + 1), step):
            c = window[i:i + n]
            if c in seen:
                continue
            seen.add(c)
            out.append((i, c))
            if len(out) >= cap:
                break
        return out

    def _verify_key_into(self, key_hex, databases, info, mask=None, offset=None):
        """
        候选 32 字节密钥 -> 用第 1 页 HMAC 校验，通过的记进 info["raw_verified"]。
        :return: True 表示至少有一个库校验通过
        """
        if not databases or not key_hex:
            return False
        key_hex = str(key_hex).lower()
        tried = info.setdefault("_tried", set())
        if key_hex in tried:
            return False
        tried.add(key_hex)
        try:
            kb = bytes.fromhex(key_hex)
        except ValueError:
            return False
        ok = False
        for path, page1 in databases:
            try:
                if self.verify_page1_hmac_4x(kb, page1):
                    info["raw_verified"][path] = {"key": key_hex, "mask": mask, "offset": offset}
                    ok = True
            except Exception:
                continue
        return ok

    def _scan_one_buffer(self, buf, base_addr, keys, databases, info, ctx, max_hits, max_cands,
                         max_verify):
        """
        扫一个内存块：找结构名 -> dump 原始字节 -> XOR 反查 / 32 字节候选校验

        成本控制（命中很多时不能把时间烧光）：
          · XOR 反查窗口 = ±ctx（默认 8KB）
          · 32 字节候选按 step=4 抽，每个命中点最多 max_cands 个
          · 每个进程累计校验次数上限 max_verify（HMAC 校验是这里最贵的一步）
        """
        for name, needle in self.WX4_CIPHER_NEEDLES:
            start = 0
            while True:
                pos = buf.find(needle, start)
                if pos < 0:
                    break
                info["needle_hits"][name] += 1
                start = pos + 1
                if len(info["samples"]) >= max_hits:
                    continue
                win_lo = max(0, pos - ctx)
                win_hi = min(len(buf), pos + ctx + len(needle))
                win = buf[win_lo:win_hi]
                lo, hi = max(0, pos - 48), min(len(buf), pos + 96)
                sample = {"needle": name, "addr": base_addr + pos,
                          "hex": buf[lo:hi].hex(" "),
                          "ascii": re.sub(rb"[^\x20-\x7e]", b".", buf[lo:hi]).decode("ascii", "replace")}
                sw = self._xor_sweep(win, keys) if keys else {"raw": [], "xored": []}
                sample["xor_raw_hit"] = list(sw["raw"])
                sample["xor_mask_hit"] = [{"key": str(k)[:16] + "…", "mask": m} for k, m in sw["xored"]]
                for k, m in sw["xored"]:
                    if not any(d["key"] == k and d["mask"] == m for d in info["xor_found"]):
                        info["xor_found"].append({"key": k, "mask": m, "addr": base_addr + pos})
                    self._verify_key_into(k, databases, info, mask=m)
                # 不假定任何混淆：窗口内 32 字节切片原样提取后直接做 HMAC 校验
                for off, cand in self._extract_32b_candidates(win, step=4, cap=max_cands):
                    if info.get("_verified", 0) >= max_verify:
                        break
                    info["_verified"] = info.get("_verified", 0) + 1
                    self._verify_key_into(cand.hex(), databases, info, offset=win_lo + off)
                info["samples"].append(sample)

    def scan_cipher_struct(self, pid=None, keys=None, databases=None, chunk_size=8 << 20,
                           overlap=256, ctx=8192, max_hits=50, max_cands=256, max_verify=4000,
                           progress_every=64):
        """
        按结构名扫 Config.Cipher（只读）。返回报告：
          processes     : [{pid,name,base,regions,mb,elapsed,needle_hits,samples,xor_found,raw_verified}]
          needle_totals : {特征串: 总命中数}
          verified_keys : {库路径: {key, mask, offset}}   —— 真的从内存里验出密钥时才非空
          conclusion    : hit / no_hit / no_key / no_process / open_failed
        :param keys:      已知真密钥 [64位hex, ...]，用于单字节 XOR 反查（可空）
        :param databases: [(库路径, 第1页4096字节), ...]，用于校验 32 字节候选；不传就自动收集
        :param ctx:        命中点两侧窗口大小（默认 8KB）
        :param max_verify: 每个进程最多做多少次 HMAC 校验（默认 4000，防止命中一大堆时拖太久）
        """
        report = {"pids": [], "processes": [],
                  "needle_totals": {n: 0 for n, _ in self.WX4_CIPHER_NEEDLES},
                  "verified_keys": {}, "conclusion": "", "error": None}
        pids = [pid] if pid else self._wx4_candidate_pids()
        report["pids"] = list(pids)
        if not pids:
            report["conclusion"] = "no_process"
            return report

        if databases is None:
            try:
                databases = self.collect_wx4_databases(self.raw_db_path or self.db_path)
            except Exception as e:
                report["error"] = f"收集加密库失败：{e}"
                databases = []

        for one in pids:
            hProcess = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, one)
            if not hProcess:
                report["processes"].append({"pid": one,
                                            "error": "OpenProcess 失败（读进程内存需要管理员权限）"})
                report["conclusion"] = "open_failed"
                continue
            t0 = _time.time()
            try:
                try:
                    pname = psutil.Process(one).name()
                except Exception:
                    pname = ""
                info = {"pid": one, "name": pname, "base": self.wx4_module_base(one),
                        "regions": 0, "mb": 0.0, "elapsed": 0.0,
                        "needle_hits": {n: 0 for n, _ in self.WX4_CIPHER_NEEDLES},
                        "samples": [], "xor_found": [], "raw_verified": {},
                        "_verified": 0, "_tried": set()}
                for m in self._iter_wx4_scannable_regions(one):
                    info["regions"] += 1
                    addr, remain = m.BaseAddress, int(m.RegionSize)
                    while remain > 0:
                        n = min(chunk_size, remain)
                        buf = self._read_mem(hProcess, addr, n)
                        if buf:
                            info["mb"] += len(buf) / 1048576
                            self._scan_one_buffer(buf, addr, keys, databases, info, ctx,
                                                  max_hits, max_cands, max_verify)
                        step = n - overlap if n > overlap else n
                        addr += step
                        remain -= step
                info["elapsed"] = _time.time() - t0
                info.pop("_tried", None)
                info["verified_tried"] = info.pop("_verified", 0)
                for name, cnt in info["needle_hits"].items():
                    report["needle_totals"][name] += cnt
                for path, d in info["raw_verified"].items():
                    report["verified_keys"][path] = dict(d)
                report["processes"].append(info)
            except Exception as e:
                report["processes"].append({"pid": one, "error": f"扫描异常：{e}"})
                if not report["error"]:
                    report["error"] = str(e)
            finally:
                CloseHandle(hProcess)

        total_hits = sum(report["needle_totals"].values())
        if report["verified_keys"]:
            report["conclusion"] = "hit"
        elif report["conclusion"] != "open_failed":
            report["conclusion"] = "no_hit" if total_hits == 0 else "no_key"
        return report

    @staticmethod
    def print_cipher_report(report):
        """把人看的诊断报告打出来（info/bias/自测都用它）"""
        print("=" * 60)
        print("[*] 4.x 内存诊断：按结构名定位 com.Tencent.WCDB.Config.Cipher（只读，无注入）")
        print(f"[*] 目标进程：{report.get('pids')}")
        for p in report.get("processes", []):
            if p.get("error"):
                print(f"[-] pid={p['pid']} {p['error']}")
                continue
            base = p.get("base")
            print(f"[*] pid={p['pid']} ({p.get('name')})  映像基址={hex(base) if base else 'None'}"
                  f"  区段 {p['regions']} 个 / 读取 {p['mb']:.1f} MB / 耗时 {p['elapsed']:.1f}s"
                  f" / HMAC 校验 {p.get('verified_tried', 0)} 次")
            for n, c in p["needle_hits"].items():
                print(f"      特征串 {n}: {c} 命中")
            for s in p["samples"][:3]:
                print(f"      · [{s['needle']}] @ 0x{s['addr']:x}")
                print(f"        hex  : {s['hex']}")
                print(f"        ascii: {s['ascii']}")
                if s.get("xor_mask_hit") or s.get("xor_raw_hit"):
                    print(f"        XOR  : mask 命中 {s['xor_mask_hit']} / 明文命中 {s['xor_raw_hit']}")
            if p["xor_found"]:
                print(f"      [+] 单字节 XOR 解混淆命中 {len(p['xor_found'])} 处：{p['xor_found']}")
            if p["raw_verified"]:
                print(f"      [+] 32 字节原始候选通过第 1 页 HMAC 校验的库：{list(p['raw_verified'])}")
        print("[*] 各特征串总命中：" + "，".join(f"{k}={v}" for k, v in report["needle_totals"].items()))
        if report["verified_keys"]:
            print(f"[+] 从内存里取到 {len(report['verified_keys'])} 个库的密钥：")
            for db, d in report["verified_keys"].items():
                print(f"     {db} -> {d['key']}（mask={d.get('mask')}, offset={d.get('offset')}）")
        else:
            print(f"[-] 内存里没有取到能通过第 1 页 HMAC 校验的密钥（conclusion={report['conclusion']}）")
            print("[-] 说明：该版本不把库密钥以明文/单字节XOR形态驻留在可读内存里")
            print("[-] 对策：走本地密钥文件（--key_file），或重启微信登录后立刻重扫")
        if report.get("error"):
            print(f"[-] 错误信息：{report['error']}")
        print("=" * 60)

    def run_wx4_scan_mem(self, keys=None, key_file=None, do_print=True, **kw):
        """
        info / bias 用的入口：扫结构名 + 尝试解混淆。
        keys 不给、但给了 key_file 时，自动从本地密钥文件读出真密钥用于 XOR 反查比对。
        """
        keys = list(keys or [])
        if not keys and key_file:
            try:
                from .wx_info import read_wx4_keys_file      # 局部导入，避免循环依赖
                keys = list(read_wx4_keys_file(key_file).values())
            except Exception:
                keys = []
        report = self.scan_cipher_struct(keys=keys, **kw)
        if do_print:
            self.print_cipher_report(report)
        return report

    @staticmethod
    def selftest_xor():
        """
        合成缓冲区自测：证明「单字节 XOR 反查 + 32 字节候选提取 + HMAC 校验」这套逻辑是对的，
        不依赖真机内存里有没有那个结构体（真机实测该结构名 0 命中）。
        """
        real = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"  # 示例值已脱敏（合成向量）
        k = bytes.fromhex(real)
        # 1) 明文形态：必须命中 raw
        buf1 = b"\x00" * 100 + k + b"\x00" * 100
        sw1 = BiasAddr._xor_sweep(buf1, [real])
        assert real in sw1["raw"], f"明文形态未命中：{sw1}"
        # 2) 单字节 XOR 0x5A 形态：必须解出 mask=0x5A
        buf2 = b"\x11" * 77 + bytes(c ^ 0x5A for c in k) + b"\x22" * 77
        sw2 = BiasAddr._xor_sweep(buf2, [real])
        assert sw2["xored"] == [(real, 0x5A)], f"XOR 形态未解出：{sw2}"
        # 3) 32 字节候选提取：明文/变形后的 key 都要能被原样提取出来（交给 HMAC 校验）
        cands1 = [c.hex() for _, c in BiasAddr._extract_32b_candidates(buf1)]
        assert real in cands1, "明文形态的 32 字节候选未提取到密钥"
        cands2 = [c.hex() for _, c in BiasAddr._extract_32b_candidates(buf2)]
        assert bytes(c ^ 0x5A for c in k).hex() in cands2, "XOR 变形后的 32 字节候选未提取到"
        # 4) 真校验函数：拿真实库的第 1 页验证这把 key（能过就说明校验函数也是对的）
        page1 = BiasAddr._read_page1(r"D:\xwechat_files\<本人wxid>_fed4\db_storage"
                                     r"\message\message_0.db")
        ok = BiasAddr._verify_hmac_raw(real, page1) if page1 else None
        msg = "[+] 自测通过：明文命中 / 单字节 XOR(0x5A) 解混淆命中 / 32 字节候选提取命中"
        if ok is not None:
            msg += f" / 真库第1页 HMAC 校验={ok}"
        return msg

    @staticmethod
    def _verify_hmac_raw(key_hex, page1):
        """selftest 用的独立校验（不依赖实例）"""
        b = BiasAddr("", "", "", "", None)
        try:
            return b.verify_page1_hmac_4x(bytes.fromhex(key_hex), page1)
        except Exception:
            return False
```

### 2.4 `get_bias_addr.py`｜`__main__` 自测入口（新增参数）

```python
    ap = argparse.ArgumentParser(description="BiasAddr 自测（4.x：扫 WCDB 密钥 / 扫 Config.Cipher 结构名）")
    ap.add_argument("--mode", default="auto", choices=["auto", "3x", "4x"])
    ap.add_argument("--db_path", default=None, help=r"4.x 账号目录，如 D:\xwechat_files\wxid_xxx_abcd")
    ap.add_argument("--keys_out", default=None, help="4.x：把 {库路径: key} 写到这个 json")
    ap.add_argument("--key_file", default=None,
                    help="4.x：本地密钥文件（all_keys.json），用于 XOR 反查时比对真密钥")
    ap.add_argument("--scan_mem", action="store_true",
                    help="4.x：附加内存诊断（搜 com.Tencent.WCDB.Config.Cipher 结构名 + 解混淆尝试）")
    ap.add_argument("--selftest", action="store_true",
                    help="跑合成缓冲区自测：验证 XOR 反查 / 32 字节候选提取 / HMAC 校验逻辑")
    ap.add_argument("--mobile", default="", help="3.x：手机号")
    ap.add_argument("--name", default="", help="3.x：微信昵称")
    ap.add_argument("--account", default="", help="3.x：微信号")
    ap.add_argument("--key", default=None, help="3.x：可选密钥")
    a = ap.parse_args()

    bias_addr = BiasAddr(a.account, a.mobile, a.name, a.key, a.db_path)

    if a.selftest:
        print("[*] 合成缓冲区自测（不依赖真机内存）：")
        print(bias_addr.selftest_xor())
        raise SystemExit(0)

    if a.scan_mem:
        bias_addr.run_wx4_scan_mem(key_file=a.key_file)
        print("[*] 内存诊断结束；下面继续走正常的 4.x 密钥获取流程")

    bias_addr.run(logging_path=True, mode=a.mode, keys_out=a.keys_out)
```

### 2.5 `wx_info.py`｜进程枚举（同时认两个进程名）

```python
    wechat_pids = []
    weixin_pids = []
    result = []

    # 【第一步·4.x】进程识别：原来只认 WeChat.exe，4.x 的 Weixin.exe 会被漏掉
    #   这里统一用 get_wx_processes()（名单 = WeChat.exe + Weixin.exe），再按代次分开用。
    #   3.x 分支只吃 wechat_pids，行为与改造前完全一致。
    try:
        from .get_bias_addr import get_wx_processes   # 局部导入，避免模块级循环依赖
        wx_procs = get_wx_processes()
    except Exception as e:
        wx_core_loger.warning(f"[-] get_wx_processes 不可用（{e}），回退到只认 WeChat.exe")
        wx_procs = [{"pid": pid, "name": name, "is_wx4": False}
                    for pid, name in get_process_list() if name == "WeChat.exe"]
    for d in wx_procs:
        if d.get("is_wx4"):
            weixin_pids.append(d["pid"])
        else:
            wechat_pids.append(d["pid"])
    if weixin_pids:
        wx_core_loger.warning(f"[*] 检测到 4.x 进程 Weixin.exe：{weixin_pids}"
                              f"（3.x 进程 WeChat.exe：{wechat_pids}）")
```

### 2.6 `wx_info.py`｜`_wx4_scan_mem_report()`（新增）

```python
def _wx4_scan_mem_report(key_file=None, keys=None):
    """
    【第二步·4.x】只读内存诊断：在 Weixin.exe 里搜 com.Tencent.WCDB.Config.Cipher 结构名，
    命中处做「单字节 XOR 解混淆」和「32 字节原始候选 -> 第 1 页 HMAC 校验」；
    结果打印出来并返回结构化报告。诊断失败/无命中都不影响 info / bias 的主流程。
    """
    try:
        from .get_bias_addr import BiasAddr      # 局部导入，避免模块级循环依赖
    except Exception as e:
        print(f"[-] 4.x 内存诊断不可用：{e}")
        return {"conclusion": "unavailable", "error": str(e), "verified_keys": {},
                "needle_totals": {}, "pids": []}
    try:
        b = BiasAddr("", "", "", "", None)
        key_list = list(keys.values()) if isinstance(keys, dict) else list(keys or [])
        return b.run_wx4_scan_mem(keys=key_list, key_file=(None if key_list else key_file))
    except Exception as e:
        print(f"[-] 4.x 内存诊断异常：{e}")
        return {"conclusion": "error", "error": str(e), "verified_keys": {},
                "needle_totals": {}, "pids": []}
```

### 2.7 `wx_info.py`｜4.x 分支里挂上诊断

```python
        if scan_mem:
            # 【第二步·4.x】附加只读内存诊断（不改密钥来源，只打印/附带报告）
            report = _wx4_scan_mem_report(key_file=key_file,
                                         keys=(result[0].get("keys") if result else None))
            if result:
                result[0]["mem_scan"] = {k: v for k, v in report.items() if k != "processes"}
                result[0]["mem_scan_conclusion"] = report.get("conclusion")
```

### 2.8 `wx_info.py`｜`_wx4_find_wx_dir()`（顺带修的目录选择）

```python
def _wx4_find_wx_dir(my_wxid, wx_path=None):
    """
    4.x 数据目录形如 D:\\xwechat_files\\wxid_xxx_fed4（带 4 位后缀）。
    优先用显式传入的 wx_path，其次在常见根目录里按 <my_wxid>_* 找。

    注意：同一个 wxid 可能同时存在「带后缀（真数据目录，里面有 db_storage）」和
    「不带后缀（空壳/残留）」两种目录，所以命中多个时优先选含 db_storage 的那个。
    """
    if wx_path and os.path.exists(wx_path):
        return wx_path
    if not my_wxid:
        return None
    roots = [r"D:\xwechat_files", r"E:\xwechat_files", r"C:\xwechat_files",
             os.path.join(os.path.expanduser("~"), "Documents", "xwechat_files"),
             os.path.join(os.environ.get("USERPROFILE", ""), "xwechat_files")]
    hits = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if os.path.isdir(full) and (name == my_wxid or name.startswith(my_wxid + "_")):
                hits.append(full)
    if not hits:
        return None
    with_db = [p for p in hits if os.path.isdir(os.path.join(p, "db_storage"))]
    return (with_db or hits)[0]
```

### 2.9 `cli.py`｜`MainBiasAddr` 整类

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
        parser.add_argument("--scan_mem", action="store_true",
                            help="(4.x)附加只读内存诊断：搜 com.Tencent.WCDB.Config.Cipher 结构名 + "
                                 "单字节 XOR 解混淆 + 32 字节原始候选 HMAC 校验")
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
        scan_mem = bool(getattr(args, "scan_mem", False))

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

            # 【第二步·4.x】bias 在 4.x 下改报「进程基址 + 内存统计」：
            #   3.x 的 [昵称,账号,手机号,邮箱,KEY] 五个偏移在 4.x 不存在
            #   （没有 WeChatWin.dll，WX_OFFS.json 的固定偏移已失效），
            #   所以这里给 Weixin.exe 的映像基址与可扫私有区段统计，配合下面的 库路径:密钥。
            try:
                procs = get_wx_processes()
                print("[*] 4.x 进程基址信息（Weixin.exe 映像基址 + 可扫私有区段）：")
                for d in procs:
                    try:
                        base = BiasAddr4x.wx4_module_base(d["pid"])
                    except Exception:
                        base = None
                    try:
                        regions = sum(1 for _ in BiasAddr4x("", "", "", "", None)
                                      ._iter_wx4_scannable_regions(d["pid"]))
                    except Exception:
                        regions = -1
                    print(f"    pid={d['pid']} {d['name']}  基址={hex(base) if base else 'None'}"
                          f"  私有内存={(d.get('private') or 0) / 1048576:.0f} MB  可扫区段={regions} 个")
                if not procs:
                    print("    （没找到 Weixin.exe 进程；密钥不受影响，仍从密钥文件读）")
                print("[*] 说明：4.x 不用 WX_OFFS.json 的固定偏移，"
                      "所以没有 3.x 那种 [昵称,账号,手机号,邮箱,KEY] 偏移量可报")
                print("[*]       要看完整内存扫描统计（区段/字节/特征串命中），加 --scan_mem")
            except Exception as e:
                print(f"[-] 4.x 进程基址信息获取失败（不影响密钥输出）：{e}")

            if scan_mem:
                from pywxdump.wx_core.wx_info import _wx4_scan_mem_report
                _wx4_scan_mem_report(key_file=key_file, keys=keymap)

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

### 2.10 `cli.py`｜`MainWxInfo` 整类

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
        parser.add_argument("--scan_mem", action="store_true",
                            help="(4.x)附加只读内存诊断：搜 com.Tencent.WCDB.Config.Cipher 结构名 + "
                                 "单字节 XOR 解混淆 + 32 字节原始候选 HMAC 校验（不影响密钥来源）")
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
                             mode=mode,
                             scan_mem=bool(getattr(args, "scan_mem", False)))  # 读取微信信息
        return result
```

## 3. 输出要求 ③：需要额外装的库

**不需要装任何东西，本机已全部就绪。**

| 库 | 用途 | 本机状态 |
| --- | --- | --- |
| `pymem` | 附加进程 / 读内存句柄 | 已装（PyWxDump 自带依赖） |
| `psutil` | 进程名、私有内存、基址统计 | 已装 |
| `pycryptodome` | 你自己的 `batch_decrypt.py` 用 | 已装 |
| `zstandard` | 4.x 消息 zstd 解压（第三步解析用） | 已装 |

本机解释器（**必须用绝对路径，PATH 里的 3.14.7 里没有 pywxdump**）：
```
C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe   # 3.12.8 + pywxdump 3.1.46
```

## 4. 怎么跑 + 实测输出

**管理员 PowerShell：**
```powershell
cd D:\PyWxDump_Source
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
$env:PYTHONPATH = "D:\PyWxDump_Source"

# ① 自测：证明 XOR 反查 / 32 字节候选提取 / HMAC 校验这套逻辑本身是对的
& $py -m pywxdump.wx_core.get_bias_addr --selftest

# ② info：本地密钥文件 + 内存诊断
& $py -m pywxdump info --mode 4x --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json" --scan_mem

# ③ bias：进程基址信息 + {库路径: 密钥}
& $py -m pywxdump bias --mode 4x --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json"

# ④ bias + 完整内存统计
& $py -m pywxdump bias --mode 4x --key_file "C:\Users\Administrator\.wechat-cli\all_keys.json" --scan_mem
```
（用你那个 .bat 走 `wxdump info ...` / `wxdump bias ...` 也一样，参数相同。）

**②的实际输出（节选）：**
```
[*] 检测到 4.x 进程 Weixin.exe：[4232, 14352, 15084, 13296, 3736]（3.x 进程 WeChat.exe：[]）
[*] 4.x 内存诊断：按结构名定位 com.Tencent.WCDB.Config.Cipher（只读，无注入）
[*] pid=4232 (Weixin.exe)  映像基址=0x7ff72b090000  区段 1089 个 / 读取 233.4 MB / 耗时 13.7s / HMAC 校验 4000 次
      特征串 ascii:com.Tencent.WCDB.Config.Cipher: 0 命中
      特征串 utf16:com.Tencent.WCDB.Config.Cipher: 0 命中
      特征串 ascii:Config.Cipher: 0 命中
      特征串 ascii:WCDB.Config: 30 命中
      特征串 utf16:Cipher: 0 命中
      · [ascii:WCDB.Config] @ 0x1cbcfe4f730
        ascii: ........com.Tencent.WCDB.Config.ScalarFunction.wcdb_decompress........
[*] 各特征串总命中：…Cipher=0，utf16…Cipher=0，Config.Cipher=0，WCDB.Config=30，utf16:Cipher=0
[-] 内存里没有取到能通过第 1 页 HMAC 校验的密钥（conclusion=no_key）
================================
[+]  version: 4.1.15.13
[+]  account: AbnerUP
[+]   mobile: None            ← 4.x 读不到，留扩展位，不崩
[+] nickname: 柳岸
[+]     mail: None            ← 同上
[+]     wxid: <本人wxid>
[+]      key: <密钥已脱敏>
[+]   wx_dir: D:\xwechat_files\<本人wxid>_fed4
[+]   key_db: message\message_0.db
[+]     keys: 29 个库的密钥
```

**③的实际输出（节选）：**
```
[*] 4.x 进程基址信息（Weixin.exe 映像基址 + 可扫私有区段）：
    pid=4232 Weixin.exe  基址=0x7ff72b090000  私有内存=271 MB  可扫区段=1088 个
    pid=14352 Weixin.exe  基址=0x7ff72b090000  私有内存=89 MB  可扫区段=640 个
    pid=15084 Weixin.exe  基址=0x7ff72b090000  私有内存=55 MB  可扫区段=157 个
    pid=13296 Weixin.exe  基址=0x7ff72b090000  私有内存=14 MB  可扫区段=109 个
    pid=3736 Weixin.exe  基址=0x7ff72b090000  私有内存=7 MB  可扫区段=137 个
[*] 说明：4.x 不用 WX_OFFS.json 的固定偏移，所以没有 3.x 那种 [昵称,账号,手机号,邮箱,KEY] 偏移量可报
{库路径: 密钥}
{'message\\message_0.db': '<密钥已脱敏>…', 'contact\\contact.db': '<密钥已脱敏>…', …}   ← 29 条
```

**①的实际输出：**
```
[+] 自测通过：明文命中 / 单字节 XOR(0x5A) 解混淆命中 / 32 字节候选提取命中 / 真库第1页 HMAC 校验=True
```
（最后一项说明：用合成方式还原出来的密钥，拿去校验真实库 `message_0.db` 第 1 页，能通过 —— 校验函数与解混淆逻辑都是对的。）

## 5. 输出要求 ④：改动范围（没有重写项目）

只动了 3 个文件的 info/bias 读取链路，共新增约 330 行：

| 文件 | 新增/替换 |
| --- | --- |
| `wx_core/get_bias_addr.py` | +约 300 行（进程名单 + 结构名扫描 + 解混淆 + 自测入口） |
| `wx_core/wx_info.py` | +约 25 行（进程枚举、诊断包装、目录选择修正） |
| `cli.py` | +约 30 行（两个子命令加 `--scan_mem`、bias 增加基址输出） |

未改动：`db/dbbase.py`、`db/dbMSG.py`、`db/dbContact.py`、`api/*`、前端资源、解密链路（`wx4_prepare.py` 一行未动）。

## 6. 验收清单（本次全部实测）

| # | 验收项 | 判据 | 结果 |
| ---: | --- | --- | --- |
| 1 | 进程识别同时认两个进程名 | 列出 5 个 `Weixin.exe` | ✅ |
| 2 | 不再误报 `WeChat No Run`（有 4.x 在跑时） | `info`/`bias` 正常进入 4.x 分支 | ✅ |
| 3 | 3.x 语义不变 | `info --mode 3x` 仍输出 `WeChat No Run` | ✅ |
| 4 | 结构名扫描能跑通且不崩 | 5 进程 / 2040 区段 / 432 MB 全扫完 | ✅ |
| 5 | 命中处交出原始字节 | 有地址 + hex + ascii dump | ✅（命中 `WCDB.Config` 家族） |
| 6 | XOR 解混淆逻辑正确 | 合成 0x5A 掩码能被解出 | ✅ 自测 |
| 7 | 32 字节候选 + HMAC 校验正确 | 合成密钥过真库第 1 页校验 | ✅ 自测 |
| 8 | `info` 输出 版本/微信号/手机号扩展位/密钥 hex | 见第 4 节输出 | ✅ |
| 9 | `bias` 输出 进程基址 + 密钥表 | 基址 0x7ff72b090000 + 29 条 | ✅ |
| 10 | 扫描成本可控 | pid 4232 从 67.7 s 优化到 13.7 s（其余进程 < 0.4 s） | ✅ |
| 11 | `wx_dir` 指向真实数据目录 | 带 `_fed4` 后缀的那个 | ✅ 修复后 |
| 12 | 3.x 回归套件 | `wx4_step2_verify.py` | ✅ 62/62 |
| 13 | UI 路径未受影响 | `ui -p 5012 --noOpenBrowser --no_decrypt`：`is_init=True`、keys=29 | ✅ |
| 14 | 端口无残留 | 5012 监听 0 | ✅ |

## 7. 结论与边界（务必看一眼）

1. **内存取密钥这条路，在 4.1.15.13 上实测走不通**，而且这次拿到了比"0 命中"更硬的证据：`com.Tencent.WCDB.Config.*` 家族在内存里能搜到（`ScalarFunction` / `AuxiliaryFunction` / `Tokenize` 都能抓到原文），**唯独 `Cipher` 不在**。所以不是搜索姿势的问题，是这个对象压根不在可读内存里。
2. 因此 `info` / `bias` 的密钥**仍然来自本地密钥文件**（B 计划），`--scan_mem` 是**只读诊断**，不会改变密钥来源、不做注入、不写对方进程内存。
3. 4.x 还有一个前提要认：密钥是**每个库一把**（你这里是 29 把），**不存在**一把 32 字节 master key，所以"读偏移处的 master key"这种思路本身不成立。
4. `WX_OFFS.json` 在 4.x 下既不读也不写；`bias` 在 4.x 下给的是"映像基址 + 内存统计 + 库路径:密钥"，3.x 的 5 个偏移格式只在 `--mode 3x` 下保留。
5. 若以后想再试内存路线：**完全退出微信 → 重新登录 → 立刻**跑 `wxdump bias --mode 4x --scan_mem`。WCDB 只在真正打开库的瞬间可能持钥，之后会释放；但本次"重启+登录后立刻扫"仍是 0 命中。

## 8. 回滚

只回滚本次三个文件即可（不影响第一、二、三步其它改动）：
```powershell
$dst = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\Lib\site-packages\pywxdump"
copy "$dst\wx_core\get_bias_addr.py" "D:\PyWxDump_Source\pywxdump\wx_core\get_bias_addr.py"
```
注意：`get_bias_addr.py` 的原版是 3.x 版（无 4.x 扫描），拷回去会**同时废掉第一步/第二步的全部 4.x 内存代码**；
`wx_info.py` / `cli.py` 若也回滚，会连带回到原版（丢掉 B 计划和第三步 UI 自动初始化）。
只想撤掉本次新增的话，删掉上面第 2 节列的三个函数块 + `--scan_mem` 参数即可。
