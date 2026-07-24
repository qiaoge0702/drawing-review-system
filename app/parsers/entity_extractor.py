"""
实体提取器模块
从DXF文档中提取各类实体并进行统计
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from collections import defaultdict

import ezdxf

from app.models.drawing import ExtractedEntities, LayerInfo

logger = logging.getLogger(__name__)


class EntityExtractor:
    """
    DXF实体提取器
    
    提取DXF文件中的各类实体信息并进行统计
    """
    
    # 支持的实体类型映射
    ENTITY_TYPES = {
        'LINE': 'line',
        'CIRCLE': 'circle',
        'ARC': 'arc',
        'POLYLINE': 'polyline',
        'LWPOLYLINE': 'lwpolyline',
        'DIMENSION': 'dimension',
        'TEXT': 'text',
        'MTEXT': 'mtext',
        'INSERT': 'insert',
        'HATCH': 'hatch',
        'ELLIPSE': 'ellipse',
        'SPLINE': 'spline',
        'POINT': 'point',
        'RAY': 'ray',
        'XLINE': 'xline',
        'LEADER': 'leader',
        'MLEADER': 'mleader',
        '3DFACE': '3dface',
        'SOLID': 'solid',
        'TRACE': 'trace',
        'MESH': 'mesh',
    }
    
    def __init__(self, doc: ezdxf.document.Drawing, msp: ezdxf.layouts.Modelspace):
        """
        初始化提取器
        
        Args:
            doc: ezdxf文档对象
            msp: 模型空间
        """
        self.doc = doc
        self.msp = msp
    
    def extract(self) -> ExtractedEntities:
        """
        提取所有实体信息
        
        Returns:
            ExtractedEntities: 实体统计信息
        """
        logger.debug("开始提取实体信息")
        
        # 统计各类实体数量
        counts = self._count_entities()
        
        # 提取图层信息
        layers = self._extract_layers()
        
        # 按类型分组提取实体数据
        entities_by_type = self._extract_entities_by_type()
        
        result = ExtractedEntities(
            layer_count=len(layers),
            line_count=counts.get('line', 0),
            circle_count=counts.get('circle', 0),
            arc_count=counts.get('arc', 0),
            polyline_count=counts.get('polyline', 0),
            lwpolyline_count=counts.get('lwpolyline', 0),
            dimension_count=counts.get('dimension', 0),
            text_count=counts.get('text', 0),
            mtext_count=counts.get('mtext', 0),
            insert_count=counts.get('insert', 0),
            hatch_count=counts.get('hatch', 0),
            ellipse_count=counts.get('ellipse', 0),
            spline_count=counts.get('spline', 0),
            layers=layers,
            entities=entities_by_type
        )
        
        logger.debug(f"实体提取完成: {result.get_total_entity_count()} 个实体")
        return result
    
    def _count_entities(self) -> Dict[str, int]:
        """统计各类实体数量"""
        counts = defaultdict(int)
        
        for entity in self.msp:
            entity_type = entity.dxftype()
            mapped_type = self.ENTITY_TYPES.get(entity_type, entity_type.lower())
            counts[mapped_type] += 1
        
        return dict(counts)
    
    def _extract_layers(self) -> List[LayerInfo]:
        """提取图层信息"""
        layers = []
        
        for layer in self.doc.layers:
            try:
                # ezdxf 1.4+ 使用 is_on() 方法而不是 on 属性
                layer_info = LayerInfo(
                    name=layer.dxf.name,
                    color=layer.dxf.color,
                    is_on=layer.is_on(),
                    is_frozen=layer.is_frozen(),
                    is_locked=layer.is_locked(),
                    linetype=layer.dxf.linetype,
                    lineweight=getattr(layer.dxf, 'lineweight', None)
                )
                layers.append(layer_info)
            except Exception as e:
                logger.warning(f"提取图层信息失败: {e}")
        
        return layers
    
    def _extract_entities_by_type(self) -> Dict[str, List[Dict[str, Any]]]:
        """按类型分组提取实体数据"""
        entities_by_type = defaultdict(list)
        
        for entity in self.msp:
            try:
                entity_type = entity.dxftype()
                mapped_type = self.ENTITY_TYPES.get(entity_type, entity_type.lower())
                
                # 提取实体数据
                entity_data = self._extract_entity_data(entity)
                if entity_data:
                    entities_by_type[mapped_type].append(entity_data)
                    
            except Exception as e:
                logger.debug(f"提取实体数据失败: {e}")
        
        return dict(entities_by_type)
    
    def _extract_entity_data(self, entity) -> Optional[Dict[str, Any]]:
        """
        提取单个实体的数据
        
        Args:
            entity: DXF实体对象
            
        Returns:
            实体数据字典，提取失败返回None
        """
        entity_type = entity.dxftype()
        
        try:
            # 基础属性
            data = {
                'type': entity_type,
                'handle': entity.dxf.handle if hasattr(entity.dxf, 'handle') else None,
                'layer': entity.dxf.layer if hasattr(entity.dxf, 'layer') else '0',
            }
            
            # 根据类型提取特定属性
            if entity_type == 'LINE':
                data.update(self._extract_line_data(entity))
            elif entity_type == 'CIRCLE':
                data.update(self._extract_circle_data(entity))
            elif entity_type == 'ARC':
                data.update(self._extract_arc_data(entity))
            elif entity_type == 'LWPOLYLINE':
                data.update(self._extract_lwpolyline_data(entity))
            elif entity_type == 'POLYLINE':
                data.update(self._extract_polyline_data(entity))
            elif entity_type == 'TEXT':
                data.update(self._extract_text_data(entity))
            elif entity_type == 'MTEXT':
                data.update(self._extract_mtext_data(entity))
            elif entity_type == 'DIMENSION':
                data.update(self._extract_dimension_data(entity))
            elif entity_type == 'INSERT':
                data.update(self._extract_insert_data(entity))
            elif entity_type == 'HATCH':
                data.update(self._extract_hatch_data(entity))
            elif entity_type == 'ELLIPSE':
                data.update(self._extract_ellipse_data(entity))
            elif entity_type == 'SPLINE':
                data.update(self._extract_spline_data(entity))
            elif entity_type == 'POINT':
                data.update(self._extract_point_data(entity))
            elif entity_type == 'RAY':
                data.update(self._extract_ray_data(entity))
            elif entity_type == 'XLINE':
                data.update(self._extract_xline_data(entity))
            elif entity_type == 'LEADER':
                data.update(self._extract_leader_data(entity))
            elif entity_type == 'MLEADER':
                data.update(self._extract_mleader_data(entity))
            
            return data
            
        except Exception as e:
            logger.debug(f"提取 {entity_type} 实体数据失败: {e}")
            return None
    
    def _extract_line_data(self, entity) -> Dict[str, Any]:
        """提取直线数据"""
        return {
            'start': (entity.dxf.start.x, entity.dxf.start.y, getattr(entity.dxf.start, 'z', 0)),
            'end': (entity.dxf.end.x, entity.dxf.end.y, getattr(entity.dxf.end, 'z', 0)),
            'length': entity.dxf.start.distance(entity.dxf.end),
        }
    
    def _extract_circle_data(self, entity) -> Dict[str, Any]:
        """提取圆数据"""
        return {
            'center': (entity.dxf.center.x, entity.dxf.center.y, getattr(entity.dxf.center, 'z', 0)),
            'radius': entity.dxf.radius,
            'diameter': entity.dxf.radius * 2,
        }
    
    def _extract_arc_data(self, entity) -> Dict[str, Any]:
        """提取圆弧数据"""
        return {
            'center': (entity.dxf.center.x, entity.dxf.center.y, getattr(entity.dxf.center, 'z', 0)),
            'radius': entity.dxf.radius,
            'start_angle': entity.dxf.start_angle,
            'end_angle': entity.dxf.end_angle,
        }
    
    def _extract_lwpolyline_data(self, entity) -> Dict[str, Any]:
        """提取轻量多段线数据"""
        points = []
        for point in entity.get_points():
            if len(point) >= 2:
                points.append((point[0], point[1]))
        
        return {
            'points': points,
            'point_count': len(points),
            'is_closed': entity.closed,
            'has_bulge': any(len(p) > 3 and p[3] != 0 for p in entity.get_points()),
        }
    
    def _extract_polyline_data(self, entity) -> Dict[str, Any]:
        """提取多段线数据"""
        points = []
        for vertex in entity.vertices:
            points.append((vertex.dxf.location.x, vertex.dxf.location.y, vertex.dxf.location.z))
        
        return {
            'points': points,
            'point_count': len(points),
            'is_closed': entity.is_closed,
            'is_3d_polyline': entity.is_3d_polyline,
        }
    
    def _extract_text_data(self, entity) -> Dict[str, Any]:
        """提取单行文字数据"""
        return {
            'text': entity.dxf.text,
            'insert': (entity.dxf.insert.x, entity.dxf.insert.y, getattr(entity.dxf.insert, 'z', 0)),
            'height': entity.dxf.height,
            'rotation': getattr(entity.dxf, 'rotation', 0),
            'style': entity.dxf.style,
        }
    
    def _extract_mtext_data(self, entity) -> Dict[str, Any]:
        """提取多行文字数据"""
        return {
            'text': entity.text,
            'insert': (entity.dxf.insert.x, entity.dxf.insert.y, getattr(entity.dxf.insert, 'z', 0)),
            'height': entity.dxf.text_height,
            'width': getattr(entity.dxf, 'width', 0),
            'rotation': getattr(entity.dxf, 'rotation', 0),
            'style': entity.dxf.style,
        }
    
    def _extract_dimension_data(self, entity) -> Dict[str, Any]:
        """提取标注数据"""
        data = {
            'dimension_type': entity.dimtype,
            'text': entity.get_text() if hasattr(entity, 'get_text') else '',
        }
        
        # 尝试提取测量值
        try:
            if hasattr(entity, 'get_measurement'):
                data['measurement'] = entity.get_measurement()
        except Exception:
            pass
        
        return data
    
    def _extract_insert_data(self, entity) -> Dict[str, Any]:
        """提取块引用数据"""
        return {
            'block_name': entity.dxf.name,
            'insert': (entity.dxf.insert.x, entity.dxf.insert.y, getattr(entity.dxf.insert, 'z', 0)),
            'scale': (entity.dxf.xscale, entity.dxf.yscale, entity.dxf.zscale),
            'rotation': entity.dxf.rotation,
        }
    
    def _extract_hatch_data(self, entity) -> Dict[str, Any]:
        """提取填充数据（含边界路径和顶点）"""
        data = {
            'pattern_name': entity.dxf.pattern_name,
            'solid_fill': entity.dxf.solid_fill,
            'color': getattr(entity.dxf, 'color', None),
        }

        # 提取边界路径顶点
        paths = []
        try:
            for path in entity.paths:
                path_info = {
                    'path_type': path.PATH_TYPE if hasattr(path, 'PATH_TYPE') else type(path).__name__,
                    'vertices': [],
                }
                # PolylinePath
                if hasattr(path, 'vertices'):
                    path_info['vertices'] = [
                        (v[0], v[1]) if isinstance(v, (tuple, list)) else (v.x, v.y)
                        for v in path.vertices
                    ]
                # EdgePath
                elif hasattr(path, 'edges'):
                    edges_info = []
                    for edge in path.edges:
                        edge_data = {
                            'edge_type': edge.EDGE_TYPE if hasattr(edge, 'EDGE_TYPE') else type(edge).__name__,
                        }
                        if hasattr(edge, 'start') and hasattr(edge, 'end'):
                            edge_data['start'] = (edge.start[0], edge.start[1])
                            edge_data['end'] = (edge.end[0], edge.end[1])
                        elif hasattr(edge, 'center') and hasattr(edge, 'radius'):
                            edge_data['center'] = (edge.center[0], edge.center[1])
                            edge_data['radius'] = edge.radius
                        edges_info.append(edge_data)
                    path_info['edges'] = edges_info
                paths.append(path_info)
        except Exception as e:
            logger.debug(f"提取填充路径失败: {e}")

        data['paths'] = paths
        data['path_count'] = len(paths)
        return data
    
    def _extract_ellipse_data(self, entity) -> Dict[str, Any]:
        """提取椭圆数据"""
        return {
            'center': (entity.dxf.center.x, entity.dxf.center.y, getattr(entity.dxf.center, 'z', 0)),
            'major_axis': (entity.dxf.major_axis.x, entity.dxf.major_axis.y),
            'ratio': entity.dxf.ratio,
            'start_param': entity.dxf.start_param,
            'end_param': entity.dxf.end_param,
        }
    
    def _extract_spline_data(self, entity) -> Dict[str, Any]:
        """提取样条曲线数据（含控制点和拟合点坐标）"""
        data = {
            'degree': entity.dxf.degree,
            'is_closed': entity.closed,
        }

        # control_points 和 fit_points 是属性不是方法
        try:
            ctrl_pts = entity.control_points
            data['control_points'] = [(p[0], p[1], p[2] if len(p) > 2 else 0) for p in ctrl_pts]
            data['control_point_count'] = len(ctrl_pts)
        except Exception:
            data['control_points'] = []
            data['control_point_count'] = 0

        try:
            fit_pts = entity.fit_points
            data['fit_points'] = [(p[0], p[1], p[2] if len(p) > 2 else 0) for p in fit_pts]
            data['fit_point_count'] = len(fit_pts)
        except Exception:
            data['fit_points'] = []
            data['fit_point_count'] = 0

        # 起点和终点
        try:
            if data['control_points']:
                data['start_point'] = data['control_points'][0]
                data['end_point'] = data['control_points'][-1]
        except Exception:
            pass

        return data

    def _extract_point_data(self, entity) -> Dict[str, Any]:
        """提取点实体数据"""
        loc = entity.dxf.location
        return {
            'location': (loc.x, loc.y, getattr(loc, 'z', 0)),
        }

    def _extract_ray_data(self, entity) -> Dict[str, Any]:
        """提取射线数据"""
        start = entity.dxf.start
        unit = entity.dxf.unit_vector
        return {
            'start': (start.x, start.y, getattr(start, 'z', 0)),
            'unit_vector': (unit.x, unit.y, getattr(unit, 'z', 0)),
        }

    def _extract_xline_data(self, entity) -> Dict[str, Any]:
        """提取构造线数据"""
        start = entity.dxf.start
        unit = entity.dxf.unit_vector
        return {
            'start': (start.x, start.y, getattr(start, 'z', 0)),
            'unit_vector': (unit.x, unit.y, getattr(unit, 'z', 0)),
        }

    def _extract_leader_data(self, entity) -> Dict[str, Any]:
        """提取引线数据"""
        data = {
            'dimension_type': getattr(entity.dxf, 'dimtype', None),
            'text': '',
        }
        try:
            # 引线顶点
            vertices = list(entity.vertices)
            data['vertices'] = [(v[0], v[1], v[2] if len(v) > 2 else 0) for v in vertices]
            data['vertex_count'] = len(vertices)
        except Exception:
            data['vertices'] = []
            data['vertex_count'] = 0

        try:
            data['arrowhead'] = entity.dxf.has_arrowhead
        except Exception:
            pass

        try:
            data['annotation_type'] = type(entity.annotation).__name__ if entity.annotation else None
        except Exception:
            pass

        return data

    def _extract_mleader_data(self, entity) -> Dict[str, Any]:
        """提取多重引线数据"""
        data = {
            'text': '',
            'vertices': [],
        }

        # 尝试获取文字内容
        try:
            ctx = entity.context
            if ctx and hasattr(ctx, 'has_text') and ctx.has_text:
                data['text'] = ctx.text
        except Exception:
            pass

        # 尝试获取引线顶点
        try:
            for leader in entity.leaders:
                for line in leader.lines:
                    pts = list(line.vertices)
                    data['vertices'].extend(
                        [(p[0], p[1], p[2] if len(p) > 2 else 0) for p in pts]
                    )
        except Exception:
            pass

        data['vertex_count'] = len(data['vertices'])

        # 箭头信息
        try:
            data['arrowhead_type'] = entity.dxf.arrowhead_handle if hasattr(entity.dxf, 'arrowhead_handle') else None
        except Exception:
            pass

        return data
    
    def get_entities_in_layer(self, layer_name: str) -> List[Any]:
        """
        获取指定图层的所有实体
        
        Args:
            layer_name: 图层名称
            
        Returns:
            实体列表
        """
        return [e for e in self.msp if hasattr(e.dxf, 'layer') and e.dxf.layer == layer_name]
    
    def get_entities_by_type(self, entity_type: str) -> List[Any]:
        """
        获取指定类型的所有实体
        
        Args:
            entity_type: 实体类型（如 'LINE', 'CIRCLE'）
            
        Returns:
            实体列表
        """
        return list(self.msp.query(entity_type))
    
    def get_text_entities(self) -> List[Dict[str, Any]]:
        """
        获取所有文字实体（TEXT和MTEXT）
        
        Returns:
            文字实体数据列表
        """
        texts = []
        
        for entity in self.msp:
            entity_type = entity.dxftype()
            if entity_type in ('TEXT', 'MTEXT'):
                data = self._extract_entity_data(entity)
                if data:
                    texts.append(data)
        
        return texts
    
    def get_line_segments(self) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
        """
        获取所有线段（用于结构分析）
        
        Returns:
            线段列表，每个元素为 (start_point, end_point)
        """
        segments = []
        
        for entity in self.msp:
            entity_type = entity.dxftype()
            
            if entity_type == 'LINE':
                start = (entity.dxf.start.x, entity.dxf.start.y)
                end = (entity.dxf.end.x, entity.dxf.end.y)
                segments.append((start, end))
                
            elif entity_type == 'LWPOLYLINE':
                points = [(p[0], p[1]) for p in entity.get_points()]
                for i in range(len(points) - 1):
                    segments.append((points[i], points[i + 1]))
                if entity.closed and len(points) > 2:
                    segments.append((points[-1], points[0]))
                    
            elif entity_type == 'ARC':
                # 将圆弧离散化为线段
                import math
                center = (entity.dxf.center.x, entity.dxf.center.y)
                radius = entity.dxf.radius
                start_angle = math.radians(entity.dxf.start_angle)
                end_angle = math.radians(entity.dxf.end_angle)
                
                # 简单离散化：每10度一个点
                if end_angle < start_angle:
                    end_angle += 2 * math.pi
                
                num_segments = max(3, int((end_angle - start_angle) / math.radians(10)))
                angle_step = (end_angle - start_angle) / num_segments
                
                prev_point = (
                    center[0] + radius * math.cos(start_angle),
                    center[1] + radius * math.sin(start_angle)
                )
                
                for i in range(1, num_segments + 1):
                    angle = start_angle + angle_step * i
                    point = (
                        center[0] + radius * math.cos(angle),
                        center[1] + radius * math.sin(angle)
                    )
                    segments.append((prev_point, point))
                    prev_point = point
        
        return segments
