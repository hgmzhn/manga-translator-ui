import itertools
from collections import Counter
from typing import List, Optional, Set, Tuple

import networkx as nx
import numpy as np
from shapely.geometry import Polygon

from ..utils import Quadrilateral, TextBlock, quadrilateral_can_merge_region

GROUP_BUBBLE_LABELS = {'balloon', 'qipao', 'other'}
GROUP_STRIP_LABELS = {'changfangtiao'}
WRAP_ONLY_LABELS = {'other'}
MODEL_ASSISTED_MIN_FONT_SIZE_RATIO = 0.5

def _get_det_label(txtln: Quadrilateral) -> Optional[str]:
    label = getattr(txtln, 'det_label', None)
    if label is None:
        label = getattr(txtln, 'yolo_label', None)
    if isinstance(label, str):
        label = label.strip().lower()
        if label:
            return label
    return None

def _build_text_block_from_txtlns(txtlns: List[Quadrilateral], fg_color: Tuple[int, int, int], bg_color: Tuple[int, int, int], config) -> Optional[TextBlock]:
    unique_txtlns = []
    seen_coords = set()
    for txtln in txtlns:
        coords_tuple = tuple(txtln.pts.reshape(-1))
        if coords_tuple not in seen_coords:
            seen_coords.add(coords_tuple)
            unique_txtlns.append(txtln)

    if not unique_txtlns:
        return None

    total_area = sum([txtln.area for txtln in unique_txtlns])
    if total_area <= 0:
        return None

    total_logprobs = 0
    for txtln in unique_txtlns:
        total_logprobs += np.log(txtln.prob) * txtln.area
    total_logprobs /= total_area

    font_size = int(min([txtln.font_size for txtln in unique_txtlns]))
    angle = np.rad2deg(np.mean([txtln.angle for txtln in unique_txtlns])) - 90
    original_angles_deg = [np.rad2deg(txtln.angle) for txtln in unique_txtlns]
    has_near_90_degree = any(abs(orig_angle - 90.0) <= 1.0 for orig_angle in original_angles_deg)
    if has_near_90_degree or abs(angle) < 3:
        angle = 0

    lines = [txtln.pts for txtln in unique_txtlns]
    texts = [txtln.text for txtln in unique_txtlns]
    stroke_width = 0.07
    line_spacing = 1.0
    letter_spacing = 1.0
    if config and hasattr(config, 'render'):
        render_cfg = config.render
        stroke_width_val = getattr(render_cfg, 'stroke_width', None)
        if stroke_width_val is not None:
            stroke_width = stroke_width_val
        line_spacing_val = getattr(render_cfg, 'line_spacing', None)
        if line_spacing_val is not None:
            line_spacing = float(line_spacing_val)
        letter_spacing_val = getattr(render_cfg, 'letter_spacing', None)
        if letter_spacing_val is not None:
            letter_spacing = float(letter_spacing_val)

    return TextBlock(
        lines,
        texts,
        font_size=font_size,
        angle=angle,
        prob=np.exp(total_logprobs),
        fg_color=fg_color,
        bg_color=bg_color,
        line_spacing=line_spacing,
        letter_spacing=letter_spacing,
        default_stroke_width=stroke_width
    )

def _filter_model_assisted_small_font_payload(txtlns: List[Quadrilateral]) -> List[Quadrilateral]:
    if len(txtlns) <= 1:
        return txtlns

    font_sizes = [float(txtln.font_size) for txtln in txtlns]
    max_font_size = max(font_sizes, default=0.0)
    if max_font_size <= 0:
        return txtlns

    min_font_size = max_font_size * MODEL_ASSISTED_MIN_FONT_SIZE_RATIO
    # Keep the special pre-merge no more permissive than the normal 2x font-size tolerance.
    return [
        txtln
        for txtln, font_size in zip(txtlns, font_sizes)
        if font_size >= min_font_size
    ]

def _is_fully_wrapped(inner: Quadrilateral, outer: Quadrilateral, eps: float = 1.0) -> bool:
    inner_x1, inner_y1 = np.min(inner.pts[:, 0]), np.min(inner.pts[:, 1])
    inner_x2, inner_y2 = np.max(inner.pts[:, 0]), np.max(inner.pts[:, 1])
    outer_x1, outer_y1 = np.min(outer.pts[:, 0]), np.min(outer.pts[:, 1])
    outer_x2, outer_y2 = np.max(outer.pts[:, 0]), np.max(outer.pts[:, 1])
    return (
        inner_x1 >= outer_x1 - eps and
        inner_y1 >= outer_y1 - eps and
        inner_x2 <= outer_x2 + eps and
        inner_y2 <= outer_y2 + eps
    )

def _wrapped_cover_ratio(inner: Quadrilateral, outer: Quadrilateral, eps: float = 1.0) -> float:
    inner_x1, inner_y1 = np.min(inner.pts[:, 0]), np.min(inner.pts[:, 1])
    inner_x2, inner_y2 = np.max(inner.pts[:, 0]), np.max(inner.pts[:, 1])
    outer_x1, outer_y1 = np.min(outer.pts[:, 0]), np.min(outer.pts[:, 1])
    outer_x2, outer_y2 = np.max(outer.pts[:, 0]), np.max(outer.pts[:, 1])

    inner_w = max(0.0, inner_x2 - inner_x1)
    inner_h = max(0.0, inner_y2 - inner_y1)
    inner_area = inner_w * inner_h
    if inner_area <= 0:
        return 0.0

    inter_x1 = max(inner_x1, outer_x1 - eps)
    inter_y1 = max(inner_y1, outer_y1 - eps)
    inter_x2 = min(inner_x2, outer_x2 + eps)
    inter_y2 = min(inner_y2, outer_y2 + eps)
    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    return (inter_w * inter_h) / inner_area

def _is_special_wrap_related(
    inner: Quadrilateral,
    outer: Quadrilateral,
    outer_label: Optional[str],
    eps: float = 1.0,
    other_cover_threshold: float = 0.9,
) -> bool:
    if _is_fully_wrapped(inner, outer, eps):
        return True
    if outer_label in WRAP_ONLY_LABELS:
        return _wrapped_cover_ratio(inner, outer, eps) > other_cover_threshold
    return False

def _aabb_bounds(txtln: Quadrilateral) -> Tuple[float, float, float, float]:
    x1, y1 = np.min(txtln.pts[:, 0]), np.min(txtln.pts[:, 1])
    x2, y2 = np.max(txtln.pts[:, 0]), np.max(txtln.pts[:, 1])
    return float(x1), float(y1), float(x2), float(y2)

def _aabb_area(txtln: Quadrilateral) -> float:
    x1, y1, x2, y2 = _aabb_bounds(txtln)
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)

def _required_uniform_shrink_scale(inner: Quadrilateral, outer: Quadrilateral, eps: float = 1.0) -> float:
    """
    返回 outer 围绕中心等比例缩小时，仍能包裹 inner 所需的最小 scale（越小越“抗缩”）。
    若 outer 退化（宽/高为 0），返回 inf。
    """
    inner_x1, inner_y1, inner_x2, inner_y2 = _aabb_bounds(inner)
    outer_x1, outer_y1, outer_x2, outer_y2 = _aabb_bounds(outer)

    cx = (outer_x1 + outer_x2) / 2.0
    cy = (outer_y1 + outer_y2) / 2.0
    half_w = (outer_x2 - outer_x1) / 2.0
    half_h = (outer_y2 - outer_y1) / 2.0
    if half_w <= 0 or half_h <= 0:
        return float('inf')

    need_left = (cx - inner_x1 - eps) / half_w
    need_right = (inner_x2 - cx - eps) / half_w
    need_top = (cy - inner_y1 - eps) / half_h
    need_bottom = (inner_y2 - cy - eps) / half_h

    return max(0.0, need_left, need_right, need_top, need_bottom)

def _group_by_full_wrap(candidates: List[Quadrilateral], wrap_eps: float = 1.0) -> List[Set[int]]:
    """
    使用“完全包裹”关系分组：只有存在 A 包裹 B 或 B 包裹 A 才连接。
    对同一个非 other 框，如果被多个 other 包裹，只保留一个 other 连接：
    以“outer 等比例缩小时仍可包裹 inner 的最小所需 scale”作为主判据（越小越优）。
    这样可避免 one-to-many 的 other 占用把多个 other 粘成同一连通组。
    其中 other 作为包裹辅助框时，允许对 inner 的 AABB 覆盖率 > 90%。
    """
    node_count = len(candidates)
    labels = [_get_det_label(txtln) for txtln in candidates]
    edge_set = set()
    for i, j in itertools.combinations(range(len(candidates)), 2):
        if labels[i] in WRAP_ONLY_LABELS and labels[j] in WRAP_ONLY_LABELS:
            # 包裹辅助框之间不互相连边，避免 helper 之间形成无意义连通桥。
            continue
        if (
            _is_special_wrap_related(candidates[i], candidates[j], labels[j], wrap_eps)
            or _is_special_wrap_related(candidates[j], candidates[i], labels[i], wrap_eps)
        ):
            edge_set.add((i, j))

    # 同一 inner 被多个 other 包裹时，裁剪为“单一 other 归属”
    for inner_idx, inner_txtln in enumerate(candidates):
        if labels[inner_idx] in WRAP_ONLY_LABELS:
            continue

        wrapped_by_other = []
        for other_idx, other_txtln in enumerate(candidates):
            if inner_idx == other_idx:
                continue
            if labels[other_idx] not in WRAP_ONLY_LABELS:
                continue
            if not _is_special_wrap_related(inner_txtln, other_txtln, labels[other_idx], wrap_eps):
                continue

            min_scale = _required_uniform_shrink_scale(inner_txtln, other_txtln, eps=wrap_eps)
            other_area = _aabb_area(other_txtln)
            wrapped_by_other.append((min_scale, other_area, other_idx))

        if len(wrapped_by_other) <= 1:
            continue

        wrapped_by_other.sort(key=lambda x: (x[0], x[1], x[2]))
        for _, _, other_idx in wrapped_by_other[1:]:
            edge = (min(inner_idx, other_idx), max(inner_idx, other_idx))
            edge_set.discard(edge)

    G = nx.Graph()
    G.add_nodes_from(range(node_count))
    G.add_edges_from(edge_set)
    return list(nx.algorithms.components.connected_components(G))

def _sort_group_textlines(txtlns: List[Quadrilateral]) -> List[Quadrilateral]:
    """
    对特殊预合并分组内文本线做稳定排序，避免 set 导致的随机顺序。
    规则与原合并流程保持一致：横排按 y 从上到下，竖排按 x 从右到左。
    """
    if len(txtlns) <= 1:
        return txtlns

    dirs = [box.direction for box in txtlns]
    majority_dir_top_2 = Counter(dirs).most_common(2)
    if len(majority_dir_top_2) == 1:
        majority_dir = majority_dir_top_2[0][0]
    elif majority_dir_top_2[0][1] == majority_dir_top_2[1][1]:
        max_aspect_ratio = -100
        majority_dir = majority_dir_top_2[0][0]
        for box in txtlns:
            if box.aspect_ratio > max_aspect_ratio:
                max_aspect_ratio = box.aspect_ratio
                majority_dir = box.direction
            inv = 1.0 / box.aspect_ratio if box.aspect_ratio != 0 else float('inf')
            if inv > max_aspect_ratio:
                max_aspect_ratio = inv
                majority_dir = box.direction
    else:
        majority_dir = majority_dir_top_2[0][0]
    if majority_dir == 'h':
        return sorted(txtlns, key=lambda x: x.centroid[1])
    if majority_dir == 'v':
        return sorted(txtlns, key=lambda x: -x.centroid[0])
    return sorted(txtlns, key=lambda x: (x.centroid[1], x.centroid[0]))

def split_text_region(
        bboxes: List[Quadrilateral],
        connected_region_indices: Set[int],
        width,
        height,
        gamma = 0.5,
        sigma = 2,
        debug = False
    ) -> List[Set[int]]:

    connected_region_indices = list(connected_region_indices)

    # case 1
    if len(connected_region_indices) == 1:
        return [set(connected_region_indices)]

    # case 2
    if len(connected_region_indices) == 2:
        fs1 = bboxes[connected_region_indices[0]].font_size
        fs2 = bboxes[connected_region_indices[1]].font_size
        fs = max(fs1, fs2)

        dist = bboxes[connected_region_indices[0]].distance(bboxes[connected_region_indices[1]])
        angle_diff = abs(bboxes[connected_region_indices[0]].angle - bboxes[connected_region_indices[1]].angle)

        if dist < (1 + gamma) * fs and angle_diff < 0.2 * np.pi:
            return [set(connected_region_indices)]
        else:
            return [set([connected_region_indices[0]]), set([connected_region_indices[1]])]

    # case 3
    G = nx.Graph()
    for idx in connected_region_indices:
        G.add_node(idx)
    for (u, v) in itertools.combinations(connected_region_indices, 2):
        dist = bboxes[u].distance(bboxes[v])
        G.add_edge(u, v, weight=dist)

    # Get distances from neighbouring bboxes
    edges = nx.algorithms.tree.minimum_spanning_edges(G, algorithm='kruskal', data=True)
    edges = sorted(edges, key=lambda a: a[2]['weight'], reverse=True)
    distances_sorted = [a[2]['weight'] for a in edges]
    fontsize = np.mean([bboxes[idx].font_size for idx in connected_region_indices])
    distances_std = np.std(distances_sorted)
    distances_mean = np.mean(distances_sorted)
    std_threshold = max(0.3 * fontsize + 5, 5)

    b1, b2 = bboxes[edges[0][0]], bboxes[edges[0][1]]
    max_poly_distance = Polygon(b1.pts).distance(Polygon(b2.pts))
    max_centroid_alignment = min(abs(b1.centroid[0] - b2.centroid[0]), abs(b1.centroid[1] - b2.centroid[1]))

    if (distances_sorted[0] <= distances_mean + distances_std * sigma \
            or distances_sorted[0] <= fontsize * (1 + gamma)) \
            and (distances_std < std_threshold \
            or max_poly_distance == 0 and max_centroid_alignment < 5):
        return [set(connected_region_indices)]
    else:
        # (split_u, split_v, _) = edges[0]
        # print(f'split between "{bboxes[split_u].pts}", "{bboxes[split_v].pts}"')
        G = nx.Graph()
        for idx in connected_region_indices:
            G.add_node(idx)
        # Split out the most deviating bbox
        for edge in edges[1:]:
            G.add_edge(edge[0], edge[1])
        ans = []
        for node_set in nx.algorithms.components.connected_components(G):
            ans.extend(split_text_region(bboxes, node_set, width, height, gamma, sigma, debug))
        return ans

# def get_mini_boxes(contour):
#     bounding_box = cv2.minAreaRect(contour)
#     points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda x: x[0])

#     index_1, index_2, index_3, index_4 = 0, 1, 2, 3
#     if points[1][1] > points[0][1]:
#         index_1 = 0
#         index_4 = 1
#     else:
#         index_1 = 1
#         index_4 = 0
#     if points[3][1] > points[2][1]:
#         index_2 = 2
#         index_3 = 3
#     else:
#         index_2 = 3
#         index_3 = 2

#     box = [points[index_1], points[index_2], points[index_3], points[index_4]]
#     box = np.array(box)
#     startidx = box.sum(axis=1).argmin()
#     box = np.roll(box, 4 - startidx, 0)
#     box = np.array(box)
#     return box

def merge_bboxes_text_region(bboxes: List[Quadrilateral], width, height, debug=False, edge_ratio_threshold=0.0, config=None):
    # step 0: merge quadrilaterals that belong to the same textline
    # u = 0
    # removed_counter = 0
    # while u < len(bboxes) - 1 - removed_counter:
    #     v = u
    #     while v < len(bboxes) - removed_counter:
    #         if quadrilateral_can_merge_region(bboxes[u], bboxes[v], aspect_ratio_tol=1.1, font_size_ratio_tol=1,
    #                                         char_gap_tolerance=1, char_gap_tolerance2=3, discard_connection_gap=0) \
    #            and abs(bboxes[u].centroid[0] - bboxes[v].centroid[0]) < 5 or abs(bboxes[u].centroid[1] - bboxes[v].centroid[1]) < 5:
    #                 bboxes[u] = merge_quadrilaterals(bboxes[u], bboxes[v])
    #                 removed_counter += 1
    #                 bboxes.pop(v)
    #         else:
    #             v += 1
    #     u += 1

    # step 1: divide into multiple text region candidates
    G = nx.Graph()
    for i, box in enumerate(bboxes):
        G.add_node(i, box=box)

    # 记录边缘距离
    edge_distances = {}
    edge_count = 0
    for ((u, ubox), (v, vbox)) in itertools.combinations(enumerate(bboxes), 2):
        # if quadrilateral_can_merge_region_coarse(ubox, vbox):
        can_merge = quadrilateral_can_merge_region(ubox, vbox, aspect_ratio_tol=1.3, font_size_ratio_tol=2,
                                          char_gap_tolerance=1, char_gap_tolerance2=3, debug=debug)
        if can_merge:
            # 计算边缘距离
            poly_dist = ubox.poly_distance(vbox)
            G.add_edge(u, v, distance=poly_dist)
            edge_distances[(u, v)] = poly_dist
            edge_count += 1

    # step 1.5: 边缘距离比例检测 - 断开距离差异过大的连接
    if edge_ratio_threshold > 0 and len(bboxes) > 2:
        edges_to_remove = []
        for node in G.nodes():
            neighbors = list(G.neighbors(node))
            if len(neighbors) >= 2:
                # 获取该节点到所有邻居的距离
                neighbor_distances = []
                for neighbor in neighbors:
                    edge = (min(node, neighbor), max(node, neighbor))
                    dist = edge_distances.get(edge, 0)
                    neighbor_distances.append((neighbor, dist))

                # 按距离排序
                neighbor_distances.sort(key=lambda x: x[1])

                # 检查最小距离和其他距离的比例
                min_dist = neighbor_distances[0][1]
                if min_dist > 0:  # 避免除以0
                    for neighbor, dist in neighbor_distances[1:]:
                        ratio = dist / min_dist
                        if ratio > edge_ratio_threshold:
                            edge_to_remove = (min(node, neighbor), max(node, neighbor))
                            if edge_to_remove not in edges_to_remove:
                                edges_to_remove.append(edge_to_remove)

        # 移除边
        for edge in edges_to_remove:
            if G.has_edge(edge[0], edge[1]):
                G.remove_edge(edge[0], edge[1])

    # step 2: postprocess - further split each region
    region_indices: List[Set[int]] = []
    connected_components = list(nx.algorithms.components.connected_components(G))

    for node_set in connected_components:
         split_result = split_text_region(bboxes, node_set, width, height, debug=debug)
         region_indices.extend(split_result)

    # step 3: return regions
    for node_set in region_indices:
    # for node_set in nx.algorithms.components.connected_components(G):
        nodes = list(node_set)
        txtlns: List[Quadrilateral] = np.array(bboxes)[nodes]

        # calculate average fg and bg color
        fg_r = round(np.mean([box.fg_r for box in txtlns]))
        fg_g = round(np.mean([box.fg_g for box in txtlns]))
        fg_b = round(np.mean([box.fg_b for box in txtlns]))
        bg_r = round(np.mean([box.bg_r for box in txtlns]))
        bg_g = round(np.mean([box.bg_g for box in txtlns]))
        bg_b = round(np.mean([box.bg_b for box in txtlns]))

        # majority vote for direction
        dirs = [box.direction for box in txtlns]
        majority_dir_top_2 = Counter(dirs).most_common(2)
        if len(majority_dir_top_2) == 1 :
            majority_dir = majority_dir_top_2[0][0]
        elif majority_dir_top_2[0][1] == majority_dir_top_2[1][1] : # if top 2 have the same counts
            max_aspect_ratio = -100
            for box in txtlns :
                if box.aspect_ratio > max_aspect_ratio :
                    max_aspect_ratio = box.aspect_ratio
                    majority_dir = box.direction
                if 1.0 / box.aspect_ratio > max_aspect_ratio :
                    max_aspect_ratio = 1.0 / box.aspect_ratio
                    majority_dir = box.direction
        else :
            majority_dir = majority_dir_top_2[0][0]

        # sort textlines
        if majority_dir == 'h':
            nodes = sorted(nodes, key=lambda x: bboxes[x].centroid[1])
        elif majority_dir == 'v':
            nodes = sorted(nodes, key=lambda x: -bboxes[x].centroid[0])
        txtlns = np.array(bboxes)[nodes]

        # yield overall bbox and sorted indices
        yield txtlns, (fg_r, fg_g, fg_b), (bg_r, bg_g, bg_b)

async def dispatch(
    textlines: List[Quadrilateral],
    width: int,
    height: int,
    config,
    verbose: bool = False,
    model_assisted_other_textlines: Optional[List[Quadrilateral]] = None
) -> List[TextBlock]:
    # 启用调试模式 (临时)
    debug = verbose
    # 获取边缘距离比例阈值
    edge_ratio_threshold = getattr(config.ocr, 'merge_edge_ratio_threshold', 0.0)
    enable_model_assisted_merge = bool(getattr(config.ocr, 'merge_special_require_full_wrap', True))
    text_regions: List[TextBlock] = []
    main_textlines = list(textlines)
    auxiliary_other_textlines = list(model_assisted_other_textlines or [])

    # 先做标签预合并（已合并的框不再参与后续原始合并）
    id_to_idx = {id(txtln): i for i, txtln in enumerate(main_textlines)}
    consumed_indices = set()

    def _run_special_stage(target_labels: Set[str]) -> None:
        candidate_indices = []
        has_target_label = False

        for i, txtln in enumerate(main_textlines):
            if i in consumed_indices:
                continue
            det_label = _get_det_label(txtln)
            if det_label in target_labels:
                has_target_label = True
                candidate_indices.append(i)
            elif det_label == 'other':
                # other 作为“完全包裹关系”的桥接框参与特殊分组
                candidate_indices.append(i)
            elif det_label is None:
                # 无标签与目标标签同级参与
                candidate_indices.append(i)

        if not has_target_label or not candidate_indices:
            return

        candidates = [main_textlines[i] for i in candidate_indices]
        # other 仅作为辅助包裹关系输入，不进入主输入序列
        if auxiliary_other_textlines:
            candidates.extend(auxiliary_other_textlines)
        candidate_groups = _group_by_full_wrap(candidates, wrap_eps=1.0)
        for node_set in candidate_groups:
            # 特殊预合并严格要求完全包裹关系，单框不算“合并”
            if len(node_set) < 2:
                continue

            group_txtlns = [candidates[i] for i in node_set]
            group_txtlns = _sort_group_textlines(group_txtlns)
            labels_in_region = set()
            for txtln in group_txtlns:
                lbl = _get_det_label(txtln)
                if lbl is not None:
                    labels_in_region.add(lbl)

            # 至少包含一个目标标签框才作为该组产物
            if not (labels_in_region & target_labels):
                continue

            # other 仅用于包裹关系辅助，不参与实际文本块几何合并
            payload_txtlns = []
            for txtln in group_txtlns:
                lbl = _get_det_label(txtln)
                if lbl in WRAP_ONLY_LABELS:
                    continue
                payload_txtlns.append(txtln)
            payload_txtlns = _sort_group_textlines(payload_txtlns)
            payload_txtlns = _filter_model_assisted_small_font_payload(payload_txtlns)
            if not payload_txtlns:
                # 仅有包裹辅助框时，不产出文本块；但会在 consumed 中移除，避免后续误合并
                for txtln in group_txtlns:
                    idx = id_to_idx.get(id(txtln))
                    if idx is not None and _get_det_label(txtln) in WRAP_ONLY_LABELS:
                        consumed_indices.add(idx)
                continue

            fg_r = round(np.mean([box.fg_r for box in payload_txtlns]))
            fg_g = round(np.mean([box.fg_g for box in payload_txtlns]))
            fg_b = round(np.mean([box.fg_b for box in payload_txtlns]))
            bg_r = round(np.mean([box.bg_r for box in payload_txtlns]))
            bg_g = round(np.mean([box.bg_g for box in payload_txtlns]))
            bg_b = round(np.mean([box.bg_b for box in payload_txtlns]))

            region = _build_text_block_from_txtlns(
                payload_txtlns,
                (fg_r, fg_g, fg_b),
                (bg_r, bg_g, bg_b),
                config
            )
            if region is not None:
                text_regions.append(region)
                payload_ids = {id(txtln) for txtln in payload_txtlns}
                for txtln in group_txtlns:
                    idx = id_to_idx.get(id(txtln))
                    if idx is not None and (
                        id(txtln) in payload_ids or _get_det_label(txtln) in WRAP_ONLY_LABELS
                    ):
                        consumed_indices.add(idx)

    if enable_model_assisted_merge:
        # changfangtiao 独立组优先
        _run_special_stage(GROUP_STRIP_LABELS)
        # balloon/qipao/other 组合并组
        _run_special_stage(GROUP_BUBBLE_LABELS)

    # 剩余框（或禁用模型辅助时的全部框）走原始合并算法
    remaining_textlines = []
    for i, txtln in enumerate(main_textlines):
        if i in consumed_indices:
            continue
        lbl = _get_det_label(txtln)
        if lbl in WRAP_ONLY_LABELS:
            continue
        remaining_textlines.append(txtln)
    for txtlns, fg_color, bg_color in merge_bboxes_text_region(
        remaining_textlines,
        width,
        height,
        debug=debug,
        edge_ratio_threshold=edge_ratio_threshold,
        config=config
    ):
        region = _build_text_block_from_txtlns(list(txtlns), fg_color, bg_color, config)
        if region is not None:
            text_regions.append(region)

    return text_regions
