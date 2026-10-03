# -*- coding: utf-8 -*-
"""
export_for_finetune.py —— 微信 4.x 已解密库 → 微调数据（对话对）JSONL

================================================================
输出文件（唯一、写死）：D:\\wx_finetune_data.jsonl
运行命令：python "E:\\Users\\Administrator\\Desktop\\export_for_finetune.py"
================================================================

已确认的环境事实（微信 4.1.15.13）
  · 解密库目录：D:\\PyWxDump_Source\\wxdump_work\\decrypted_wx4\\<新号账号名>
  · 消息库文件名形如 message_<数字>_decrypted.db；message_fts / message_resource / media 等
    辅助库不含消息表，自动跳过。
  · 消息表是 Msg_<md5(会话 wxid)>（一个会话可能被拆在多个库的多张分表里）。
  · 收发判定：real_sender_id 去本库 Name2Id 查（SELECT rowid, user_name FROM Name2Id），
    等于本人 wxid 的那条 rowid 就是"我发的"，不能假设它等于 1。
  · 正文 message_content：WCDB_CT_message_content=0 是明文，=4 是 zstd 压缩（magic 28 b5 2f fd）。
    解压需要 zstandard；没装也不崩，只跳过压缩消息并打印提示。
  · local_type：低 32 位是消息类型，1 = 文本；其它（图片/链接/系统…）不作为文本样本。

产出格式（每行一条 JSON，OpenAI messages 风格，方便喂 LLaMA-Factory / 各大框架）
  {"messages":[{"role":"user","content":"对方上一条"},
               {"role":"assistant","content":"我的回复"}],
   "meta":{"talker":"wxid_xxx","ask_time":"2026-09-27 12:00:00","reply_time":"2026-09-27 12:00:11"}}

容错设计（对应本次要求）
  1) 输出路径写死并在启动时打印，跑完再打印实际落盘大小。
  2) 每个库单独 try/except：没有 Msg_ 表、表名对不上、库是空的 → 打印一行"跳过"继续下一个，绝不中断。
  3) 每处理一个库都打印"正在处理 message_X.db，提取到 Y 条对话对"。
  4) 分批落盘：攒够 BATCH_SIZE 条就写文件并 flush，绝不把全量数据堆在内存里
     （按 talker 做多路流式归并，内存占用与消息总数无关，切到 59 万条的大号也不会爆）。
  5) 过滤逻辑：只取文本、剥掉群聊的"发送者:\\n"前缀、超时(默认 6 小时)不成对、
     空内容/纯 XML 跳过；无法反查会话的表明确记为"跳过"而不是静默丢弃。
"""

import glob
import hashlib
import heapq
import json
import os
import re
import sqlite3
import sys
import time

# ======================= 配置区 =======================
DEC_DIR = r"D:\PyWxDump_Source\wxdump_work\decrypted_wx4\wxid_ki0yoyssaae712_aa50"
OUT_PATH = r"D:\wx_finetune_data.jsonl"

BATCH_SIZE = 100          # 每攒够多少条就落盘 + flush 一次
MAX_GAP_SECONDS = 6 * 3600   # "对方一句 → 我一句"的最大间隔，超过就不当成一组问答
SKIP_CHATROOM = False     # True = 只导出单聊，丢掉群聊
MIN_TEXT_LEN = 1          # 正文至少多长才算有效样本
MAX_TEXT_LEN = 2000       # 过长的正文截断，避免一条撑爆样本

MSG_DB_RE = re.compile(r"^message_(\d+)_decrypted\.db$", re.I)
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
MSG_TABLE_PREFIX = "Msg_"

try:
    import zstandard
except Exception:
    zstandard = None


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def md5hex(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def decode_content(data, ct=None):
    """把 message_content 还原成文本：str 直接用；zstd magic 解压；其它按 utf-8 宽松解。"""
    if isinstance(data, memoryview):
        data = bytes(data)
    if isinstance(data, str):
        return data
    if not isinstance(data, (bytes, bytearray)) or not data:
        return ""
    data = bytes(data)
    if data[:4] == ZSTD_MAGIC:
        if zstandard is None:
            return None            # None = 压缩消息但没装 zstandard，由调用方计数跳过
        try:
            return zstandard.ZstdDecompressor().decompressobj().decompress(data).decode("utf-8", "replace").rstrip("\x00")
        except Exception:
            return ""
    return data.decode("utf-8", "replace").rstrip("\x00")


PREFIX_RE = re.compile(r"^([A-Za-z0-9_\-\.@]{4,80}):\n")


def strip_sender_prefix(text, known_ids):
    """剥掉群聊正文最前面的「发送者:\\n」，只在确实是账号时才剥，避免误伤 '12:30' 这类文本。"""
    m = PREFIX_RE.match(text)
    if not m:
        return text
    p = m.group(1)
    if p in known_ids or p.startswith("wxid_") or p.endswith("@chatroom") or p.endswith("@openim"):
        return text[m.end():]
    return text


def cleanup_text(text):
    if not text:
        return ""
    text = text.replace("\u2005", " ").replace("\u200b", "").strip()
    if not text or text.startswith("<"):
        return ""                  # 纯 XML / sysmsg / appmsg 不是聊天文本
    if "<_wc_custom_link_" in text or "<sysmsg" in text or "CDATA[" in text:
        return ""                  # 企业微信提示、群系统消息的残留
    if len(text) > MAX_TEXT_LEN:
        text = text[:MAX_TEXT_LEN]
    return text


def ts2str(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return str(ts)


def open_db(path):
    con = sqlite3.connect(path)
    try:
        con.execute("PRAGMA query_only=ON")
    except Exception:
        pass
    return con


def table_list(con):
    try:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    except Exception as e:
        log("    !! 读表名失败：%s" % e)
        return []


def load_name2id(con, tabs):
    """返回 {rowid: username}；表不存在或读取失败都只警告不抛。"""
    t = None
    for name in tabs:
        if name.lower() == "name2id":
            t = name
            break
    if not t:
        return {}
    try:
        return {r[0]: (r[1] or "") for r in con.execute('SELECT rowid, user_name FROM "%s"' % t)}
    except Exception as e:
        log("    !! 读 Name2Id 失败：%s" % e)
        return {}


def my_wxid_of(dir_path):
    """本人 wxid：优先读目录里的 _wx4_account.txt，退化为目录名去掉 _xxxx 后缀。"""
    p = os.path.join(dir_path, "_wx4_account.txt")
    if os.path.exists(p):
        try:
            v = open(p, encoding="utf-8", errors="replace").read().strip()
            if v:
                return re.sub(r"_[0-9a-fA-F]{4}$", "", v)
        except Exception:
            pass
    base = os.path.basename(dir_path.rstrip("\\/"))
    return re.sub(r"_[0-9a-fA-F]{4}$", "", base)


def resolve_dec_dir():
    if os.path.isdir(DEC_DIR):
        return DEC_DIR
    parent = os.path.dirname(DEC_DIR)
    if os.path.isdir(parent):
        for d in sorted(glob.glob(os.path.join(parent, "*"))):
            if os.path.isdir(d) and glob.glob(os.path.join(d, "message_*_decrypted.db")):
                log("配置目录不存在，改用同目录下找到的账号目录：%s" % d)
                return d
    return None


def norm_type(local_type):
    """local_type 低 32 位 = 消息类型（1 = 文本）"""
    try:
        return int(local_type) % 4294967296
    except Exception:
        return -1


def main():
    log("=" * 72)
    log("微信 4.x 聊天记录 → 微调数据导出")
    log("输出文件（写死）：%s" % OUT_PATH)
    log("解密库目录（写死）：%s" % DEC_DIR)
    if zstandard is None:
        log("!! 未安装 zstandard，zstd 压缩的正文会被跳过。安装命令：pip install zstandard")

    dec_dir = resolve_dec_dir()
    if not dec_dir:
        log("!! 找不到解密库目录，无法继续。请先跑 wxdump ui 让它把库解密到：%s" % DEC_DIR)
        return 1

    my_wxid = my_wxid_of(dec_dir)
    log("解密库目录：%s" % dec_dir)
    log("本人 wxid（用于判定收发）：%s" % my_wxid)

    all_dbs = sorted(glob.glob(os.path.join(dec_dir, "*_decrypted.db")))
    msg_dbs = [p for p in all_dbs if MSG_DB_RE.match(os.path.basename(p))]
    skipped_by_name = [os.path.basename(p) for p in all_dbs if not MSG_DB_RE.match(os.path.basename(p))]
    log("目录下 *_decrypted.db 共 %d 个；符合 message_<数字>_decrypted.db 的 %d 个"
        % (len(all_dbs), len(msg_dbs)))
    log("按文件名排除的辅助库（%d）：%s" % (len(skipped_by_name), ", ".join(skipped_by_name)))
    if not msg_dbs:
        log("!! 没有任何 message_<数字>_decrypted.db，导出 0 条。")
        return 1

    # ---------- 第一遍：读每个库的 Name2Id + Msg_ 分表，建立全局索引 ----------
    log("-" * 72)
    log("第一遍：扫描库结构")
    db_info = {}          # path -> {"n2i":{}, "my_rowid":int|None, "tables":{表名:库路径}, "msg_tabs":[]}
    global_ids = set()
    for p in msg_dbs:
        name = os.path.basename(p)
        try:
            con = open_db(p)
            tabs = table_list(con)
            n2i = load_name2id(con, tabs)
            msg_tabs = sorted(t for t in tabs if t.upper().startswith(MSG_TABLE_PREFIX.upper()))
            if not msg_tabs:
                log("  跳过 %-28s 没有 Msg_ 表（表数 %d）%s" % (name, len(tabs), "" if n2i else "，Name2Id 也是空的"))
                con.close()
                continue
            my_rowid = None
            for rid, uname in n2i.items():
                if uname == my_wxid:
                    my_rowid = rid
                    break
            mine_rows = 0
            if my_rowid is not None:
                for t in msg_tabs:
                    try:
                        mine_rows += con.execute(
                            'SELECT COUNT(*) FROM "%s" WHERE real_sender_id=?' % t, (my_rowid,)).fetchone()[0]
                    except Exception:
                        pass
            db_info[p] = {"n2i": n2i, "my_rowid": my_rowid, "msg_tabs": msg_tabs, "mine_rows": mine_rows}
            global_ids.update(u for u in n2i.values() if u)
            log("  扫描 %-28s Msg_ 分表 %-3d Name2Id %-4d 本人rowid=%s 我发的=%d 条"
                % (name, len(msg_tabs), len(n2i), my_rowid, mine_rows))
            con.close()
        except Exception as e:
            log("  跳过 %-28s 打开/读取异常：%s" % (name, e))
            continue

    if not db_info:
        log("!! 所有消息库都没有可用的 Msg_ 表，导出 0 条。")
        return 1
    if not any(v["my_rowid"] is not None for v in db_info.values()):
        log("!! 各库 Name2Id 里都找不到本人 wxid（%s），IsSender 将全判为 0（可能没有可成对的样本）" % my_wxid)

    # 表名 → 会话 wxid（用所有库 Name2Id 的并集反查，跨库也能认出）
    hash2wxid = {MSG_TABLE_PREFIX + md5hex(u): u for u in global_ids}
    log("全局账号 id %d 个，可反查的消息表名 %d 个" % (len(global_ids), len(hash2wxid)))

    # talker -> [(path, table)]，一个会话可能被拆在多库多表
    talker_shards = {}
    unresolved = []
    for p, info in db_info.items():
        for t in info["msg_tabs"]:
            w = hash2wxid.get(t)
            if not w:
                unresolved.append((os.path.basename(p), t))
                continue
            talker_shards.setdefault(w, []).append((p, t))
    if unresolved:
        log("以下 %d 张 Msg_ 表反查不到会话 wxid（跳过，不影响其它数据）：" % len(unresolved))
        for f, t in unresolved[:20]:
            log("    %s / %s" % (f, t))
        if len(unresolved) > 20:
            log("    ...（其余 %d 张略）" % (len(unresolved) - 20))
    if not talker_shards:
        log("!! 没有反查到任何会话，导出 0 条。")
        return 1
    log("可用会话 %d 个" % len(talker_shards))

    # ---------- 第二遍：按会话流式归并 → 生成对话对 → 分批落盘 ----------
    log("-" * 72)
    log("第二遍：提取对话对（每 %d 条落盘一次）" % BATCH_SIZE)
    out = open(OUT_PATH, "w", encoding="utf-8", newline="\n")
    buf = []
    total = 0
    per_db = {}
    stat = {"rows": 0, "text": 0, "nontype": 0, "no_zstd": 0, "too_far": 0, "chatroom_skip": 0,
            "mine": 0, "mine_text": 0}

    def flush(force=False):
        nonlocal buf, total
        if buf and (force or len(buf) >= BATCH_SIZE):
            for item in buf:
                out.write(json.dumps(item, ensure_ascii=False) + "\n")
            out.flush()
            total += len(buf)
            log("    >>> 已写入 %d 条（累计 %d）" % (len(buf), total))
            buf = []

    def rows_of(path, table):
        """流式返回该表按时间排序的 (create_time, is_sender, text)，正文已在库内解好。"""
        info = db_info[path]
        my_rowid = info["my_rowid"]
        is_sender_sql = ("CASE WHEN real_sender_id=%d THEN 1 ELSE 0 END" % int(my_rowid)) \
            if my_rowid is not None else "0"
        sql = ("SELECT create_time, %s AS is_sender, message_content, WCDB_CT_message_content, local_type "
               "FROM \"%s\" ORDER BY create_time ASC, sort_seq ASC" % (is_sender_sql, table))
        try:
            cur = open_db(path).cursor()
            cur.execute(sql)
            while True:
                batch = cur.fetchmany(500)
                if not batch:
                    break
                for ct, is_sender, content, ctf, lt in batch:
                    stat["rows"] += 1
                    if is_sender == 1:
                        stat["mine"] += 1
                    # 只要文本消息：local_type 低 32 位 = 1；图片/语音/链接/系统消息等一律不要
                    if norm_type(lt) != 1:
                        stat["nontype"] += 1
                        continue
                    txt = decode_content(content, ctf)
                    if txt is None:
                        stat["no_zstd"] += 1
                        continue
                    txt = cleanup_text(strip_sender_prefix(txt, global_ids))
                    if len(txt) < MIN_TEXT_LEN:
                        continue
                    stat["text"] += 1
                    if is_sender == 1:
                        stat["mine_text"] += 1
                    yield (int(ct or 0), int(is_sender or 0), txt)
        except Exception as e:
            log("    !! 读表失败 %s / %s：%s" % (os.path.basename(path), table, e))
            return

    for talker in sorted(talker_shards):
        if SKIP_CHATROOM and talker.endswith("@chatroom"):
            stat["chatroom_skip"] += 1
            continue
        shards = talker_shards[talker]
        try:
            heap = []
            for idx, (path, table) in enumerate(shards):
                it = rows_of(path, table)
                try:
                    heap.append([next(it), idx, it])
                except StopIteration:
                    pass
            heapq.heapify(heap)

            pending = None           # 上一条"对方"的文本
            generated = 0
            while heap:
                (ct, is_sender, txt), idx, it = heapq.heappop(heap)
                if is_sender == 1:
                    if pending is not None:
                        gap = ct - pending[0]
                        if 0 <= gap <= MAX_GAP_SECONDS:
                            item = {
                                "messages": [
                                    {"role": "user", "content": pending[1]},
                                    {"role": "assistant", "content": txt},
                                ],
                                "meta": {"talker": talker,
                                         "ask_time": ts2str(pending[0]),
                                         "reply_time": ts2str(ct)},
                            }
                            buf.append(item)
                            generated += 1
                            dbkey = os.path.basename(shards[idx][0])
                            per_db[dbkey] = per_db.get(dbkey, 0) + 1
                            flush()
                        else:
                            stat["too_far"] += 1
                    pending = None
                else:
                    pending = (ct, txt)
                try:
                    heapq.heappush(heap, [next(it), idx, it])
                except StopIteration:
                    pass

            key = os.path.basename(shards[0][0])
            per_db[key] = per_db.get(key, 0) + generated
            log("  正在处理 %-28s（会话 %s，%d 张分表）→ 提取到 %d 条对话对"
                % (key, talker, len(shards), generated))
        except Exception as e:
            log("  !! 会话 %s 处理异常，已跳过：%s" % (talker, e))
            continue

    flush(force=True)
    out.close()

    size = os.path.getsize(OUT_PATH) if os.path.exists(OUT_PATH) else 0
    log("-" * 72)
    log("扫描消息行数：%d；其中有效文本：%d" % (stat["rows"], stat["text"]))
    log("本账号自己发的消息：%d 条（其中有效文本 %d 条）" % (stat["mine"], stat["mine_text"]))
    log("因不是文本消息（图片/语音/链接/系统消息等）跳过：%d 条" % stat["nontype"])
    log("因未装 zstandard 跳过的压缩消息：%d" % stat["no_zstd"])
    log("因间隔超过 %d 秒未成对的：%d" % (MAX_GAP_SECONDS, stat["too_far"]))
    if SKIP_CHATROOM:
        log("因跳过群聊丢弃的会话：%d" % stat["chatroom_skip"])
    log("各库产出：")
    for k in sorted(per_db):
        log("    %-30s %d 条" % (k, per_db[k]))
    log("对话对总数：%d" % total)
    log("输出文件：%s（%d 字节）" % (OUT_PATH, size))
    if total == 0:
        log("提示：没有产出对话对。常见原因——该账号消息太少；只有单向消息（对方发的我没回过）；")
        log("      正文都是非文本（图片/语音/系统消息）；或未安装 zstandard 导致压缩正文被跳过。")
        if stat["mine"] == 0:
            log("      本次实测原因：本账号一条自己发的消息都没有（全部是收到的），")
            log("      因此 (对方→我) 这种对话对在数学上不可能存在——不是脚本问题。")
            log("      换一个自己发过消息的账号，把本文件顶部的 DEC_DIR 指到它的解密库目录即可（其余不用改）。")
    else:
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                log("样例第一行：%s" % f.readline().strip()[:300])
        except Exception:
            pass
    log("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
