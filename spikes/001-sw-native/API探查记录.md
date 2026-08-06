# SW 原生视图 API 探查记录（真机实证）

> 本文件是 SW API 签名/行为的**唯一落盘来源**。任何 API 探查结论必须当日写入本文件，禁止只留在会话里。

## 2026-08-06 probe（spikes/probe_sw_apis.py，SW 2025 真机，LB26 总成）

| API | 实证结果 | 细节 |
|-----|----------|------|
| `CreateDrawViewFromModelView3` | ✅ 生产在用 | `"*前视"`/`"*Front"` 中英文候选 ≤2 次 |
| `Create1stAngleViews2(model_path)` | ✅ **成功** | **返回值是 bool（True=成功），不是视图数组**；视图对象需自行枚举图纸页 |
| 草图 `CreateCircleByRadius` / `CreateLine` | ✅ 成功 | ActivateView(父视图名) 后可画 |
| `CreateDetailViewAt3(X,Y,Z,Style,Scale1,Scale2,Label,Showtype,FullOutline)` | ✅ **成功（2026-08-06 官方签名+真机实证）** | 9 参；`(0, 2.0, 1.0, "A", 1, False)` 可用；草图圆新建后自动选中无需 SelectByID2；已封装入 sw_drawing.create_detail_view |
| `CreateSectionViewAt4(x, y, z, 0, excluded)` | ❌ 参数 5 类型不匹配 | None / 空 tuple / 空 VT_ARRAY|VT_DISPATCH 变体均失败 |

| `CreateSectionViewAt4(X, Y, Z, SectionLabel:str, Options:int, ExcludedComponents:obj)` | ✅ **成功（2026-08-06 官方签名+真机实证）** | 6 参；`("A", 0, None)` 可用；新建草图线自动选中无需 SelectByID2；Options 位掩码见 swCreateSectionViewAtOptions_e（0x4 翻转/0x40 排紧固件/0x10 局部剖）；已封装入 sw_drawing.create_section_view |

**待官方文档确认（已报老板，2026-08-06）**：
1. ~~CreateDetailViewAt3 签名~~ ✅ 已闭环（见上表）
2. ~~CreateSectionViewAt4 签名~~ ✅ 已闭环（见上表）

**遗留小项（不阻塞）**：
- 局部放大草图圆坐标系（图纸 vs 父视图草图）待 B-M1 端到端时目视确认
- `SelectByID2` 调用签名待查（当前场景均不需要）

**SelectByID2 备注**：`drw.Extension.SelectByID2("", "SKETCHSEGMENT", x, y, 0, False, 0, None, 0)` 报参数 8 类型不匹配（调用签名待查，剖视场景实证不需要）

## 官方文档存档（2026-08-06 老板提供）

### CreateDetailViewAt3（9 参；Obsolete，新版 At4 但本版可用）

```
CreateDetailViewAt3(X:dbl, Y:dbl, Z:dbl, Style:int, Scale1:dbl, Scale2:dbl,
                    LabelIn:str, Showtype:int, FullOutline:bool) -> View
```
- X/Y/Z：放大视图位置（图纸空间，米）
- Style：`swDetViewStyle_e`：STANDARD=0 / BROKEN=1 / LEADER=2 / NOLEADER=3 / CONNECTED=4
- Scale1/Scale2：比例分子/分母（2:1 → Scale1=2, Scale2=1）
- LabelIn：标记字母（如 "A"）
- Showtype：`swDetCircleShowType_e`：PROFILE=0 / CIRCLE=1 / DONTSHOW=2
- **无 ParentView 参数**：父视图由选中的草图圆隐含（同剖视线，新建草图自动选中）

### CreateSectionViewAt4（6 参；Obsolete，新版 At5 但本版可用）

```
CreateSectionViewAt4(X:dbl, Y:dbl, Z:dbl, SectionLabel:str, Options:int,
                     ExcludedComponents:obj) -> View
```
- Options：`swCreateSectionViewAtOptions_e` 位掩码：NotAligned=0x1 / OffsetSection=0x2 /
  ChangeDirection=0x4 / ScaleWithModel=0x8 / Partial=0x10 / DisplaySurfaceCut=0x20 /
  ExcludeFasteners=0x40 / CutSurfaceBodies=0x80
- ExcludedComponents：排除零件数组，None 可用（真机实证）
- 调用前剖切线需选中（新建草图线自动选中，真机实证）

### CreateUnfoldedViewAt3（钣金展开，暂不需要，存档备用）

```
CreateUnfoldedViewAt3(X:dbl, Y:dbl, Z:dbl, NotAligned:bool) -> View
```

**历史教训**：8-05 状态文件"参数已确认"= 老板拍板 API 选型（路线确认），签名级探查此前从未落盘，导致 8-06 重复申请真机窗口。此后凡探查结论当日写入本文件。
