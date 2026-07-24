"""
审查报告生成模块
生成 Markdown 格式审查报告
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class ReportGenerator:
    """审查报告生成器"""
    
    def generate_markdown(
        self,
        drawing_info: Dict[str, Any],
        rule_issues: List[Dict[str, Any]],
        rule_summary: Dict[str, int],
        ai_result: Optional[Dict[str, Any]] = None,
        materials: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        生成 Markdown 格式审查报告
        
        Args:
            drawing_info: 图纸基本信息
            rule_issues: 规则检查问题列表
            rule_summary: 规则检查摘要
            ai_result: AI 审查结果（可选）
            materials: 材料数据（可选）
            
        Returns:
            Markdown 格式报告
        """
        lines = []
        
        # 标题
        lines.append("# 专用车辆上装设计图纸审查报告")
        lines.append("")
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        # 图纸信息
        lines.append("---")
        lines.append("## 一、图纸基本信息")
        lines.append("")
        
        metadata = drawing_info.get("metadata", {})
        extents = drawing_info.get("extents", {})
        entity_counts = drawing_info.get("entity_counts", {})
        
        lines.append("| 项目 | 内容 |")
        lines.append("|------|------|")
        lines.append(f"| 图样名称 | {metadata.get('title', '—')} |")
        lines.append(f"| 图样代号 | {metadata.get('drawing_no', '—')} |")
        lines.append(f"| 比例 | {metadata.get('scale', '—')} |")
        lines.append(f"| 材料 | {metadata.get('material', '—')} |")
        lines.append(f"| 图纸宽度 | {extents.get('width', 0):.0f} mm |")
        lines.append(f"| 图纸高度 | {extents.get('height', 0):.0f} mm |")
        lines.append(f"| 实体总数 | {drawing_info.get('total_entities', 0)} |")
        lines.append(f"| 图层数 | {drawing_info.get('layer_count', 0)} |")
        lines.append(f"| 预估类型 | {drawing_info.get('estimated_type', '—')} |")
        lines.append("")
        
        # 规则检查结果
        lines.append("---")
        lines.append("## 二、规则检查结果（GB 标准硬校验）")
        lines.append("")
        
        total = rule_summary.get("total", 0)
        critical = rule_summary.get("critical", 0)
        warning = rule_summary.get("warning", 0)
        info = rule_summary.get("info", 0)
        
        lines.append(f"**检查摘要**: 共发现 {total} 个问题")
        lines.append(f"- 严重: {critical}")
        lines.append(f"- 警告: {warning}")
        lines.append(f"- 提示: {info}")
        lines.append("")
        
        if rule_issues:
            lines.append("| 级别 | 类别 | 问题 | 描述 | 建议 | 标准 |")
            lines.append("|------|------|------|------|------|------|")
            
            sev_map = {"critical": "严重", "warning": "警告", "info": "提示"}
            cat_map = {
                "structure": "结构", "regulation": "法规", "safety": "安全",
                "process": "工艺", "dimension": "尺寸", "material": "材料",
                "weld": "焊接", "drawing": "图纸规范", "other": "其他"
            }
            
            for issue in rule_issues:
                sev = sev_map.get(issue.get("severity", ""), "未知")
                cat = cat_map.get(issue.get("category", ""), "其他")
                title = issue.get("title", "")
                desc = issue.get("description", "").replace("|", "\\|")
                sug = issue.get("suggestion", "").replace("|", "\\|")
                std = issue.get("standard", "")
                lines.append(f"| {sev} | {cat} | {title} | {desc} | {sug} | {std} |")
            lines.append("")
        else:
            lines.append("✅ 所有规则检查通过")
            lines.append("")
        
        # AI 审查结果（如果有）
        if ai_result:
            lines.append("---")
            lines.append("## 三、AI 智能审查结果")
            lines.append("")
            
            vehicle_name = ai_result.get("vehicle_name", "未知")
            confidence = ai_result.get("confidence", 0)
            reasoning = ai_result.get("reasoning", "")
            
            lines.append(f"**车型识别**: {vehicle_name} (置信度: {confidence}%)")
            lines.append(f"**识别依据**: {reasoning}")
            lines.append("")
            
            ai_issues = ai_result.get("issues", [])
            if ai_issues:
                lines.append(f"**AI 发现问题**: {len(ai_issues)} 个")
                lines.append("")
                
                for i, issue in enumerate(ai_issues, 1):
                    sev = issue.get("severity", "info")
                    sev_text = {"critical": "🔴 严重", "warning": "🟡 警告", "info": "🔵 提示"}.get(sev, "⚪ 未知")
                    lines.append(f"### {i}. {issue.get('title', '未命名问题')} {sev_text}")
                    lines.append("")
                    lines.append(f"**描述**: {issue.get('description', '')}")
                    lines.append("")
                    if issue.get("suggestion"):
                        lines.append(f"**建议**: {issue.get('suggestion')}")
                        lines.append("")
                    if issue.get("standard"):
                        lines.append(f"**参考标准**: {issue.get('standard')}")
                        lines.append("")
            
            recommendations = ai_result.get("recommendations", [])
            if recommendations:
                lines.append("### 优化建议")
                lines.append("")
                for rec in recommendations:
                    lines.append(f"- {rec}")
                lines.append("")
        
        # 材料数据摘要（如果有）
        if materials:
            lines.append("---")
            lines.append("## 四、材料数据摘要")
            lines.append("")
            
            bom = materials.get("bom", {})
            dims = materials.get("dimensions", {})
            texts = materials.get("texts", {})
            welds = materials.get("welds", {})
            
            lines.append(f"- BOM 明细: {bom.get('count', 0)} 条")
            lines.append(f"- 尺寸标注: {dims.get('count', 0)} 条")
            lines.append(f"- 文字内容: {texts.get('total_count', 0)} 条")
            lines.append(f"- 焊接符号: {welds.get('count', 0)} 条")
            lines.append("")
            
            # BOM 明细表
            bom_items = bom.get("items", [])
            if bom_items:
                lines.append("### BOM 明细表")
                lines.append("")
                lines.append("| 件号 | 名称 | 数量 | 材料 | 规格 | 重量 | 备注 |")
                lines.append("|------|------|------|------|------|------|------|")
                for item in bom_items[:20]:  # 限制条数
                    lines.append(
                        f"| {item.get('item_no', '-')} | {item.get('name', '-')} | "
                        f"{item.get('qty', '-')} | {item.get('material', '-')} | "
                        f"{item.get('spec', '-')} | {item.get('weight', '-')} | "
                        f"{item.get('remark', '-')} |"
                    )
                if len(bom_items) > 20:
                    lines.append(f"| ... | 共 {len(bom_items)} 条 | | | | | |")
                lines.append("")
        
        # 结论
        lines.append("---")
        lines.append("## 五、审查结论")
        lines.append("")
        
        overall_status = "通过"
        if critical > 0:
            overall_status = "未通过"
        elif warning > 0:
            overall_status = "有条件通过"
        
        lines.append(f"**审查结论**: {overall_status}")
        lines.append("")
        
        if critical > 0:
            lines.append(f"发现 {critical} 个严重问题，必须修改后重新提交审查。")
        elif warning > 0:
            lines.append(f"发现 {warning} 个警告问题，建议修改后重新提交审查。")
        else:
            lines.append("未发现严重或警告级别问题，设计符合基本要求。")
        lines.append("")
        
        # 签名区
        lines.append("---")
        lines.append("| 角色 | 签名 | 日期 |")
        lines.append("|------|------|------|")
        lines.append("| 设计 | | |")
        lines.append("| 校对 | | |")
        lines.append("| 审核 | | |")
        lines.append("| 批准 | | |")
        lines.append("")
        
        return "\n".join(lines)


# 便捷函数
_generator: Optional[ReportGenerator] = None


def get_report_generator() -> ReportGenerator:
    """获取全局报告生成器实例"""
    global _generator
    if _generator is None:
        _generator = ReportGenerator()
    return _generator


def generate_report(
    drawing_info: Dict[str, Any],
    rule_issues: List[Dict[str, Any]],
    rule_summary: Dict[str, int],
    ai_result: Optional[Dict[str, Any]] = None,
    materials: Optional[Dict[str, Any]] = None,
) -> str:
    """生成审查报告（便捷函数）"""
    return get_report_generator().generate_markdown(
        drawing_info, rule_issues, rule_summary, ai_result, materials
    )
