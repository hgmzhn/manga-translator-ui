"""
完全基于 desktop-ui 的几何系统
替换 Qt 的坐标系统，使用 desktop-ui 的数据结构和算法
"""
import math
from typing import List, Optional, Tuple

# === desktop-ui 的核心几何函数 ===

def rotate_point(x, y, angle_deg, cx, cy):
    """围绕中心点旋转一个点"""
    angle_rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    x_new = cx + (x - cx) * cos_a - (y - cy) * sin_a
    y_new = cy + (x - cx) * sin_a + (y - cy) * cos_a
    return x_new, y_new

def get_polygon_center(vertices: List[Tuple[float, float]]) -> Tuple[float, float]:
    """
    计算多边形的中心点（边界框中心）

    注意：lines存储的是未旋转的世界坐标，所以这里计算的是
    这些未旋转坐标的简单边界框中心，不使用cv2.minAreaRect
    """
    if not vertices:
        return 0, 0

    # 直接计算边界框中心（对于未旋转的坐标）
    x_coords = [v[0] for v in vertices]
    y_coords = [v[1] for v in vertices]

    if not x_coords or not y_coords:
        return 0, 0

    center_x = (min(x_coords) + max(x_coords)) / 2
    center_y = (min(y_coords) + max(y_coords)) / 2

    return center_x, center_y

def _project_vector(v_to_project: Tuple[float, float], v_target: Tuple[float, float]) -> Tuple[float, float]:
    """将一个向量投影到另一个向量上"""
    dot_product = v_to_project[0] * v_target[0] + v_to_project[1] * v_target[1]
    target_len_sq = v_target[0]**2 + v_target[1]**2
    if target_len_sq < 1e-9:
        return (0, 0)
    scale = dot_product / target_len_sq
    return (v_target[0] * scale, v_target[1] * scale)


def calculate_center_scaled_rect(
    original_rect: List[float],
    handle_kind: str,
    handle_index: int,
    new_point: Tuple[float, float],
    min_size: float = 8.0,
) -> List[float]:
    """围绕矩形中心实时对称缩放边或角。"""
    left, top, right, bottom = map(float, original_rect)
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    half_width = (right - left) / 2.0
    half_height = (bottom - top) / 2.0
    min_half = max(0.0, float(min_size)) / 2.0

    if handle_kind == "corner" and 0 <= handle_index < 4:
        half_width = max(abs(float(new_point[0]) - center_x), min_half)
        half_height = max(abs(float(new_point[1]) - center_y), min_half)
    elif handle_kind == "edge" and handle_index in (0, 2):
        half_height = max(abs(float(new_point[1]) - center_y), min_half)
    elif handle_kind == "edge" and handle_index in (1, 3):
        half_width = max(abs(float(new_point[0]) - center_x), min_half)

    return [
        center_x - half_width,
        center_y - half_height,
        center_x + half_width,
        center_y + half_height,
    ]


def calculate_new_vertices_on_drag(
    original_vertices: List[Tuple[float, float]],
    dragged_vertex_index: int,
    new_mouse_position: Tuple[float, float],
    angle: float = 0,
    center: Optional[Tuple[float, float]] = None
) -> List[Tuple[float, float]]:
    """当单个顶点被拖拽时，计算所有顶点的新位置。"""
    
    rotation_center = center if center else get_polygon_center(original_vertices)

    # For non-quadrilaterals, use simple logic
    if len(original_vertices) != 4:
        if angle != 0:
             new_mouse_position = rotate_point(new_mouse_position[0], new_mouse_position[1], -angle, rotation_center[0], rotation_center[1])
        new_vertices_fallback = list(original_vertices)
        new_vertices_fallback[dragged_vertex_index] = new_mouse_position
        return new_vertices_fallback

    # --- Corrected logic for rotated parallelograms ---
    # 1. Identify points in model space
    p_drag_idx = dragged_vertex_index
    p_anchor_idx = (p_drag_idx + 2) % 4
    p_adj1_idx = (p_drag_idx - 1 + 4) % 4
    p_adj2_idx = (p_drag_idx + 1) % 4

    p_anchor_model = original_vertices[p_anchor_idx]

    # If not rotated, use the original simple (but flawed) projection logic for now
    if angle == 0:
        p_adj1_orig = original_vertices[p_adj1_idx]
        p_adj2_orig = original_vertices[p_adj2_idx]
        v_anchor_adj1 = (p_adj1_orig[0] - p_anchor_model[0], p_adj1_orig[1] - p_anchor_model[1])
        v_anchor_adj2 = (p_adj2_orig[0] - p_anchor_model[0], p_adj2_orig[1] - p_anchor_model[1])
        v_anchor_mouse = (new_mouse_position[0] - p_anchor_model[0], new_mouse_position[1] - p_anchor_model[1])
        v_new_adj1 = _project_vector(v_anchor_mouse, v_anchor_adj1)
        v_new_adj2 = _project_vector(v_anchor_mouse, v_anchor_adj2)
        new_p_adj1 = (p_anchor_model[0] + v_new_adj1[0], p_anchor_model[1] + v_new_adj1[1])
        new_p_adj2 = (p_anchor_model[0] + v_new_adj2[0], p_anchor_model[1] + v_new_adj2[1])
        new_p_drag = (p_anchor_model[0] + v_new_adj1[0] + v_new_adj2[0], p_anchor_model[1] + v_new_adj1[1] + v_new_adj2[1])
        new_vertices = [ (0,0) ] * 4
        new_vertices[p_anchor_idx] = p_anchor_model
        new_vertices[p_adj1_idx] = new_p_adj1
        new_vertices[p_adj2_idx] = new_p_adj2
        new_vertices[p_drag_idx] = new_p_drag
        return new_vertices

    # --- Logic for rotated parallelograms ---
    # 1. Rotate anchor to world space
    p_anchor_world = rotate_point(p_anchor_model[0], p_anchor_model[1], angle, rotation_center[0], rotation_center[1])

    # 2. Calculate mouse drag vector in world space
    v_mouse_drag_world = (new_mouse_position[0] - p_anchor_world[0], new_mouse_position[1] - p_anchor_world[1])

    # 3. Un-rotate the mouse drag vector to get the drag in model space
    v_mouse_drag_model_x, v_mouse_drag_model_y = rotate_point(v_mouse_drag_world[0], v_mouse_drag_world[1], -angle, 0, 0)
    v_mouse_drag_model = (v_mouse_drag_model_x, v_mouse_drag_model_y)

    # 4. Decompose the model-space drag vector along the model-space sides
    p_adj1_model = original_vertices[p_adj1_idx]
    p_adj2_model = original_vertices[p_adj2_idx]
    v_side1_model = (p_adj1_model[0] - p_anchor_model[0], p_adj1_model[1] - p_anchor_model[1])
    v_side2_model = (p_adj2_model[0] - p_anchor_model[0], p_adj2_model[1] - p_anchor_model[1])

    # We need to solve v_mouse_drag_model = c1*v_side1_model + c2*v_side2_model for c1, c2
    # This is a 2x2 system of linear equations
    m_det = v_side1_model[0] * v_side2_model[1] - v_side1_model[1] * v_side2_model[0]
    if abs(m_det) < 1e-9: # Sides are collinear, cannot decompose
        return original_vertices

    # Using Cramer's rule to solve for c1 and c2
    c1 = (v_mouse_drag_model[0] * v_side2_model[1] - v_mouse_drag_model[1] * v_side2_model[0]) / m_det
    c2 = (v_side1_model[0] * v_mouse_drag_model[1] - v_side1_model[1] * v_mouse_drag_model[0]) / m_det

    # 5. Calculate new model-space points
    new_p_adj1_model = (p_anchor_model[0] + c1 * v_side1_model[0], p_anchor_model[1] + c1 * v_side1_model[1])
    new_p_adj2_model = (p_anchor_model[0] + c2 * v_side2_model[0], p_anchor_model[1] + c2 * v_side2_model[1])
    new_p_drag_model = (new_p_adj1_model[0] + (new_p_adj2_model[0] - p_anchor_model[0]), new_p_adj1_model[1] + (new_p_adj2_model[1] - p_anchor_model[1]))

    # 6. Assemble final list
    new_vertices = [ (0,0) ] * 4
    new_vertices[p_anchor_idx] = p_anchor_model
    new_vertices[p_adj1_idx] = new_p_adj1_model
    new_vertices[p_adj2_idx] = new_p_adj2_model
    new_vertices[p_drag_idx] = new_p_drag_model
    
    return new_vertices

def calculate_new_edge_on_drag(
    original_vertices: List[Tuple[float, float]],
    dragged_edge_index: int,
    new_mouse_position: Tuple[float, float],
    angle: float = 0,
    center: Optional[Tuple[float, float]] = None
) -> List[Tuple[float, float]]:
    """当边缘被拖拽时，计算新的顶点位置 (沿法线移动)"""
    
    rotation_center = center if center else get_polygon_center(original_vertices)

    # 1. Get edge vertices in model space
    v1_model_idx = dragged_edge_index
    v2_model_idx = (v1_model_idx + 1) % len(original_vertices)
    v1_model = original_vertices[v1_model_idx]
    v2_model = original_vertices[v2_model_idx]

    # 2. Rotate them to world space to find the visual edge and its normal
    v1_world = rotate_point(v1_model[0], v1_model[1], angle, rotation_center[0], rotation_center[1])
    v2_world = rotate_point(v2_model[0], v2_model[1], angle, rotation_center[0], rotation_center[1])

    # 3. Calculate the edge normal in world space
    edge_vector_world_x = v2_world[0] - v1_world[0]
    edge_vector_world_y = v2_world[1] - v1_world[1]
    normal_vector_world_x = -edge_vector_world_y
    normal_vector_world_y = edge_vector_world_x
    
    norm_len = math.hypot(normal_vector_world_x, normal_vector_world_y)
    if norm_len == 0: return original_vertices

    unit_normal_world_x = normal_vector_world_x / norm_len
    unit_normal_world_y = normal_vector_world_y / norm_len

    # 4. Project the mouse drag vector onto the world-space normal
    mouse_drag_vector_world_x = new_mouse_position[0] - v1_world[0]
    mouse_drag_vector_world_y = new_mouse_position[1] - v1_world[1]
    
    projection_length = mouse_drag_vector_world_x * unit_normal_world_x + mouse_drag_vector_world_y * unit_normal_world_y
    
    # 5. Calculate the offset vector in world space
    offset_world_x = projection_length * unit_normal_world_x
    offset_world_y = projection_length * unit_normal_world_y

    # 6. Un-rotate the world-space offset vector back to model space
    offset_model_x, offset_model_y = rotate_point(offset_world_x, offset_world_y, -angle, 0, 0)

    # Correction: For near-rectangular shapes, ensure offset is only along one axis in model space
    # to prevent drift when dragging edges.
    model_edge_dx = abs(v2_model[0] - v1_model[0])
    model_edge_dy = abs(v2_model[1] - v1_model[1])
    if model_edge_dx > model_edge_dy: # Horizontal edge
        offset_model_x = 0
    else: # Vertical edge
        offset_model_y = 0

    # 7. Apply the model-space offset to the two vertices of the edge
    new_vertices = list(original_vertices)
    
    new_v1_model = (v1_model[0] + offset_model_x, v1_model[1] + offset_model_y)
    new_v2_model = (v2_model[0] + offset_model_x, v2_model[1] + offset_model_y)

    new_vertices[v1_model_idx] = new_v1_model
    new_vertices[v2_model_idx] = new_v2_model
        
    return new_vertices
