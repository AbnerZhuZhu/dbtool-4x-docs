# PyWxDump 4.0.0 聊天热力图：跨度自适应 · 双向滚动 · 缩放 修复说明

修复日期：2026-10-04　目标：**任何账号、任何历史跨度都不再被截断，且都能滚动看全**

---

## 一、结论速览

| 问题 | 结论 |
|---|---|
| 改 Vue 源码还是编译后 JS？ | **只能改编译后的 JS**。`pywxdump\ui\web\` 下没有 Vue 源码工程（无 package.json / vite.config.js / src），只有构建产物。改完 **Ctrl+F5 强刷即可生效，无需重新打包**。 |
| 年份范围 | 日历范围 = **后端返回的数据跨度 ∪ 按天聚合数据键**（并集），不再依赖"第一个键/最后一个键"，任一侧有的年份都不会漏；中间无数据的年份也照渲染。 |
| 滚动 | 包裹热力图的容器本来就是 `overflow:auto`（横纵都自动），本轮**保留**并实测双轴生效。 |
| 缩放 | **`dataZoom` 对 calendar 坐标系无效**（ECharts 硬限制，实测确认）。改用真正生效的三种方式：① 新增「缩放」下拉（紧凑/标准/放大）② **Ctrl+滚轮**缩放 ③ 年份下拉。 |
| 防卡顿 | 后端本来就按天 `GROUP BY` 聚合（59 万条 → 2,389 个日期键），浏览器从不接触原始行；实测 17 年跨度渲染 **162 ms**、含两次接口 **665 ms**。另加「跨度 ≥10 年自动紧凑」把画布面积降约 2 倍。 |

---

## 二、改动清单（3 个文件）

### 1. 后端新增接口：`pywxdump\db\dbMSG.py` → `MsgHandler.get_date_range()`

在 `get_date_count()` 之后新增（3.x/4.x 共用 `msg_sources_for` 数据源，自动分流）：

```python
@db_error
def get_date_range(self, wxid=''):
    """
    【4.0 热力图自适应】返回消息的时间跨度与逐年条数。
    返回：{min_date, max_date, min_year, max_year, span_years, total, per_year}
    """
    if not self.tables_exist("MSG"):
        return {}
    sql_wxid = "AND StrTalker = ? " if wxid else ""
    params = (wxid,) if wxid else ()
    span_sql = ("SELECT MIN(CreateTime), MAX(CreateTime), COUNT(*) FROM {src_} "
                "WHERE StrTalker NOT LIKE '%chatroom%' " + sql_wxid)
    year_sql = ("SELECT strftime('%Y', CreateTime, 'unixepoch', 'localtime') AS y, COUNT(*) "
                "FROM {src_} WHERE StrTalker NOT LIKE '%chatroom%' " + sql_wxid + " GROUP BY y")
    min_ts = max_ts = None
    total = 0
    per_year = {}
    for path, src in self.msg_sources_for([wxid] if wxid else []):
        for row in (self._exec_on(path, span_sql.format(src_=src), params) or []):
            if row and row[0] is not None:
                min_ts = row[0] if min_ts is None else min(min_ts, row[0])
                max_ts = row[1] if max_ts is None else max(max_ts, row[1])
                total += row[2] or 0
        for row in (self._exec_on(path, year_sql.format(src_=src), params) or []):
            if row and row[0]:
                per_year[row[0]] = per_year.get(row[0], 0) + (row[1] or 0)
    if min_ts is None:
        return {}
    min_date = timestamp2str(min_ts)[:10]
    max_date = timestamp2str(max_ts)[:10]
    return {"min_ts": int(min_ts), "max_ts": int(max_ts),
            "min_date": min_date, "max_date": max_date,
            "min_year": int(min_date[:4]), "max_year": int(max_date[:4]),
            "span_years": int(max_date[:4]) - int(min_date[:4]) + 1,
            "total": total, "per_year": dict(sorted(per_year.items()))}
```

实测返回（柳岸，`POST /api/rs/date_range`，0.65 s）：

```json
{"min_ts": 1536754213, "max_ts": 1791047138,
 "min_date": "2018-09-12", "max_date": "2026-10-04",
 "min_year": 2018, "max_year": 2026, "span_years": 9, "total": 270647,
 "per_year": {"2018":2604,"2019":7272,"2020":6233,"2021":10933,"2022":29980,
              "2023":19901,"2024":38440,"2025":103674,"2026":51610}}
```

### 2. 后端接口：`pywxdump\api\remote_server.py`

**(a) `/api/rs/date_count` 的 `extra` 里附带跨度**（不改 `body`，老前端零影响）：

```python
date_count = db.get_date_count(wxid=wxid, start_time=start_time, end_time=end_time, time_format=time_format)
_days = sorted(date_count.keys()) if isinstance(date_count, dict) else []
_extra = {}
if _days:
    _extra = {"min_date": _days[0], "max_date": _days[-1], "days": len(_days),
              "min_year": int(_days[0][:4]), "max_year": int(_days[-1][:4])}
return ReJson(0, date_count, extra=_extra)
```

**(b) 新增 `/api/rs/date_range`**（前端专用于"渲染前拿跨度"）：

```python
class DateRangeRequest(BaseModel):
    wxid: str = ""

@rs_api.api_route('/date_range', methods=["GET", 'POST'])
def get_date_range(request: DateRangeRequest):
    wxid = request.wxid
    my_wxid = gc.get_conf(gc.at, "last")
    if not my_wxid: return ReJson(1001, body="my_wxid is required")
    db = DBHandler(gc.get_conf(my_wxid, "db_config"), my_wxid=my_wxid)
    rdata = db.get_date_range(wxid=wxid)
    if not rdata: return ReJson(4004, body={})
    return ReJson(0, rdata)
```

### 3. 前端：`pywxdump\ui\web\assets\StatisticsView-_MI6G2N3.js`

**改动前（固定列宽 + 只认首尾数据键）：**

```js
v=async(p=!0)=>{p&&await f();let g=Object.keys(i.value)[0],y=Object.keys(i.value)[...length-1];
  if(!g){...}let m=parseInt(g.split("-")[0]),_=parseInt(y.split("-")[0]);
  Y.value!=="all"&&(m=parseInt(Y.value),_=m);u.value.calendar=[],u.value.series=[];
  for(let S=m;S<_+1;S++)u.value.calendar.push({top:100,left:50+200*(S-m),orient:"vertical",range:S.toString(),...})
```
（列宽写死 200、高度写死 `1360px`、范围只看数据键）

**改动后（范围取并集 + 自适应缩放）：**

```js
// ① 状态
rng=Wt({}),zm=Wt(1),zAut=Wt(!1),csz=Wt({width:2070,height:1360})

// ② 拉取后端跨度
R=async()=>{try{const Q=await fetch("/api/rs/date_range",{method:"POST",
  headers:{"Content-Type":"application/json"},body:JSON.stringify({wxid:t.value})}).then(Z=>Z.json());
  rng.value=(Q&&Q.body&&Q.body.min_date)?Q.body:{}}catch(Z){rng.value={}}}

// ③ 渲染
v=async(p=!0)=>{p&&(await f(),await R());
  let g=Object.keys(i.value)[0],y=Object.keys(i.value)[Object.keys(i.value).length-1],m=0,_=0;
  if(g)m=parseInt(g.split("-")[0]),_=parseInt(y.split("-")[0]);
  else if(rng.value&&rng.value.min_year)m=rng.value.min_year,_=rng.value.max_year;
  else{u.value.calendar=[],u.value.series=[],l.value=!l.value;return}
  rng.value&&rng.value.min_year&&(m=Math.min(m,rng.value.min_year),_=Math.max(_,rng.value.max_year));  // ★ 并集
  Y.value!=="all"&&(m=parseInt(Y.value),_=m);
  let N=_-m+1; zAut.value||(zm.value=N>=20?0.55:N>=10?0.7:1);                                          // ★ 跨度大自动紧凑
  const cw=Math.round(200*zm.value),cs=Math.max(8,Math.round(20*zm.value));
  csz.value={width:50+cw*N+220,height:100+Math.ceil(53*cs)+220};                                        // ★ 画布随缩放
  u.value.title=Object.assign({},u.value.title,{subtext:
    ((rng.value&&rng.value.min_date)?(rng.value.min_date+" ~ "+rng.value.max_date):(g+" ~ "+y))+"　共 "+N+" 年"});
  u.value.calendar=[],u.value.series=[];
  for(let S=m;S<_+1;S++)u.value.calendar.push({top:100,left:50+cw*(S-m),orient:"vertical",
      range:S.toString(),cellSize:[cs,cs],dayLabel:{...},monthLabel:{...},yearLabel:{color:"#000"}}),
    u.value.series.push({type:"heatmap",coordinateSystem:"calendar",calendarIndex:S-m,data:[]});
  ...}

// ④ 容器：横纵双向滚动（保留原有）+ Ctrl+滚轮缩放
Bt(w,{style:{height:"calc(100% - 100px)",width:"100%",overflow:"auto"},
  "onWheel":Q=>{if(Q&&(Q.ctrlKey||Q.metaKey)){Q.preventDefault&&Q.preventDefault();
    const Z=Math.min(2.4,Math.max(.4,Math.round((zm.value+(Q.deltaY<0?.1:-.1))*100)/100));
    zm.value!==Z&&(zm.value=Z,zAut.value=!0,v(!1))}}},
  {default:Jt(()=>[Bt(km,{option:u.value,update:l.value,
    style:{width:csz.value.width+"px",height:csz.value.height+"px"}},null,8,["option","update","style"])])})

// ⑤ 工具条：年份下拉旁新增「缩放」下拉（紧凑 0.7 / 标准 1 / 放大 1.4）
mr("strong",null,"缩放：",-1),Bt(m,{modelValue:zm.value,
  "onUpdate:modelValue":Q=>{const zz=Number(Q)||1;zz!==zm.value&&(zm.value=zz,zAut.value=!0,v(!1))},
  size:"small",style:{width:"96px"}},{default:Jt(()=>[
  (ie(!0),Ir(so,null,Es([{l:"紧凑",v:.7},{l:"标准",v:1},{l:"放大",v:1.4}],
   Q=>(ie(),Qr(y,{key:Q.v,label:Q.l,value:Q.v},null,8,["label","value"]))),128))]),_:1},8,["modelValue"])
```

**为什么不用 dataZoom**：ECharts 的 `dataZoom` 只作用于直角坐标/polar/single 轴，**calendar 坐标系不支持**（已实测：加上去完全不响应）。所以"缩放"用列宽 + 单元格大小（`cellSize`）实现，配合容器滚动，效果等价且真实可用。

---

## 三、实测验证记录（本机，柳岸 59 万条）

| 场景 | 边界条件 | 实测结果 |
|---|---|---|
| **17 年跨度**（注入 2010–2026 数据） | N=17 | 画布 **2,650×1,062**（自动紧凑 0.7）；日历 **17 块**；年份下拉 **18 项（2010–2026 全在）**；容器 `scrollWidth 2,690 > 25`、`scrollHeight 1,102 > 781` → **横纵都能滚**；耗时 **665 ms**（含 2 次接口，纯重渲染 162 ms） |
| 缩放=放大 | N=17 | 画布 **5,030×1,804**，容器 `scrollWidth 5,070` ✓ |
| 年份=2015 | 单年 | 画布 **550×1,804**，日历 1 块 ✓ |
| **柳岸真实数据** | N=9 | 画布 **2,070×1,380**；年份下拉 **10 项（全部 + 2018–2026）**；容器 `sw 2,110 > 25`、`sh 1,420 > 781` ✓；**重启后端后复验一致，控制台零报错** ✓ |
| 零散数据（只有 2013、2019） | 后端报 2013–2019 | 渲染 **14 块日历（2013…2026 并集，中间空档年照渲染）**，无截断 ✓ |
| 后端 `/api/rs/date_range` | 柳岸 | `2018-09-12 ~ 2026-10-04`，跨度 9 年，总 270,647 条，逐年 `2018:2604 / 2019:7272 / 2020:6233 / 2021:10933 / 2022:29980 / 2023:19901 / 2024:38440 / 2025:103674 / 2026:51610`，耗时 **0.65 s** |
| 后端 `/api/rs/date_count` + extra | 柳岸 | 2,389 个日期键，耗时 **0.47 s**，`extra` 正确 ✓ |
| **3.x 老库回归** | merge_all.db（594,764 行） | `is_wx4=False`、`tables_exist("MSG")=True`；`get_date_range` 返回 `2018-09-12 ~ 2026-10-03`，**逐年与 4.x 库完全吻合**（仅 2026 差 6 条＝新库更新）；老逻辑零改动 ✓ |
| 语法校验 | — | `python -m py_compile` 退出码 0；`node --check`（ESM）退出码 0 ✓ |

---

## 三点五、五条技术要求逐条对照（含取证）

| # | 要求 | 状态 | 取证方式 / 证据 |
|---|---|---|---|
| 1 | 绝对禁止硬编码年份/range | ✅ | 静态取证 `%TEMP%\p104_bundle_probe.py` / `p106_opt_probe.py`：全 bundle 内**形如 `'20xx'` 的引号年份字面量 0 个**；`minDate`/`maxDate` **0 次**；日历 range 只有一处，写法是 **`range:S.toString()`（变量）**；option 构建段内 `2018/2019/2030` **各 0 次** |
| 2 | 后端返回全部数据 + `MIN/MAX(CreateTime)` | ✅ | 前端调 `/date_count` 时传 `start_time=0,end_time=0`（无时间过滤，全量）；新增 `/api/rs/date_range` 直接返回 **`min_ts:1536754213` / `max_ts:1791047138`**（原始时间戳）与 `min_date/max_date`（`YYYY-MM-DD`）、`span_years`、`per_year` |
| 3 | 前端动态 range 渲染 | ✅ | 每块日历的 `range` = 该年年份（由"后端跨度 ∪ 数据键"的并集算出，`Math.min/min_year`、`Math.max/max_year`），副标题显示 `min_date ~ max_date　共 N 年`。**没有**用 `range:['2018-01-01','2026-12-31']` 这种单块多年写法——单块 vertical 日历铺 9~17 年会变成几千像素高的单列、完全不可读；改成"一年一块、横向排列"，每块的 `range` 仍是数据算出来的 |
| 4 | 容器 `overflow-x/y:auto` + dataZoom + 滚轮缩放 + 底部滑块 | ⚠️→✅ | 容器 `overflow:auto` 实测 `overflowX/overflowY 均为 auto`，`scrollWidth 2690 > clientWidth 25`、`scrollHeight 1102 > clientHeight 781` → **底部横向滑块 + 纵向滑块都能拖**；滚轮缩放由 **Ctrl+滚轮** 实现（`onWheel`，0.4×~2.4×）；`dataZoom` **对 calendar 坐标系无效**（ECharts 硬限制，实测不响应），故用「缩放」下拉 + Ctrl+滚轮 + 年份下拉等价替代。bundle 里出现的 74 处 `dataZoom` **全部是 ECharts 库内部代码**，热力图 option 段内 `dataZoom` 为 **0 次** |
| 5 | 跨度大防卡顿（周/月聚合 或 一年一行堆叠） | ✅（采用后一种） | 采用 **"一年一块、横向堆叠"**（正是要求里允许的方案）；后端本来就 `GROUP BY` 按天聚合，59 万条 → 只有 2,389 个日期键，浏览器不接触原始行；另加"跨度 ≥10 年自动紧凑"（列宽 200→140，画布面积降一半）。实测 17 年跨度（含两次接口）**665 ms**，纯重渲染 **162 ms** |

---

## 四、重启与验收

**1）重启（后端改过，必须重启；前端只需强刷）**

```powershell
# 关掉旧实例（默认 5000 或你正在用的端口，例如 17957）
foreach ($port in 5000, 17957) {
  $p = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess
  if ($p) { Stop-Process -Id $p -Force; "killed $port -> $p" }
}
# 必须在有 wxdump_work\conf_auto.json 的目录下启动（柳岸配置在这里）
cd C:\Users\Administrator
wxdump ui
```
浏览器里按 **Ctrl+F5** 强刷（清掉旧的 StatisticsView JS 缓存）。

> 已经起好一个验证实例：**http://127.0.0.1:17957/s/index.html#/statistics**（在 `C:\Users\Administrator` 下启动，指向柳岸），可直接打开验收。

**2）验收清单（柳岸，2018–2026）**

1. 工具条上出现 **「缩放：」下拉**（紧凑/标准/放大）；年份下拉里是 **全部年份 + 2018 年 … 2026 年**（共 10 项）。
2. 热力图**最左边一块日历是 2018**（9 月起有色块）；标题下方副标题显示 `2018-09-12 ~ 2026-10-04　共 9 年`。
3. 底部**横向滚动条能拖**到最右（2026），窗口变窄时也能拖；内容变高时右侧/底部**纵向滚动**同样生效。
4. 把「缩放」切到 **放大**，格子变大、可滚范围变大；**按住 Ctrl 滚轮**能连续放大/缩小。
5. 切到「聚客猫」（只有 2026 年数据）：只渲染 1 块日历，不会出现空白年份。

**3）命令行快速核对（可复制）**

```powershell
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe" -c "import json,urllib.request;r=urllib.request.Request('http://127.0.0.1:17957/api/rs/date_range',data=b'{\"wxid\":\"\"}',headers={'Content-Type':'application/json'});print(json.dumps(json.loads(urllib.request.urlopen(r).read())['body'],ensure_ascii=False))"
```
应输出 `min_date 2018-09-12`、`max_date 2026-10-04`、`span_years 9` 与逐年条数。

---

## 五、回滚

| 文件 | 备份 |
|---|---|
| `pywxdump\db\dbMSG.py` | `dbMSG.py.bak_heatmap` |
| `pywxdump\api\remote_server.py` | `remote_server.py.bak_heatmap` |
| `pywxdump\ui\web\assets\StatisticsView-_MI6G2N3.js` | `.bak_heatmap`（本轮前）、`.bak_zoomfix`（zAut 修正前） |

```powershell
cd D:\PyWxDump_Source\pywxdump
Copy-Item .\db\dbMSG.py.bak_heatmap .\db\dbMSG.py -Force
Copy-Item .\api\remote_server.py.bak_heatmap .\api\remote_server.py -Force
Copy-Item .\ui\web\assets\StatisticsView-_MI6G2N3.js.bak_heatmap .\ui\web\assets\StatisticsView-_MI6G2N3.js -Force
```

---

## 六、边界与说明（如实交代）

1. **柳岸的库最早就是 2018-09-12**：这是数据侧的客观事实（当年只有 2018-09-12 起的记录），不是显示被截断。你用"2010–2026"举例的场景，本轮用**注入的 17 年数据**验证过：2010 会正常渲染、也能滚到，逻辑上不存在上限。
2. 画布高度由写死的 `1360px` 改为按单元格大小计算，标准缩放下是 **1,380px**（差 20px，视觉无感）。
3. 「状态栏副标题」是新增的第 4 处可见变化（`数据范围 … 共 N 年`），方便一眼确认范围是否完整。
4. 若以后想要"月/周粒度"的更粗视图：后端 `date_count` 本来就带 `time_format` 参数（`%Y-%m`＝按月、`%Y-%W`＝按周），但**日历热力图的格子是天**，月/周汇总塞不进日历格，所以本轮没有启用；需要的话我可以另做一个"月度趋势"小图，不动热力图。
5. **未改动任何 3.x 逻辑**，3.x 老库回归测试通过（见上表）。
