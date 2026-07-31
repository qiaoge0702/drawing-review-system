需要你查的 4 个 API 点
#	查什么	关键字（API Help 搜索用）
1	GetVisibleEntities2 的 TypeCode 完整枚举	swViewEntityType_e — 我们要确认哪个值是 hidden edge（可见边/隐藏边/轮廓边/交线的码分别是多少）
2	视图显示模式的 API 设置	IView::SetDisplayMode3 和 swDisplayMode_e（Wireframe / HiddenLinesVisible / HiddenLinesRemoved / Shaded）— 怀疑要先设 HLV 再 ForceRebuild3 才读得到隐藏边
3	是否有专门的隐藏边接口	搜 hidden edge、IView 下的 GetHiddenEdgeCount/Edges 类方法，或 IEdge 的 visibility 属性
4	工程图视图属性"显示隐藏线"	对应 SW 界面里视图右键 → 属性 → 显示隐藏线，查这个开关在 API 里的属性名（可能在 IView 的 DisplayMode 或某个 Feature 属性上）