# Spike 001 交接档案（给 dev 子代理）

## 任务
完成 P2 三 Spike 的剩余验证，产出 verdict 报告。项目: E:\147\workspaces\drawing-review-system

## 已完成（勿重复）
- **S-3 企业模板**: ✅ VALIDATED。LB26.SLDDRW 反存 .drwdot 成功（93KB），自定义属性齐全（Number/Description/Material/Weight/公司名称等，$PRP 数据源存在）。产物: spikes\001-sw-native\output\LB26-template.drwdot
- **S-2 快照机制**: ✅ 机制 VALIDATED。Extension.SaveAs PNG 0.2s / PDF 0.5s，无弹窗。注意：LB26.SLDDRW 本身是空白模板图（无视图），所以快照空白是图纸内容问题不是导出问题；需对**有视图的图纸**（S-1 产物）再验一次快照质量
- **重大发现**: LB26.00000拉臂总成.SLDDRW 经自定义属性证实是**空白模板**（Author='您的姓名'），不是完成的图纸 → 验收基准实物可能是同目录 LB26.00000拉臂总成.DWG，已上报老板

## 待完成
1. **S-1 尺寸筛选**:
   - 插视图：CreateDrawViewFromModelView3(**模型文件路径字符串**, "*Front", x_m, y_m, 0) — 第一参数是路径不是对象（生产代码 app/generators/sw_drawing.py 已验证）
   - InsertModelAnnotations3: gen_py dir() 里没找到（Extension 和 IModelDoc2 都查了）。API 文档说它在 IDrawingDoc 上——先跑探针 `[m for m in dir(drwD) if 'Model' in m]`（drwD = CastTo 或 NewDocument 返回值）；找不到就试 IDrawingDoc.InsertModelAnnotations3 直接调
   - 导入后枚举 DisplayDimension（GetFirstDisplayDimension5/GetNext5），验证 SelectByID2+DeleteSelection2 删除可行性
2. **S-0 侦察**（可选加分）: IDrawingDoc 用 GetSheetNames/Sheet(name)（没有 GetFirstSheet），摸清 LB26.SLDDRW 结构
3. **S-2 补验**: 对 S-1 有视图的图纸导出 PNG，目视确认视图轮廓清晰可见

## COM 调用纪律（血泪教训，必须遵守）
- 脚本: spikes\001-sw-native\spike_sw_native.py（v4 骨架可改），探针: probe.py
- **绑定**: 全程 gen_py 早期绑定（wc.GetObject 直接返回 gen_py）；byref 参数**直接传 int**，返回值是元组（第0位是真返回值）
- **属性怪癖**: GetTitle/RevisionNumber 可能是属性不是方法 → 用 P(x)=x() if callable(x) else x
- **接口转换**: CastTo(drw,'IDrawingDoc') 对 OpenDoc6 返回的 doc 有效；NewDocument 返回的对象直接调方法即可（生产代码先例）
- **中文文件**: 改代码用 write/edit 工具，严禁 PowerShell Set-Content 改写含中文的 .py（已踩雷一次）
- **SW 进程**: 只有 1 个实例（pid 34536，老板授权我启动的）。连接用 GetObject；**严禁杀进程、严禁关老板的文档**；脚本崩了没关系，SW 留着别动
- 弹窗处理：OpenDoc6 已带 Silent=1；遇到意外弹窗**停止并上报**，禁止写自动关窗 hack

## 输出
- 更新 spikes\001-sw-native\output\results.json + spike.log
- 写 spikes\001-sw-native\VERDICT.md：三问各自的 VALIDATED/PARTIAL/INVALIDATED + 证据 + 生产建议
- 回复中给出 VERDICT 全文
