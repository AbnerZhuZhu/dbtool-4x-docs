# 微信 4.1.15.13 · PyWxDump 改造【第四步】内存取密钥（info / bias 已跑通）

> 宿主：`D:\PyWxDump_Source\`（PyWxDump 3.1.46 本地副本，只改这里）
> 解释器：`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`（3.12.8）
> 微信：4.1.15.13，进程 `Weixin.exe` ×5（pid 4232 / 14352 / 15084 / 13296 / 3736）
> 本轮新增依赖：**无**（复用已有的 numpy 2.4.4 / pycryptodome / psutil / pymem）
> 运行前提：微信**已登录**且进程在跑；只申请 `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ`，**只读**，不注入、不 Hook、不改微信文件、不需要管理员

---

## 0. 结论先行（一句话）

**不用密钥文件、不用锚点，`wxdump info --mode 4x --scan_mem` 现在能直接从 `Weixin.exe` 内存里把密钥取出来**：
本机实测 6.6 秒扫完 224 MB，**自动反推出 32 字节全局 XOR 掩码，拿到 20 把通过真库第 1 页 HMAC-SHA512 校验的密钥**（含 1 把 `all_keys.json` 里已经过期、只有内存里才是最新值的 `message_resource.db` 密钥）。

```
[+] 自动反推出全局 32 字节 XOR 掩码：450048844c2448488944254048584c24d2c7442458020000004889442450488b
[+] 从内存校验通过的密钥：20 把
[+] 已从 Weixin.exe 内存直接取到 20 把密钥，本次不需要 --key_file
[+] 数据来源：已解密数据库 + Weixin.exe 只读内存扫描得到的密钥（未注入、未 Hook、未修改微信文件）
[+]  version: 4.1.15.13
[+]  account: AbnerUP
[+] nickname: 柳岸
[+]     wxid: <本人wxid>
[+] key_source: memory
```

---

## 1. 先纠正两条前提（实测证据，避免继续走死路）

| 原假设 | 实测结论 | 证据 |
| --- | --- | --- |
| 内存里有 `com.Tencent.WCDB.Config.Cipher` 结构体，读它偏移处的 32 字节 master key | ❌ **该结构名不在可读内存里**；同家族的 `com.Tencent.WCDB.Config.ScalarFunction.wcdb_decompress`、`…AuxiliaryFunction.substring_match_info`、`…Tokenize.MMFtsTokenizer` 都能搜到（`ascii:WCDB.Config` 命中 30 处），**唯独没有 `Cipher`** | `wx4_cipher_scan_probe.py` 全 0 命中；`t43_info_scanmem2.log` 的 30 处命中原文 |
| 4.x 是"一把主密钥" | ❌ 4.x 是**每个库一把密钥**（`all_keys.json` 里 29 把） | 密钥文件结构与逐库 HMAC 校验结果 |
| 密钥不在内存里，只能退回密钥文件 | ❌ **密钥明文确实在内存里**，只是被一份 **32 字节固定 XOR 掩码**逐字节保护 | 掩码反推 + 20 把真库 HMAC 通过 |

**真正的机制（本机实测确定）**

- 明文形态：`x'<64位hex密钥><32位hex salt>'`，共 99 字节，与 3.x/密钥文件里的形态一致；
- 保护方式：`内存字节[x] = 明文[x] ^ 掩码[x % 32]`，**掩码是 32 字节常量、全进程共用同一份，索引按"绝对地址 % 32"**；
- 本机掩码值（32 字节，可直接复用）：

```
450048844c2448488944254048584c24d2c7442458020000004889442450488b
```

- `salt` 就是每个真库文件的**前 16 字节**（明文，不是秘密）——这一点是整套算法的关键抓手。

---

## 2. 算法（三步，已在真机上闭环）

```
①差分预筛 + 熵过滤     隔 32/64 字节 XOR 必落在 {hex^hex} 集合内（掩码被消掉）
                      + 窗口内"相邻字节变化次数 ≥ 40"，把内存里大量常量垃圾区踢掉
                      → 224 MB 里只留个位数候选窗口
②salt 假设反推掩码     密钥串第 66..97 共 32 个字符正好"每个残类各覆盖一次"，
                      假设 salt = 某个真库 salt，则 32 个掩码字节被一次性唯一确定；
                      再用形状（首字节 x、次字节 '、末字节 '、其余 96 位必须 hex）校验
③全局解码 + HMAC 校验   用掩码解码整块内存 → 正则捞出所有 x'…' 串 →
                      逐把用真库第 1 页 HMAC-SHA512 校验，**只有通过的才算数**
```

第 ② 步是本轮的突破口：它把"枚举掩码"这种不可能的组合爆炸，变成"273 个 salt 逐个试"，实测 **0.76 秒/块** 就唯一命中，且与锚点、与密钥文件完全无关。

---

## 3. 改动清单（4 个文件）

### 3.1 新增核心模块 `pywxdump\wx_core\wx4_xor_scan.py`

```python
def find_masked_candidates(buf, base_addr, max_cand=None, min_changes=40):
    """【与掩码无关】的差分预筛：找出可能是"掩码保护的 x'<64hex><32hex>' 串"的窗口起点。
    判据 1：隔 32 / 隔 64 字节的 XOR 必须落在 {hex^hex} 内（掩码相消）
    判据 2：窗口内相邻字节"变化次数 ≥ min_changes" —— 踢掉常量/低熵垃圾区
            （否则它们 XOR 恒为 0，会全部通过判据 1，把候选表刷爆）
    """
```

```python
def recover_mask_via_salts(buf, base_addr, salts, log=None, max_windows=200000,
                           min_distinct=32, max_mode_ratio=0.3):
    """【精确、快速】用磁盘真库的 salt 作假设，一步反推全局 32 字节掩码。
    salt 的 32 个字符正好"每个残类各覆盖一次"→ 每个 salt 假设唯一确定整份掩码；
    注意：写入掩码时要用 (base_addr + A + 66 + i) % 32 这个【残类下标】，
    不能直接用 salt 字符的序号（两者差一个 (base_addr+A+66)%32 的旋转）。
    """
```

```python
def _harvest_keys(buf, base_addr, mask):
    """用给定掩码全局解码缓冲区，收集所有合法密钥串"""

def recover_mask_from_candidates(buf, base_addr, salts=None, log=None):
    """先走"真库 salt 假设"精确路线；不可用时退回"形状投票"路线"""

def collect_salts_from_db_files(root=r"D:\xwechat_files"):
    """收集磁盘上所有真库文件开头的 16 字节 salt（hex，小写），用于消歧与校验"""

def extract_keys_from_memory(pids=None, page1_map=None, chunk_size=16 << 20,
                             time_budget=180.0, valid_salts=None, log=print):
    """【无密钥文件、无锚点】地从 Weixin.exe 内存里取出所有 SQLCipher 密钥"""
```

同时保留/重写的自测：`selftest()`（已知明文 + 周期掩码还原）、`selftest_blind()`（**不给任何密钥、不给锚点**，18 组不同基址/相位全部精确还原 32 字节掩码）。

### 3.2 `pywxdump\wx_core\get_bias_addr.py`

新增 3 个成员（其余老逻辑未动）：

```python
    def run_wx4_xor_keys(self, wx_root=None, pids=None, time_budget=300, do_print=True,
                         log=print):
        """4.x 内存取密钥（只读）正式入口：形状预筛 + 真库 salt 反推掩码 + 第 1 页 HMAC 校验"""
        from .wx4_xor_scan import (extract_keys_from_memory, collect_salts_from_db_files,
                                   build_page1_map)
        root = wx_root or r"D:\xwechat_files"
        page1_map = build_page1_map(root)
        salts = collect_salts_from_db_files(root)
        rep = extract_keys_from_memory(pids=pids, page1_map=page1_map, valid_salts=salts,
                                       time_budget=time_budget,
                                       log=log if do_print else (lambda *a: None))
        rep["salts"], rep["verifiable"] = len(salts), len(page1_map)
        self.print_xor_key_report(rep)
        return rep

    @staticmethod
    def print_xor_key_report(rep):
        """打印：掩码 / 各进程统计 / 逐库密钥 / 无对应 salt 的串"""

    @staticmethod
    def selftest_xor_scan():
        """新模块自测：掩码还原 + 无锚点盲反推"""
```

`__main__` 参数变化（自测入口）：

```python
    ap.add_argument("--scan_mem", action="store_true",
                    help="4.x：只读内存取密钥（形状预筛 + 真库 salt 反推 32 字节 XOR 掩码 + 第 1 页 HMAC 校验）")
    ap.add_argument("--scan_mem_struct", action="store_true",
                    help="4.x：旧诊断——搜 com.Tencent.WCDB.Config.Cipher 结构名（实测不在可读内存）")
    ap.add_argument("--wx_root", default=None, help=r"4.x：微信数据根目录（默认 D:\xwechat_files）")
    ap.add_argument("--mem_budget", type=float, default=300, help="4.x：每进程扫描时间上限（秒）")
```

### 3.3 `pywxdump\wx_core\wx_info.py`

新增内存取密钥入口 + `get_wx_info` 参数扩展：

```python
def _wx4_mem_keys_report(wx_root=None, time_budget=300, pids=None, do_print=True):
    """【4.x·内存取密钥】只读内存扫描的正式入口；失败/无命中都不抛异常，不影响 info/bias 主流程"""
```

```python
def get_wx_info(WX_OFFS=None, is_print=False, save_path=None,
                decrypted_dir=None, my_wxid=None, wx_path=None, keys_file=None,
                mode=None, key_file=None, scan_mem=False,
                wx_root=None, mem_time_budget=300):
    ...
    if mode == "4x":
        mem_report = None
        if scan_mem:
            mem_report = _wx4_mem_keys_report(wx_root=wx_root, time_budget=mem_time_budget)
            if mem_report.get("keys") and not key_file:      # ← 没给密钥文件：直接用内存里的
                mem_keys = dict(mem_report["keys"])
                result = get_wx_info_from_db(decrypted_dir=decrypted_dir, my_wxid=my_wxid,
                                             wx_path=wx_path, keys=mem_keys)
                if result:
                    result[0]["key_source"] = "memory"
                    result[0]["mem_scan"] = {k: v for k, v in mem_report.items() if k != "processes"}
                    result[0]["mem_scan_conclusion"] = "ok"
        if not result:                                      # ← 内存没拿到才回落密钥文件（B 计划不回归）
            result = get_wx_info_from_db(..., key_file=key_file)
```

注意两个坑（都已修）：
1. 内存分支拿到 `result` 后，**不能再无条件**跑一遍密钥文件分支，否则 `key_source` 会被覆盖掉；
2. `report["mask"]` 存的是 hex 字符串，解码时要用另存的 **bytes** 掩码，否则第二个进程会 `TypeError`。

### 3.4 `pywxdump\cli.py`

```python
# info 子命令新增
        parser.add_argument("--scan_mem", action="store_true",
                            help="(4.x)只读内存取密钥：形状预筛 + 真库 salt 反推 32 字节 XOR 掩码 + "
                                 "第 1 页 HMAC 校验；不给 --key_file 时直接用内存里拿到的密钥")
        parser.add_argument("--wx_root", type=str, default=None,
                            help=r"(4.x)微信数据根目录，默认 D:\xwechat_files")
        parser.add_argument("--mem_budget", type=float, default=300,
                            help="(4.x)每个进程内存扫描时间上限（秒），默认 300")
```

```python
# bias 子命令的 4.x 分支：无密钥文件时用内存密钥
        if mode == "4x" or key_file or scan_mem:
            mem_report = None
            if scan_mem:
                from pywxdump.wx_core.wx_info import _wx4_mem_keys_report
                mem_report = _wx4_mem_keys_report(
                    wx_root=getattr(args, "wx_root", None),
                    time_budget=float(getattr(args, "mem_budget", 300) or 300))
            keymap = read_wx4_keys_file(key_file) if key_file else {}
            if not keymap and not key_file and mem_report and mem_report.get("keys"):
                keymap = dict(mem_report["keys"])
```

---

## 4. 命令与验收（全部已实测）

### 4.1 自测（不碰真机内存，30 秒内出结果）

```powershell
cd D:\PyWxDump_Source
$env:PYTHONPATH = "D:\PyWxDump_Source"
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -m pywxdump.wx_core.get_bias_addr --selftest
```

期望（实测一致）：

```
[+] 自测通过：明文命中 / 单字节 XOR(0x5A) 解混淆命中 / 32 字节候选提取命中 / 真库第1页 HMAC 校验=True
[+] 自测通过：周期 [1, 2, 4, 8, 16, 32] 全部命中且掩码/明文均正确；还原出的密钥对 bizchat\bizchat.db 第 1 页 HMAC 校验 = True
[+] 盲扫自测通过：18 组（不同基址/相位）全部精确恢复 32 字节掩码
```

### 4.2 只读内存取密钥（**不需要任何密钥文件**）

```powershell
cd D:\PyWxDump_Source
$env:PYTHONPATH = "D:\PyWxDump_Source"
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -m pywxdump info --mode 4x --scan_mem
```

验收清单（本轮实测值）：

| # | 检查点 | 期望 | 实测 |
| --- | --- | --- | --- |
| 1 | 识别 4.x 进程 | 列出 5 个 `Weixin.exe` pid | ✅ 4232/14352/15084/13296/3736 |
| 2 | 真库 salt 参照集 | 273 个 | ✅ 273 |
| 3 | 自动反推掩码 | 32 字节 hex | ✅ `4500…488b`（与第 1 节常量逐字一致） |
| 4 | 单进程耗时 | 秒级 | ✅ pid 4232：1092 区段 / 225 MB / **6.6 s** |
| 5 | 密钥数 | > 0 且全部过 HMAC | ✅ **20 把** |
| 6 | 账号信息 | 昵称/账号/wxid | ✅ 柳岸 / AbnerUP / `<本人wxid>` |
| 7 | 密钥来源标记 | `key_source: memory` | ✅ |
| 8 | 其它 4 个进程 | 0 把（密钥只在主进程） | ✅ 0/0/0/0 |

### 4.3 `bias` 同样支持

```powershell
& "…python.exe" -m pywxdump bias --mode 4x --scan_mem
```

实测输出：掩码 + 各进程基址/私有内存/可扫区段 + `{库路径: 密钥}` **20 条**，并打印
`[+] 密钥来源：Weixin.exe 只读内存扫描（20 把，无需密钥文件）`。

### 4.4 零回归复测

| 项 | 命令 | 实测 |
| --- | --- | --- |
| 3.x 老逻辑（62 项） | `python E:\Users\Administrator\Desktop\wx4_step2_verify.py` | ✅ **62 项通过 / 0 项失败** |
| B 计划（密钥文件 29 把） | `python -m pywxdump info --mode 4x --key_file C:\Users\Administrator\.wechat-cli\all_keys.json` | ✅ 仍是 29 把，账号信息正常 |
| 老结构名诊断 | 加 `--scan_mem_struct` | ✅ 保留可复测（结论仍为 0 命中） |

### 4.5 纯盲扫（连 salt 参照都不给也行）

```powershell
& "…python.exe" -m pywxdump.wx_core.wx4_xor_scan --blind --time_budget 300
```

期望：掩码 + `从内存校验通过的密钥：20 把`。这条路径**不读 `all_keys.json`**（合成自测里连 salt 参照都不给，也能精确还原掩码）。

---

## 5. 回滚

只需还原两个文件即可回到上一步状态（密钥文件路线）：

```
pywxdump\wx_core\wx4_xor_scan.py     ← 新增文件，删除即回滚
pywxdump\wx_core\wx_info.py          ← 去掉 scan_mem 的内存分支，保留原有 key_file 分支
pywxdump\cli.py                      ← 去掉 --scan_mem/--wx_root/--mem_budget 三个参数
pywxdump\wx_core\get_bias_addr.py    ← 去掉 run_wx4_xor_keys / print_xor_key_report / selftest_xor_scan
```

原版参照（可对照回滚）：`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\Lib\site-packages\pywxdump\`

---

## 6. 遗留 / 未决

1. **10 个串在本机 273 个库里找不到对应 salt**（形如 `e96a8df3a53a2d45…/ab6eed90…`）。可能是其它账号的库、或已轮换的旧密钥。要不要一并写盘保存，请拍板（**未擅自写**）。
2. **`message_resource.db` 在 `all_keys.json` 里已过期**，内存里拿到的才是最新的
   （`<密钥已脱敏>`，真库第 1 页 HMAC 通过）。
   要不要回写 `C:\Users\Administrator\.wechat-cli\all_keys.json`？**等你确认后再动**。
3. 本轮取到的 20 把里，有 9 把（如 `message_8.db`、`sns.db`、`weclaw.db` 等）**不在内存命中列表**里 —— 它们要么此刻没被 WCDB 缓存，要么在其它进程/区段被换出。若需要"内存 + 文件"取并集，可在下一步加一个合并逻辑（当前未做）。
4. `--scan_mem` 目前**同时**保留在 `info` 与 `bias`；`--scan_mem_struct` 作为历史诊断保留。是否精简请你决定。

---

## 7. 复现用到的临时脚本与日志（都在 `C:\Users\Administrator\AppData\Local\Temp\`）

| 文件 | 用途 |
| --- | --- |
| `p28_dbg_blind_live.py` | 在真实内存窗口上验证"掩码反推"链路（本轮定位到两处 bug 的现场） |
| `p29_dbg_salt_hypo.py` / `p30_dbg_byte.py` / `p31_replicate.py` | 逐字节定位"salt 假设"错位问题 |
| `t76_dbg_blind_live.log` | 修复后首次精确还原掩码（0.76 s，解出 5 串、3 串 salt 命中） |
| `t78_blind_live.log` | 全进程盲扫：掩码 + 20 把密钥 |
| `t82/t83_info_scanmem.log` | `info --mode 4x --scan_mem` 验收 |
| `t84_bias_scanmem.log` | `bias --mode 4x --scan_mem` 验收 |
| `t85_step2_verify.log` | 3.x 回归 62/62 |
| `t86_info_keyfile.log` | B 计划（密钥文件）不回归 |
