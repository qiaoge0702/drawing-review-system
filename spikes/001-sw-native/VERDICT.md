# Spike 001 — SW 原生出图可行性验证 VERDICT

日期：2026-08-02 ｜ 环境：SolidWorks 2025（中文版，pid 34536）+ Python 3.12 + pywin32 gen_py 早期绑定
样件：LB26拉臂装置（LB26.00000拉臂总成.SLDASM / LB26.00001旋转轴.SLDPRT / LB26.SLDDRW 模板）

---

## S-1 尺寸筛选（导入模型尺寸 → 枚举 → 选择性删除）：**VALIDATED**

**结论：全链路跑通。插视图 → 导入模型尺寸（14 个）→ 逐个 SelectByID2 + DeleteSelection2 删除（5/5 成功，剩余 9）。**

证据（spike_s1g.py / output/results_s1g.json / spike_s1g.log）：

1. **插视图**：`drw.CreateDrawViewFromModelView3(模型文件路径字符串, "*前视", x米, y米, 0)` → OK，视图名 工程图视图5。
   - ⚠️ 中文版 SW 预定义视图名必须中文（`*前视`/`*上视`/`*左视`），`*Front` 返回 NULL（生产代码 config.py 已内置此映射，spike 侧踩了一次）。
2. **导入模型尺寸**：正确调用路径是 **`IDrawingDoc.InsertModelAnnotations2(32768, True, 0, True, True, True)`** → 一次导入 **14 个尺寸**。
   - gen_py 签名：`IMA2(Option:int, AllTypes:bool, Types:int, AllViews:bool, DuplicateDims:bool, HiddenFeatureDims:bool)`，返回 bool。
   - `InsertModelAnnotations3`（6 参）对本零件全部 Option/Types 组合返回 0 尺寸（装配体上 Option=1=swInsertCThreads 时返回 7 个装饰螺纹线注解——证明 IMA3 可调用但语义不符尺寸导入需求）。**生产用 IMA2，不用 IMA3。**
   - swconst.tlb 实测枚举（swconst.tlb 未注册到注册表版本，需按文件路径 LoadTypeLib 解析）：swInsertDimensionsMarkedForDrawing=**32768**、NotMarkedForDrawing=**524288**、swInsertCThreads=1。HANDOFF 里 "32767=swInsertAll" 是错的。
3. **枚举**：`IView.GetFirstDisplayDimension5()` + `IFeature.GetNextDisplayDimension(dd)` 迭代（注意 Next 在 Feature 上，不在 DisplayDimension 上）→ before=0 / after=14。
4. **删除**：`Extension.SelectByID2("D1@草图1@LB26.00001旋转轴-1@工程图视图5", "DIMENSION", ...)` + `Extension.DeleteSelection2(1)` → **deleted=5/5，remain=9**。尺寸全名格式 `Dn@草图@零件-实例@视图名`，按名筛选删除可行。

### COM 工程化要点（新增血泪教训，并入生产纪律）
- **NewDocument / ActiveDoc 返回的对象 typeinfo 损坏**（GetTypeInfo 无效索引 → CastTo/EnsureDispatch 全挂，方法解析成 None）。**绕行方案**：手工 `QueryInterface(gen_py接口类.CLSID, IID_IDispatch)` + 直接实例化 gen_py 类包装（见 spike_s1g.py 的 `wrap()`）。所有接口（IDrawingDoc/IView/IDisplayDimension/IModelDoc2）逐层 wrap 后一切正常。
- `dir()` 对动态派发对象同样会炸（`_dir_ole_` 依赖 GetTypeInfo）；探针对 gen_py 类用 `dir(type(obj))`。
- IMA3 返回的注解 COMObject **不一定支持 IDisplayDimension**（螺纹线案例只支持 IAnnotation）；删除走名字 + SelectByID2 最稳，不要依赖接口强转。

## S-2 快照机制（PNG/PDF 导出）：**VALIDATED**

- Extension.SaveAs PNG ≈0.3–0.4s / PDF ≈0.5s，静默无弹窗（此前已验）。
- 补验（有视图的图纸）：S1/S1d/S1g 三个 PNG 经图像识别确认**图框、标题栏、视图轮廓均清晰可见**——非空白，导出管线无问题。
- 已知限制：企业模板默认 1:100 比例下零件视图在 A0 图幅上占比极小，尺寸数字在 PNG 位图上不可读。**这是布局/比例适配问题，不是导出机制问题**；生产侧需布局引擎按零件包围盒自动选图幅+比例（step3 布局引擎职责范围）。
- 另：`IView.SetScale2` 在本版 gen_py 不存在；比例设置建议走图纸 Sheet 比例或视图属性（生产中验证）。

## S-3 企业模板反存：**VALIDATED**（此前已完成）

- LB26.SLDDRW → Extension.SaveAs .drwdot 成功（93KB），自定义属性齐全（Number/Description/Material/Weight/公司名称等，$PRP 数据源存在）。产物：output/LB26-template.drwdot。
- 本次 S-1 全部实验均用该模板建图，NewDocument(模板路径) 工作正常，图框/标题栏随模板带出。

---

## 重大副产品：验收基准再确认
- LB26.00000拉臂总成.SLDDRW 经自定义属性证实为**空白模板**（Author='您的姓名'），非完成图纸；同目录存在 LB26.00000拉臂总成.DWG（946KB，疑似真图）。已上报老板，待确认验收基准。

## 生产建议
1. **尺寸筛选管线定型**：NewDocument(模板) → CreateDrawViewFromModelView3(路径,"*前视") → IMA2(32768,True,0,True,True,True) → 枚举 DisplayDimension → 按命名规则（`Dn@特征@零件@视图`）SelectByID2+DeleteSelection2 删除不合格尺寸。全部静默、无弹窗、单图 <2s。
2. **COM 封装层必须内置 wrap() 兜底**：所有 NewDocument/ActiveDoc/视图/尺寸对象经 QueryInterface 手工包装，禁止裸 CastTo/裸调动态派发对象的方法。
3. **视图名中文化**：预定义视图名从 config.py 的 predefined_view_names 读取，禁止硬编码 "*Front"。
4. **比例/图幅自适应**：出图前按零件包围盒选图幅与比例（1:100 下 A0 位图尺寸不可读），否则快照审查环节无法读尺寸。
5. 装配体级 IMA 语义与零件级不同（螺纹线等注解走 IAnnotation 接口），生产过滤逻辑按 `SelectByID2(name,"DIMENSION")` 名字+类型双判定，不要强转接口。

## 产物清单（spikes/001-sw-native/）
- spike_s1.py … spike_s1g.py（迭代记录，s1g 为最终验证脚本）
- probe_dims.py（零件特征尺寸遍历探针）、probe_swconst.py（swconst.tlb 枚举解析器）
- output/results_s1[a-g].json、spike_s1[a-g].log、S1/S1d/S1g-snapshot.png、LB26-template.drwdot
