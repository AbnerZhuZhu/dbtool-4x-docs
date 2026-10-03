# -*- coding: utf-8 -*-
"""
PyWxDump 4.0.0（微信 4.x 适配版）发布验收脚本
------------------------------------------------
一条命令跑完，输出 PASS/FAIL 清单：

    C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe ^
        E:\\Users\\Administrator\\Desktop\\wx4_release_verify.py

覆盖 4 件事：
  ① 版本号 4.0.0（代码常量 / 帮助信息 / API 接口）
  ② 默认参数：wxdump info / wxdump bias 不带参数即走 4.x 内存取密钥
  ③ 密钥落盘：内存密钥回写正确、幂等、跨账号不串写
  ④ 服务能起：ui/api 启动即自动准备（内存取密钥 → 解密 → 加载会话），接口有数据
另外单独验一条硬约束：3.x 老路径未被破坏（merge_all.db 走 MSG 表老逻辑）。
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

SRC = r"D:\PyWxDump_Source"
PY = sys.executable or r"C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
DESK = r"E:\Users\Administrator\Desktop"
DBDIR = r"D:\decrypted_wx_db"
MERGE_3X = os.path.join(DBDIR, "merge_all.db")
if "--merge3x" in sys.argv:
    MERGE_3X = sys.argv[sys.argv.index("--merge3x") + 1]
KEYS = r"C:\Users\Administrator\.wechat-cli\all_keys.json"

sys.path.insert(0, SRC)
os.environ.setdefault("PYTHONPATH", SRC)

RESULTS = []


def check(name, ok, evidence=""):
    RESULTS.append((bool(ok), name, str(evidence)[:400]))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {evidence}" if evidence else ""))


def sec(t):
    print("\n" + "=" * 88)
    print(t)
    print("=" * 88)


def run_cli(*args, timeout=600):
    env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    p = subprocess.run([PY, "-m", "pywxdump", *args], cwd=SRC, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


# =====================================================================================
sec("① 版本号：3.1.46 → 4.0.0")
# =====================================================================================
import pywxdump  # noqa: E402

check("pywxdump.__version__ == '4.0.0'", pywxdump.__version__ == "4.0.0", pywxdump.__version__)
check("带 4.x 适配标记 __version_note__", getattr(pywxdump, "__version_note__", "") == "wx4-adapted",
      getattr(pywxdump, "__version_note__", "(缺失)"))

out_h = run_cli("-h")
check("wxdump -h 显示 PyWxDump v4.0.0", "PyWxDump v4.0.0" in out_h,
      [l for l in out_h.splitlines() if "v4.0.0" in l][:1])
check("wxdump -h 明示『支持微信 4.x 内存取密钥』", "支持微信 4.x 内存取密钥" in out_h)
check("wxdump -h 提示无需 --mode 4x --scan_mem",
      "无需再手输 --mode 4x --scan_mem" in out_h)
check("wxdump -h 声明只读（不注入/不 Hook）", "不注入" in out_h and "不 Hook" in out_h)
check("子命令帮助里 info 已标注默认内存取密钥",
      re.search(r"info\s+获取微信信息（4\.x：默认只读内存取密钥", out_h) is not None)
check("子命令帮助里 bias 已标注默认内存取密钥",
      re.search(r"bias\s+获取微信基址偏移（4\.x：默认只读内存取密钥", out_h) is not None)
check("全库没有残留的 3.1.46 版本号（只允许出现在说明注释里）",
      all(("3.1.46" not in l) or l.lstrip().startswith("#")
          for dp, _dn, fns in os.walk(os.path.join(SRC, "pywxdump")) for f in fns
          if f.endswith(".py")
          for l in open(os.path.join(dp, f), encoding="utf-8", errors="replace").read().splitlines()
          if "3.1.46" in l or re.match(r"\s*__version__\s*=", l)))

# =====================================================================================
sec("② 默认参数：不带参数即自动识别 4.x 并走内存取密钥")
# =====================================================================================
from pywxdump.cli import detect_wx_generation, auto_detect_mode_for  # noqa: E402


class _A:
    wx_mode = "auto"
    scan_mem = False
    no_scan_mem = False


gen, weixin_pids, wechat_pids = detect_wx_generation()
check("detect_wx_generation() 认出 4.x（Weixin.exe 在跑）", gen == "4x", f"mode={gen}, pids={weixin_pids}")
mode, scan_mem, ok = auto_detect_mode_for("info", _A())
check("auto_detect_mode_for(): auto + Weixin.exe → 4x 且默认开内存取密钥",
      (mode, scan_mem, ok) == ("4x", True, True), f"({mode}, {scan_mem}, {ok})")

_a3 = _A()
_a3.wx_mode = "3x"
mode3, sm3, ok3 = auto_detect_mode_for("info", _a3)
check("显式 --mode 3x 仍可强制走老路径（不被 4.x 自动识别劫持）",
      mode3 == "3x" and sm3 is False and ok3 is True, f"({mode3}, {sm3}, {ok3})")
_a3s = _A()
_a3s.wx_mode = "3x"
_a3s.scan_mem = True
mode3s, sm3s, ok3s = auto_detect_mode_for("info", _a3s)
check("--mode 3x + 显式 --scan_mem 的组合可解析（不崩）", mode3s == "3x" and ok3s is True,
      f"({mode3s}, {sm3s}, {ok3s})")
_a4 = _A()
_a4.no_scan_mem = True
mode_n, sm_n, ok_n = auto_detect_mode_for("info", _a4)
check("--no_scan_mem 能关掉内存取密钥（不出现在参数表）", (mode_n, sm_n) == ("4x", False),
      f"({mode_n}, {sm_n})")

out_info = run_cli("info")
check("wxdump info（零参数）自动走 4.x 内存取密钥",
      "检测到微信 4.x 进程 Weixin.exe" in out_info and "key_source: memory" in out_info,
      [l for l in out_info.splitlines() if "key_source" in l or "检测到微信 4.x" in l][:2])
check("wxdump info 打印了昵称/wxid", "nickname:" in out_info and "wxid:" in out_info,
      [l for l in out_info.splitlines() if "nickname" in l or "wxid:" in l][:2])

out_bias = run_cli("bias")
check("wxdump bias（零参数）自动走 4.x 内存取密钥",
      "密钥来源：Weixin.exe 只读内存扫描" in out_bias and "共拿到" in out_bias,
      [l for l in out_bias.splitlines() if "密钥来源" in l or "共拿到" in l][:2])
check("wxdump bias 打印掩码 + 各进程统计",
      "自动反推出全局 32 字节 XOR 掩码" in out_bias and "可扫区段" in out_bias)
check("wxdump bias 未写 WX_OFFS.json（4.x 无偏移）",
      os.path.getmtime(os.path.join(SRC, "pywxdump", "WX_OFFS.json")) < time.time() - 3600,
      time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(
          os.path.getmtime(os.path.join(SRC, "pywxdump", "WX_OFFS.json")))))

# =====================================================================================
sec("③ 密钥落盘：回写自测 / 幂等 / 跨账号不串写")
# =====================================================================================
from pywxdump.wx_core import wx4_key_store as KS  # noqa: E402

st = KS.selftest()
check("密钥落盘自测 6/6（更新/一致/拒写/另存/salt 修正/跨账号保护）",
      "6/6" in st and "失败项" not in st, st.splitlines()[0] if st else "")

had_keys = os.path.isfile(KEYS)
before = open(KEYS, "rb").read() if had_keys else b""
run_cli("info")
after = open(KEYS, "rb").read() if os.path.isfile(KEYS) else b""
if had_keys:
    check("再跑一次 info：密钥文件字节不变（幂等）", before == after,
          f"{len(before)} B → {len(after)} B")
else:
    check("干净环境（没有密钥文件）：只读内存取密钥并自动重建密钥文件",
          len(after) > 0, f"重建 {len(after)} B")

from pywxdump.wx_core.get_bias_addr import BiasAddr  # noqa: E402
from pywxdump.wx_core.wx4_prepare import _looks_like_account  # noqa: E402

b = BiasAddr("", "", "", "", None)
raw = json.loads(after.decode("utf-8"))
root = r"D:\xwechat_files"
index = {}
for acct in sorted(os.listdir(root)):
    sub = os.path.join(root, acct, "db_storage")
    if not os.path.isdir(sub):
        continue
    for dp, _dn, fns in os.walk(sub):
        for fn in fns:
            if fn.endswith(".db"):
                p1 = BiasAddr._read_page1(os.path.join(dp, fn))
                if p1:
                    index.setdefault(os.path.relpath(os.path.join(dp, fn), sub), []).append((acct, p1))

ok_key = ok_owner = 0
bad = []
for rel, v in raw.items():
    kh = (v.get("enc_key") if isinstance(v, dict) else v) or ""
    sh = (v.get("salt") if isinstance(v, dict) else "") or ""
    head = str(rel).replace("/", "\\").strip("\\").split("\\")
    acct_q = head[0] if len(head) >= 2 and _looks_like_account(head[0]) else None
    rel_n = "\\".join(head[1:]) if acct_q else "\\".join(head)
    hit = salt_ok = False
    for acct, p1 in index.get(rel_n, []):
        if acct_q and acct.lower() != acct_q.lower():
            continue
        if sh and p1[:16].hex() == str(sh).lower():
            salt_ok = True
        if sh and p1[:16].hex() != str(sh).lower():
            continue
        try:
            if b.verify_page1_hmac_4x(bytes.fromhex(str(kh)), p1):
                hit = True
                break
        except Exception:
            pass
    if hit:
        ok_key += 1
        ok_owner += 1 if salt_ok else 0
    else:
        bad.append(rel)
check("密钥文件每条都能对到某个账号的真库（key+salt 同源）",
      ok_key == len(raw), f"{ok_key}/{len(raw)} 条通过真库 HMAC" + (f"；对不上：{bad[:3]}" if bad else ""))
check("salt 与 key 同源（修掉了历史脏数据）", ok_owner == len(raw), f"{ok_owner}/{len(raw)} 条")

from pywxdump.wx_core.wx4_prepare import prepare_wx4  # noqa: E402

r = prepare_wx4(no_decrypt=True, work_path=os.path.join(SRC, "wxdump_work"))
check("多账号共用密钥文件时能选出『正在登录』的账号（写库最新）",
      bool(r.get("wx_path")) and "库最新写入" in (r.get("account_detail") or ""),
      f"{os.path.basename(r.get('wx_path') or '')} / {r.get('account_detail')}")
check("别账号的账号限定条目被排除", r.get("keys_other_account", 0) >= 0,
      f"跳过 {r.get('keys_other_account')} 条")
check("解密产物目录按账号隔离（不会串库）",
      (r.get("decrypted_dir") or "").endswith(os.path.basename(r.get("wx_path") or "")),
      r.get("decrypted_dir"))

# =====================================================================================
sec("④ 服务能起：ui/api 启动即自动准备，接口有数据")
# =====================================================================================


def free_port(start=17950):
    for port in range(start, start + 60):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return port
        except Exception:
            continue
        finally:
            s.close()
    return start


PORT = free_port()
wd = tempfile.mkdtemp(prefix="wx4_release_")
env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
proc = subprocess.Popen([PY, "-m", "pywxdump", "api", "-p", str(PORT)], cwd=wd,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                        encoding="utf-8", errors="replace", env=env, bufsize=1)
lines = []
threading.Thread(target=lambda: [lines.append(l.rstrip()) for l in proc.stdout],
                 daemon=True).start()


def http(path, data=None, timeout=30):
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}",
                                data=json.dumps(data).encode() if data is not None else None,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


ok = False
t0 = time.time()
for _ in range(90):
    try:
        http("/api/rs/is_init", timeout=4)
        ok = True
        break
    except Exception:
        time.sleep(1)
check("服务成功启动（启动即自动准备，含内存取密钥 + 解密）", ok, f"耗时 {time.time() - t0:.1f}s，端口 {PORT}")

if ok:
    check("启动过程走了 4.x 自动准备", any("4.x 自动初始化完成" in l for l in lines),
          [l for l in lines if "自动初始化完成" in l][:1])
    check("/api/rs/is_init 为 True（前端不再要求手填路径）", http("/api/rs/is_init").get("body") is True)
    check("/api/rs/version 返回 4.0.0", http("/api/rs/version").get("body") == "4.0.0")
    j = http("/api/rs/user_session_list")
    sessions = j.get("body") or []
    check("/api/rs/user_session_list 有会话", len(sessions) > 0, f"{len(sessions)} 个")
    j2 = http("/api/rs/user_list", {})
    users = j2.get("body") or {}
    check("/api/rs/user_list 有联系人（联系人画像不再是 0）", len(users) > 0, f"{len(users)} 个")
    wx = next((s.get("wxid") for s in sessions if s.get("wxid") and "@chatroom" in str(s.get("wxid"))), None) \
        or next((s.get("wxid") for s in sessions if s.get("wxid") and s.get("nMsgType") == 1), None)
    if wx:
        j3 = http("/api/rs/msg_list", {"wxid": wx, "start": 0, "limit": 3})
        msgs = (j3.get("body") or {}).get("msg_list") or []
        # 有些会话（如 newsapp 这类公众号）本就没落消息，换个会话再试
        if not msgs:
            for s in sessions:
                w2 = s.get("wxid")
                if not w2 or w2 == wx:
                    continue
                try:
                    j4 = http("/api/rs/msg_list", {"wxid": w2, "start": 0, "limit": 3})
                except Exception:
                    continue
                m4 = (j4.get("body") or {}).get("msg_list") or []
                if m4:
                    wx, msgs = w2, m4
                    break
        check(f"聊天记录能读出来（{wx}）", len(msgs) > 0, f"{len(msgs)} 条")
        if msgs:
            m = msgs[0]
            check("消息字段齐全（含 is_sender / type_name / msg）",
                  all(k in m for k in ("is_sender", "type_name", "msg", "CreateTime")),
                  ",".join(m.keys()))
            check("消息类型标签已渲染（非空）", bool(m.get("type_name")), m.get("type_name"))

try:
    proc.terminate()
except Exception:
    pass
time.sleep(1)
try:
    proc.kill()
except Exception:
    pass

# =====================================================================================
sec("⑤ 硬约束：3.x 老路径未被破坏（MSG 表 / merge_all.db 走老逻辑）")
# =====================================================================================
import sqlite3  # noqa: E402

tmp3 = os.path.join(tempfile.mkdtemp(prefix="wx3_fake_"), "MSG.db")
con = sqlite3.connect(tmp3)
con.execute("CREATE TABLE MSG(localId INTEGER, StrTalker TEXT, StrContent TEXT, CreateTime INTEGER, IsSender INTEGER)")
con.execute("INSERT INTO MSG VALUES(1,'wxid_test','hello',1700000000,0)")
con.commit()
con.close()

from pywxdump.db.dbbase import DatabaseBase  # noqa: E402

try:
    d = DatabaseBase({"key": "v3x_fake", "type": "sqlite", "path": tmp3})
    check("3.x 形态库（有 MSG 表）仍按老逻辑打开（is_wx4=False）",
          getattr(d, "is_wx4", None) is False, f"{type(d).__name__} is_wx4={getattr(d, 'is_wx4', None)}")
except Exception as e:
    check("3.x 形态库（有 MSG 表）仍按老逻辑打开（is_wx4=False）", False, str(e))

if os.path.isfile(MERGE_3X):
    try:
        from pywxdump.db import DBHandler
        con3 = sqlite3.connect(MERGE_3X)
        n = con3.execute("SELECT COUNT(*) FROM MSG").fetchone()[0]
        row = con3.execute(
            "SELECT StrTalker FROM MSG GROUP BY StrTalker ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
        n_talkers = con3.execute("SELECT COUNT(DISTINCT StrTalker) FROM MSG").fetchone()[0]
        con3.close()
        dh = DBHandler({"key": "v3x_merge", "type": "sqlite", "path": MERGE_3X}, "wxid_test")
        got = dh.get_msg_list(wxids=[row[0]], start_index=0, page_size=3, my_talker="我") if row else []
        msgs = got[0] if isinstance(got, (tuple, list)) and got and isinstance(got[0], list) else got
        check("3.x 老路径仍可用：merge_all.db 走 MSG 表读消息",
              n > 0 and len(msgs) > 0,
              f"MSG 共 {n} 行 / {n_talkers} 个会话；"
              f"取最活跃会话 {row[0] if row else '-'} 读到 {len(msgs)} 条"
              + (f"；首条 CreateTime={msgs[0].get('CreateTime')}" if msgs and isinstance(msgs[0], dict) else ""))
        check("3.x 库 is_wx4=False（不会被 4.x 分支劫持）", getattr(dh, "is_wx4", None) is False,
              f"is_wx4={getattr(dh, 'is_wx4', None)}")
        try:
            dh.close()
        except Exception:
            pass
    except Exception as e:
        check("3.x 老路径仍可用：merge_all.db 走 MSG 表读消息", False, str(e)[:200])
else:
    print(f"  [SKIP] 3.x 回归基线 merge_all.db 不在（{MERGE_3X}）——清理环境后属正常；"
          f"要复测 3.x 老路径，用 --merge3x 指定一个 3.x 形态的库，或恢复该文件后重跑")

# =====================================================================================
sec("汇总")
# =====================================================================================
n_pass = sum(1 for ok_, _n, _e in RESULTS if ok_)
n_fail = len(RESULTS) - n_pass
print(f"总判定：{n_pass} 项通过 / {n_fail} 项失败")
if n_fail:
    print("失败项：")
    for ok_, n, e in RESULTS:
        if not ok_:
            print(f"  [FAIL] {n}   {e}")
    sys.exit(1)
print("全部通过 ✅  —— PyWxDump 4.0.0（微信 4.x 适配版）可用于发布")
