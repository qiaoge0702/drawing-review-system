"""
SolidWorks 文件解析器
通过 pywin32 + SW COM API 读取 3D 模型数据
"""

import win32com.client
import pythoncom
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class SWMaterial:
    """材料信息"""
    name: str = ""
    description: str = ""


@dataclass
class SWFeature:
    """加工特征"""
    name: str = ""
    feature_type: str = ""  # Extrude, Cut, Hole, Fillet, Chamfer...
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SWComponent:
    """装配体中的组件"""
    name: str = ""
    path: str = ""
    instance_id: str = ""
    quantity: int = 1
    is_suppressed: bool = False
    is_hidden: bool = False


@dataclass
class SWPart:
    """零件信息"""
    name: str = ""
    path: str = ""
    material: SWMaterial = field(default_factory=SWMaterial)
    features: List[SWFeature] = field(default_factory=list)
    mass: float = 0.0  # kg
    bounding_box: tuple = (0, 0, 0)  # (x, y, z) mm


@dataclass
class SWAssembly:
    """装配体信息"""
    name: str = ""
    path: str = ""
    components: List[SWComponent] = field(default_factory=list)
    mates: List[Dict[str, Any]] = field(default_factory=list)


class SWParser:
    """SolidWorks 文件解析器"""
    
    def __init__(self):
        self.sw_app = None
        self._connect()
    
    def _connect(self):
        """连接 SolidWorks"""
        try:
            self.sw_app = win32com.client.Dispatch('SldWorks.Application')
            # 确保可见（可选）
            # self.sw_app.Visible = True
            print(f"Connected to SolidWorks {self.sw_app.RevisionNumber}")
        except Exception as e:
            raise RuntimeError(f"Failed to connect SolidWorks: {e}")
    
    def open_document(self, filepath: str) -> Any:
        """打开文档"""
        filepath = str(Path(filepath).resolve())
        
        # 检查是否已打开
        doc = self._get_open_doc(filepath)
        if doc:
            print(f"Document already open: {filepath}")
            return doc
        
        # 打开文档 - 使用 OpenDoc6 并正确处理 VARIANT 参数
        try:
            doc_type = self._guess_doc_type(filepath)
            
            # 创建 VARIANT 对象用于输出参数
            from win32com.client import VARIANT
            import pythoncom
            
            errors = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            warnings = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            
            doc = self.sw_app.OpenDoc6(
                filepath,
                doc_type,
                0,  # options
                "",  # configuration
                errors,
                warnings
            )
            print(f"Opened: {filepath} (errors={errors.value}, warnings={warnings.value})")
            return doc
        except Exception as e:
            raise RuntimeError(f"Failed to open {filepath}: {e}")
    
    def _get_open_doc(self, filepath: str) -> Optional[Any]:
        """检查文档是否已打开"""
        try:
            # 尝试获取活动文档
            doc = self.sw_app.ActiveDoc
            if doc and doc.GetPathName() == filepath:
                return doc
        except:
            pass
        return None
    
    def _guess_doc_type(self, filepath: str) -> int:
        """根据扩展名猜测文档类型"""
        ext = Path(filepath).suffix.lower()
        if ext == '.sldprt':
            return 1  # swDocPART
        elif ext == '.sldasm':
            return 2  # swDocASSEMBLY
        elif ext == '.slddrw':
            return 3  # swDocDRAWING
        else:
            raise ValueError(f"Unknown file type: {ext}")
    
    def parse_assembly(self, filepath: str) -> SWAssembly:
        """解析装配体"""
        doc = self.open_document(filepath)
        
        assembly = SWAssembly(
            name=Path(filepath).stem,
            path=filepath
        )
        
        # 获取组件 - 使用 GetComponents 方法
        try:
            # 获取第一个配置
            config_names = doc.GetConfigurationNames
            
            if config_names and len(config_names) > 0:
                config = doc.GetConfigurationByName(config_names[0])
                root_comp = config.GetRootComponent
                
                if root_comp:
                    # 获取子组件 - GetChildren 返回 tuple
                    children = root_comp.GetChildren
                    
                    # 处理 tuple 类型
                    if isinstance(children, tuple):
                        for child in children:
                            if child:
                                comp = SWComponent(
                                    name=child.Name2,
                                    path=child.GetPathName,
                                    instance_id=child.Name2,
                                    is_suppressed=child.IsSuppressed,
                                    is_hidden=False
                                )
                                assembly.components.append(comp)
                    else:
                        # 处理 COM 集合类型
                        count = children.Count
                        for i in range(count):
                            child = children.Item(i + 1)
                            if child:
                                comp = SWComponent(
                                    name=child.Name2,
                                    path=child.GetPathName,
                                    instance_id=child.Name2,
                                    is_suppressed=child.IsSuppressed,
                                    is_hidden=False
                                )
                                assembly.components.append(comp)
                    
        except Exception as e:
            print(f"Warning: Failed to parse components: {e}")
        
        return assembly
    
    def parse_part(self, filepath: str) -> SWPart:
        """解析零件"""
        doc = self.open_document(filepath)
        
        part = SWPart(
            name=Path(filepath).stem,
            path=filepath
        )
        
        # 获取材料
        try:
            material = doc.MaterialUserName
            if material:
                part.material = SWMaterial(name=material)
        except:
            pass
        
        # 获取质量属性
        try:
            mass_props = doc.Extension.CreateMassProperty
            if mass_props:
                part.mass = mass_props.Mass
        except:
            pass
        
        # 获取特征
        try:
            feature_mgr = doc.FeatureManager
            features = feature_mgr.GetFeatures(True)
            
            for i in range(features.Count):
                feat = features.Item(i)
                sw_feat = SWFeature(
                    name=feat.Name,
                    feature_type=self._get_feature_type(feat)
                )
                part.features.append(sw_feat)
                
        except Exception as e:
            print(f"Warning: Failed to parse features: {e}")
        
        return part
    
    def _get_feature_type(self, feature) -> str:
        """获取特征类型名称"""
        try:
            return feature.GetTypeName2
        except:
            return "Unknown"
    
    def get_bom(self, filepath: str) -> List[Dict[str, Any]]:
        """获取BOM表"""
        doc = self.open_document(filepath)
        bom = []
        
        try:
            # 获取第一个配置
            config_names = doc.GetConfigurationNames
            if config_names and len(config_names) > 0:
                config = doc.GetConfigurationByName(config_names[0])
                root_comp = config.GetRootComponent
                
                if root_comp:
                    self._traverse_bom(root_comp, bom, 0)
                
        except Exception as e:
            print(f"Warning: Failed to get BOM: {e}")
        
        return bom
    
    def _traverse_bom(self, component, bom_list: list, level: int):
        """递归遍历BOM"""
        try:
            comp_info = {
                'level': level,
                'name': component.Name2,
                'path': component.GetPathName,
                'quantity': 1,
                'is_suppressed': component.IsSuppressed
            }
            bom_list.append(comp_info)
        except:
            pass
        
        # 递归子组件
        try:
            children = component.GetChildren
            
            # 处理 tuple 类型
            if isinstance(children, tuple):
                for child in children:
                    self._traverse_bom(child, bom_list, level + 1)
            else:
                # 处理 COM 集合类型
                count = children.Count
                for i in range(count):
                    child = children.Item(i + 1)
                    self._traverse_bom(child, bom_list, level + 1)
        except:
            pass
    
    def close_document(self, filepath: str, save: bool = False):
        """关闭文档"""
        try:
            doc = self._get_open_doc(filepath)
            if doc:
                self.sw_app.CloseDoc(doc.GetTitle)
                print(f"Closed: {filepath}")
        except Exception as e:
            print(f"Warning: Failed to close {filepath}: {e}")
    
    def quit(self):
        """退出 SolidWorks"""
        if self.sw_app:
            try:
                self.sw_app.ExitApp()
                print("SolidWorks closed")
            except:
                pass


# 测试代码
if __name__ == "__main__":
    # LB26 装配路径
    lb26_path = r"E:\147\workspaces\drawing-review-system\LB26拉臂装置\LB26.00000拉臂总成.SLDASM"
    
    parser = SWParser()
    
    try:
        # 解析装配体
        print("\n=== 解析装配体 ===")
        assembly = parser.parse_assembly(lb26_path)
        print(f"装配体: {assembly.name}")
        print(f"组件数量: {len(assembly.components)}")
        
        print("\n=== 组件列表（前10个）===")
        for i, comp in enumerate(assembly.components[:10]):
            print(f"  {i+1}. {comp.name} | 路径: {Path(comp.path).name}")
        
        # 获取BOM
        print("\n=== BOM表（前10行）===")
        bom = parser.get_bom(lb26_path)
        for i, item in enumerate(bom[:10]):
            indent = "  " * item['level']
            print(f"{indent}{item['name']} {'[隐藏]' if item['is_suppressed'] else ''}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        parser.close_document(lb26_path)
        # 不退出SW，保持打开
        # parser.quit()
