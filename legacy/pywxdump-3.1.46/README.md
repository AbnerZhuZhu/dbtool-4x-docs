# PyWxDump 3.1.46 历史版本存档

## 这是什么

本目录是通过 `pip download pywxdump==3.1.46` 下载得到的 **PyWxDump 3.1.46 原始安装包**（主程序 wheel + 安装时 pip 一并下载的全部依赖包），仅用于**本地历史版本对比与学习参考**，不用于实际运行。

## 背景

原版 PyWxDump 作者 `xaoyaoo` 因合规问题已删除其 GitHub 仓库，3.1.46 版本**不再维护、不再更新**。本存档仅作为历史对照留存，不对原项目主张任何权利。

## 声明

- 仅供**个人学习研究**使用
- **不得公开传播**（本仓库必须保持 Private）
- **不得用于商业用途**
- **不得用于任何违法用途**

> 原项目著作权与许可证归原作者所有：主包内附 `LICENSE`（位于 `pywxdump-3.1.46-py3-none-any.whl` 的 `pywxdump-3.1.46.dist-info/LICENSE`，License 声明为 **MIT**），请一并保留。

## 环境要求

| 项 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11（本存档为 win_amd64 依赖包） |
| Python | 3.8 – 3.12（原包声明 `Requires-Python: >=3.8, <4`；本次验证环境 Python 3.12.8） |
| pip | 任意较新版本（本次验证环境 pip 24.3.1） |
| 权限 | 普通 PowerShell 即可；本目录的离线安装**不需要管理员权限**、**不需要联网** |

## ⚠️ 安装前必读：命令名冲突

3.1.46 与 4.0 改造版注册的是**同一个命令名**：

```
wxdump = pywxdump.cli:console_run
```

也就是说，**如果把 3.1.46 装进现有环境，会直接覆盖掉 4.0 改造版的 `wxdump` 命令**（`wxdump info` 会退回 3.x 逻辑）。
所以本存档**推荐用独立虚拟环境安装**（方案 A），与现有环境完全隔离、互不影响。

## 安装（PowerShell）

### 方案 A：独立虚拟环境 + 本地离线安装（**推荐**）

```powershell
cd E:\Users\Administrator\Desktop\pywxdump_old
python -m venv .venv-3146
.\.venv-3146\Scripts\python.exe -m pip install --no-index --find-links . pywxdump==3.1.46
.\.venv-3146\Scripts\wxdump.exe -V
```

- `--no-index` = 完全不联网；`--find-links .` = 全部依赖从**本目录已下载的 wheel** 安装。
- 该命令已在本机实测可成功解析（`Would install pywxdump-3.1.46`），全流程无需联网。
- 装完后所有调用都走虚拟环境里的可执行文件，例如
  `.\.venv-3146\Scripts\wxdump.exe -h`。

### 方案 B：全局离线安装（**会覆盖现有 `wxdump` 命令，慎用**）

```powershell
cd E:\Users\Administrator\Desktop\pywxdump_old
python -m pip install --no-index --find-links . pywxdump==3.1.46
```

### 方案 C：联网一条命令直接下载并安装

```powershell
python -m pip install pywxdump==3.1.46
```

### 方案 C-2：先下载到本地、再离线安装（两步，等价于制作本存档的过程）

```powershell
python -m pip download pywxdump==3.1.46 -d E:\Users\Administrator\Desktop\pywxdump_old
python -m pip install --no-index --find-links E:\Users\Administrator\Desktop\pywxdump_old pywxdump==3.1.46
```

> 若 `python` 不在 PATH，改用完整路径 `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`，或使用 `py -3.12`。

## 验证安装

```powershell
wxdump -V      # 应输出：PyWxDump v3.1.46
wxdump -h      # 查看全部子命令
```

3.1.46 共 10 个子命令：

| 子命令 | 用途 |
| --- | --- |
| `info` | 读取微信信息（密钥、昵称等） |
| `bias` | 获取/输出内存偏移量 |
| `wx_path` | 显示微信数据目录路径 |
| `decrypt` | 解密数据库文件 |
| `dbshow` | 展示数据库内容 |
| `merge` | 合并多个数据库 |
| `export` | 导出聊天记录 |
| `all` | 一键执行全流程 |
| `ui` | 启动网页界面 |
| `api` | 启动本地 API 服务 |

## 卸载

```powershell
python -m pip uninstall -y pywxdump            # 全局安装（方案 B / C）的卸载
Remove-Item -Recurse -Force .\.venv-3146        # 虚拟环境（方案 A）直接删目录即可
```

## 重要限制（读数据前必看）

- 3.1.46 是 **微信 3.x 时代**的实现：它读的是 `MSG` / `Contact` / `Session` 这套表结构。
- **它无法读取微信 4.x 的数据**（4.x 的表名是 `Msg_<hash>` / `contact` / `session`，密钥机制也不同）。用 3.1.46 去读 4.x 库只会报错或无数据。
- 因此请务必注意：**不要**为了"省事"把 3.1.46 装到微信 4.x 的环境里覆盖 4.0 改造版；它在这里的定位就是**历史版本对照物**。

## 目录内容

- 主程序包：`pywxdump-3.1.46-py3-none-any.whl`（3.1.46）
- 依赖包：其余 `*.whl` / `*.tar.gz`

| 文件 | 字节数 |
| --- | ---: |
| `PyAudio-0.2.14-cp312-cp312-win_amd64.whl` | 164,126 |
| `annotated_doc-0.0.5-py3-none-any.whl` | 5,302 |
| `annotated_types-0.8.0-py3-none-any.whl` | 13,427 |
| `anyio-4.15.1-py3-none-any.whl` | 132,079 |
| `blackboxprotobuf-1.0.1.tar.gz` | 14,435 |
| `certifi-2026.7.22-py3-none-any.whl` | 136,983 |
| `cffi-2.1.1-cp312-cp312-win_amd64.whl` | 185,919 |
| `charset_normalizer-3.5.2-cp312-cp312-win_amd64.whl` | 207,486 |
| `click-8.5.0-py3-none-any.whl` | 125,251 |
| `dbutils-3.2.0-py3-none-any.whl` | 36,210 |
| `fastapi-0.142.2-py3-none-any.whl` | 144,409 |
| `h11-0.16.0-py3-none-any.whl` | 37,515 |
| `idna-3.20-py3-none-any.whl` | 69,583 |
| `lxml-6.1.3-cp312-cp312-win_amd64.whl` | 4,005,999 |
| `lz4-4.4.5-cp312-cp312-win_amd64.whl` | 99,497 |
| `opentelemetry_api-1.45.0-py3-none-any.whl` | 60,020 |
| `protobuf-3.10.0-py2.py3-none-any.whl` | 434,939 |
| `psutil-7.2.2-cp37-abi3-win_amd64.whl` | 137,737 |
| `pyahocorasick-2.3.1-cp312-cp312-win_amd64.whl` | 35,258 |
| `pycparser-3.0-py3-none-any.whl` | 48,172 |
| `pycryptodomex-3.23.0-cp37-abi3-win_amd64.whl` | 1,803,124 |
| `pydantic-2.13.5-py3-none-any.whl` | 472,589 |
| `pydantic_core-2.46.5-cp312-cp312-win_amd64.whl` | 2,043,140 |
| `pymem-1.14.0-py3-none-any.whl` | 29,833 |
| `python_dotenv-1.2.4-py3-none-any.whl` | 23,266 |
| `pywin32-312-cp312-cp312-win_amd64.whl` | 6,914,841 |
| `pywxdump-3.1.46-py3-none-any.whl` | 3,275,415 |
| `requests-2.34.2-py3-none-any.whl` | 73,075 |
| `setuptools-84.0.0-py3-none-any.whl` | 818,216 |
| `silk_python-0.2.8-cp312-cp312-win_amd64.whl` | 336,992 |
| `six-1.17.0-py2.py3-none-any.whl` | 11,050 |
| `starlette-1.7.0-py3-none-any.whl` | 78,980 |
| `typing_extensions-4.16.0-py3-none-any.whl` | 45,571 |
| `typing_inspection-0.4.4-py3-none-any.whl` | 14,750 |
| `urllib3-2.8.0-py3-none-any.whl` | 135,717 |
| `uvicorn-0.54.0-py3-none-any.whl` | 87,427 |

合计：**36 个文件 / 22,258,333 B（约 21.23 MB）**

> 说明：主包 wheel 内除 Python 代码外，还包含随包分发的 `libcrypto-1_1-x64.dll` 与 `realTime.exe`（原包自带），以及 3.x 时代的 `WX_OFFS.json` 偏移表。本存档不做任何改动、不执行其中任何程序。

## 更新日期

2026-10-04
