# -*- coding: utf-8 -*-
"""
view_all_chat.py

用途：仅基于「已经解密好」的微信 4.1.x 数据库，完整汇总导出全部聊天记录。
- 不提取密钥、不依赖 PyWxDump / wechatauto / 图形界面
- 自动遍历 D:\\decrypted_wx_db\\ 下所有 message_*_decrypted.db
- 自动排除 fts / media / resource 等辅助库
- 优先读取传统 MSG 表；若不存在（微信 4.x 实际情况），自动检测真实表结构并适配
- 兼容 zstd 压缩的消息内容
- 按时间从新到旧排序，写入 UTF-8 文本
"""

import os
import sys
import glob
import time
import sqlite3
import hashlib

# ================= 配置区 =================
DB_DIR = r"D:\decrypted_wx_db"
OUTPUT_FILE = r"E:\Users\Administrator\Desktop\我的全部聊天记录.txt"

# 本机自己的 wxid（用于判断【我】）
MY_WXID = "wxid_8215502153912"
# ==========================================

try:
    import zstandard as zstd
    _ZSTD = zstd.ZstdDecompressor()
except Exception:
    _ZSTD = None

UNDECODABLE = "[图片/语音/文件/视频]"


def format_time(ts):
    """时间戳（秒或毫秒）转可读字符串"""
    if ts is None:
        return "未知时间"
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return str(ts)
    if ts > 10 ** 12:          # 毫秒 -> 秒
        ts = ts / 1000
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        return str(ts)


def decode_content(mc):
    """解码消息内容：zstd 压缩 / utf-8 明文 / 二进制回退"""
    if mc is None:
        return ""
    if isinstance(mc, (bytes, bytearray)):
        raw = bytes(mc)
        # zstd 魔数 0x28B52FFD
        if raw[:4] == b"\x28\xb5\x2f\xfd" and _ZSTD is not None:
            try:
                return _ZSTD.decompress(raw).decode("utf-8", "replace")
            except Exception:
                return UNDECODABLE
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return UNDECODABLE
    return str(mc)


def should_include_db(path):
    """只保留 message_*_decrypted.db，排除 fts / media / resource 辅助库"""
    name = os.path.basename(path).lower()
    if "fts" in name or "media" in name or "resource" in name:
        return False
    return name.startswith("message_") and name.endswith("_decrypted.db")


def list_tables(conn):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]


def print_structure(conn, db_name):
    """打印数据库表结构（满足『自动检测并打印表结构』的要求）"""
    tables = list_tables(conn)
    msg_like = [t for t in tables if t.lower() == "msg"]
    msg_suffix = [t for t in tables if t.startswith("Msg_")]
    print(f"  [结构] {db_name}: 共 {len(tables)} 张表"
          f"（MSG表: {'有' if msg_like else '无'}，Msg_<hash>表: {len(msg_suffix)} 张）")
    if msg_like:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{msg_like[0]}")')]
        print(f"    - MSG 字段: {cols}")
    elif msg_suffix:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{msg_suffix[0]}")')]
        print(f"    - 示例表 {msg_suffix[0]} 字段: {cols}")


def build_name_map(conn):
    """构建 {md5(会话wxid): 会话wxid} 映射，用于从 Msg_<hash> 表名反查会话 ID"""
    name_map = {}
    try:
        rows = conn.execute("SELECT user_name FROM Name2Id").fetchall()
    except Exception:
        return name_map
    for (uname,) in rows:
        if uname:
            name_map[hashlib.md5(uname.encode("utf-8")).hexdigest()] = uname
    return name_map


def extract_from_msg_table(conn, messages):
    """兼容路径：若存在传统 MSG 表，按 CreateTime/TalkerId/Message/IsSender 读取"""
    tables = {t.lower(): t for t in list_tables(conn)}
    if "msg" not in tables:
        return False
    tname = tables["msg"]
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{tname}")')]
    cl = {c.lower(): c for c in cols}
    c_time = cl.get("createtime") or cl.get("create_time")
    c_talker = cl.get("talkerid") or cl.get("talker_id")
    c_msg = cl.get("message") or cl.get("message_content")
    c_send = cl.get("issender") or cl.get("is_sender")
    if not (c_time and c_msg):
        return False
    sel = f'SELECT "{c_time}", ' + (f'"{c_talker}"' if c_talker else '""') + \
          f', "{c_msg}", ' + (f'"{c_send}"' if c_send else '0') + f' FROM "{tname}"'
    for create_time, talker_id, message, is_sender in conn.execute(sel):
        talker_id = talker_id or ""
        messages.append((create_time or 0, "【我】" if is_sender == 1 else "【对方】",
                         talker_id, talker_id, decode_content(message)))
    return True


def extract_from_msg_hash_tables(conn, name_map, messages, contact_names):
    """微信 4.x 路径：读取所有 Msg_<hash> 表"""
    tables = [t for t in list_tables(conn) if t.startswith("Msg_")]
    for t in tables:
        session_id = name_map.get(t[len("Msg_"):], t[len("Msg_"):])
        display = contact_names.get(session_id, session_id)
        try:
            cursor = conn.execute(
                f'SELECT create_time, real_sender_id, message_content FROM "{t}"')
            for create_time, real_sender_id, message_content in cursor:
                who = "【我】" if real_sender_id == 1 else "【对方】"
                messages.append((create_time or 0, who, display, session_id,
                                 decode_content(message_content)))
        except Exception as e:
            print(f"    读取表 {t} 失败: {e}")
            continue


def load_contact_names():
    """从 contact 库读取 {wxid: 备注或昵称}，用于展示更友好的会话名"""
    result = {}
    path = os.path.join(DB_DIR, "contact_decrypted.db")
    if not os.path.exists(path):
        return result
    try:
        conn = sqlite3.connect(path)
        for username, remark, nick in conn.execute(
                "SELECT username, remark, nick_name FROM contact"):
            display = (remark or nick or "").strip()
            if display:
                result[username] = display
        conn.close()
    except Exception:
        pass
    return result


def main():
    if not os.path.isdir(DB_DIR):
        print(f"[错误] 目录不存在: {DB_DIR}")
        return 1

    db_files = sorted(f for f in glob.glob(os.path.join(DB_DIR, "message_*_decrypted.db"))
                      if should_include_db(f))
    if not db_files:
        print(f"[错误] 未找到 message_*_decrypted.db: {DB_DIR}")
        return 1

    print(f"共找到 {len(db_files)} 个消息数据库：")
    for f in db_files:
        print(f"  - {os.path.basename(f)}")

    contact_names = load_contact_names()
    messages = []

    for db_path in db_files:
        db_name = os.path.basename(db_path)
        print(f"\n读取: {db_name}")
        conn = sqlite3.connect(db_path)
        try:
            print_structure(conn, db_name)
            before = len(messages)

            used_msg = extract_from_msg_table(conn, messages)
            if used_msg:
                print("  -> 使用传统 MSG 表导出")
            else:
                print("  -> 未发现 MSG 表，自动适配微信 4.x 的 Msg_<hash> 表结构")
                name_map = build_name_map(conn)
                extract_from_msg_hash_tables(conn, name_map, messages, contact_names)
            print(f"  -> 本库新增 {len(messages) - before} 条")
        except Exception as e:
            print(f"  [跳过] 读取 {db_name} 出错: {e}")
        finally:
            conn.close()

    if not messages:
        print("\n提取结果为空。")
        return 1

    # 按时间从新到旧排序
    messages.sort(key=lambda x: x[0], reverse=True)
    total = len(messages)
    print(f"\n共 {total} 条消息，正在写入 {OUTPUT_FILE} ...")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for create_time, who, display, session_id, text in messages:
            f.write(f"[{format_time(create_time)}] {who}(ID: {session_id})\n")
            f.write(f"{text}\n")
            f.write("-----------\n")

    size = os.path.getsize(OUTPUT_FILE)
    print("=" * 56)
    print(f"导出完成！")
    print(f"总消息条数: {total}")
    print(f"文件保存路径: {OUTPUT_FILE}")
    print(f"文件大小: {size:,} 字节（约 {size / 1024 / 1024:.2f} MB）")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    sys.exit(main())
