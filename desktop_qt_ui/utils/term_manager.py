#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
专有名词管理器
功能：按作品管理专有名词（人名、地名、特殊术语），集成到高质量翻译提示词
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from .name_replacer import NameReplacer


class TermManager:
    """专有名词管理器 - 按作品分类管理"""
    
    # 专有名词类别
    CATEGORY_CHARACTER = "character_names"  # 角色名
    CATEGORY_PLACE = "place_names"          # 地名
    CATEGORY_TERM = "special_terms"         # 特殊术语/技能
    
    ALL_CATEGORIES = [CATEGORY_CHARACTER, CATEGORY_PLACE, CATEGORY_TERM]
    CATEGORY_DISPLAY_NAMES = {
        CATEGORY_CHARACTER: "角色名",
        CATEGORY_PLACE: "地名",
        CATEGORY_TERM: "特殊术语"
    }
    
    def __init__(self, terms_file: str = "dict/terms_by_work.json", name_replacer: Optional[NameReplacer] = None):
        """
        初始化专有名词管理器
        
        Args:
            terms_file: 专有名词数据文件路径（相对于项目根目录）
            name_replacer: 名称替换器实例（用于识别作品名）
        """
        self.terms_file = Path(terms_file)
        self.name_replacer = name_replacer or NameReplacer()
        self.terms_data: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._load_terms()
    
    def _load_terms(self):
        """从文件加载专有名词数据"""
        if self.terms_file.exists():
            try:
                with open(self.terms_file, 'r', encoding='utf-8') as f:
                    self.terms_data = json.load(f)
            except Exception as e:
                print(f"加载专有名词文件失败: {e}")
                self.terms_data = {}
        else:
            # 创建默认结构
            self.terms_data = {}
            self._save_terms()
    
    def _save_terms(self):
        """保存专有名词数据到文件"""
        try:
            # 确保目录存在
            self.terms_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.terms_file, 'w', encoding='utf-8') as f:
                json.dump(self.terms_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存专有名词文件失败: {e}")
    
    def reload(self):
        """重新加载数据文件"""
        self._load_terms()
    
    def get_work_name_from_path(self, file_path: str) -> Optional[str]:
        """
        从文件路径提取作品名（熟肉名）
        
        流程：
        1. 查找 _source_path.txt 文件
        2. 读取原始路径，提取生肉名（倒数第二级目录）
        3. 通过 NameReplacer 映射到熟肉名
        
        Args:
            file_path: 当前处理的文件路径
            
        Returns:
            作品熟肉名，如果无法识别则返回 None
        """
        try:
            if not file_path:
                return None
            
            current_path = Path(file_path).resolve()
            print(f"[TermManager] 尝试识别作品，文件路径: {current_path}")
            
            # 向上查找 _source_path.txt（最多向上5级）
            search_paths = [current_path.parent] + list(current_path.parents)[:5]
            
            for parent in search_paths:
                source_path_file = parent / "_source_path.txt"
                print(f"[TermManager] 检查: {source_path_file}")
                
                if source_path_file.exists():
                    with open(source_path_file, 'r', encoding='utf-8') as f:
                        source_path = f.read().strip()
                    
                    print(f"[TermManager] 找到 _source_path.txt: {source_path}")
                    
                    # 提取作品名：倒数第二级目录就是作品名
                    path_parts = Path(source_path).parts
                    print(f"[TermManager] 路径层级: {path_parts}")
                    
                    if len(path_parts) >= 2:
                        # 标准格式：.../作品名/Chapter 20/
                        raw_work_name = path_parts[-2]
                        print(f"[TermManager] 提取生肉名（倒数第二级）: {raw_work_name}")
                        
                        # 过滤明显不是作品名的目录（如"未翻译"、"原始"等）
                        invalid_names = ['未翻译', '原始', 'raw', 'source', 'input', 'output', '输入', '输出']
                        if raw_work_name.lower() in [n.lower() for n in invalid_names]:
                            print(f"[TermManager] ⚠️ 路径不完整！倒数第二级是'{raw_work_name}'，不是作品名")
                            print(f"[TermManager] ℹ️ 请在 _source_path.txt 中写入完整原始路径")
                            print(f"[TermManager] ℹ️ 格式示例: E:\\下载\\Solo Leveling\\Chapter 20")
                            # 回退：使用倒数第一级作为临时作品名
                            raw_work_name = path_parts[-1]
                            print(f"[TermManager] 临时使用章节名作为作品名: {raw_work_name}")
                        
                        # 通过 NameReplacer 映射到熟肉名
                        translated_name = self.name_replacer.get_translated_name(raw_work_name)
                        if translated_name != raw_work_name:
                            print(f"[TermManager] ✓ 映射到熟肉名: {translated_name}")
                        else:
                            print(f"[TermManager] ℹ️ 未找到映射，使用原名: {raw_work_name}")
                        
                        return translated_name
                    
                    print(f"[TermManager] ❌ 路径层级不足（至少需要2级）: {path_parts}")
            
            print(f"[TermManager] 未找到 _source_path.txt")
            return None
        except Exception as e:
            print(f"[TermManager] 提取作品名失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def add_term(self, work_name: str, category: str, original: str, translation: str):
        """
        添加专有名词
        
        Args:
            work_name: 作品名（熟肉名）
            category: 类别（character_names/place_names/special_terms）
            original: 原文
            translation: 译文
        """
        if category not in self.ALL_CATEGORIES:
            raise ValueError(f"Invalid category: {category}")
        
        # 确保作品存在
        if work_name not in self.terms_data:
            self.terms_data[work_name] = {cat: {} for cat in self.ALL_CATEGORIES}
        
        # 添加术语
        self.terms_data[work_name][category][original] = translation
        self._save_terms()
    
    def get_terms(self, work_name: str, category: Optional[str] = None) -> Dict[str, str]:
        """
        获取作品的专有名词
        
        Args:
            work_name: 作品名
            category: 类别，None 表示获取所有类别
            
        Returns:
            专有名词字典 {原文: 译文}
        """
        if work_name not in self.terms_data:
            return {}
        
        if category:
            return self.terms_data[work_name].get(category, {}).copy()
        else:
            # 合并所有类别
            all_terms = {}
            for cat in self.ALL_CATEGORIES:
                all_terms.update(self.terms_data[work_name].get(cat, {}))
            return all_terms
    
    def remove_term(self, work_name: str, category: str, original: str) -> bool:
        """
        删除专有名词
        
        Returns:
            是否成功删除
        """
        if work_name in self.terms_data and category in self.terms_data[work_name]:
            if original in self.terms_data[work_name][category]:
                del self.terms_data[work_name][category][original]
                self._save_terms()
                return True
        return False
    
    def remove_work(self, work_name: str) -> bool:
        """
        删除整个作品的所有专有名词
        
        Returns:
            是否成功删除
        """
        if work_name in self.terms_data:
            del self.terms_data[work_name]
            self._save_terms()
            return True
        return False
    
    def get_all_works(self) -> List[str]:
        """获取所有作品名列表"""
        return sorted(list(self.terms_data.keys()))
    
    def get_work_term_count(self, work_name: str) -> int:
        """获取作品的专有名词总数"""
        if work_name not in self.terms_data:
            return 0
        count = 0
        for category in self.ALL_CATEGORIES:
            count += len(self.terms_data[work_name].get(category, {}))
        return count
    
    def build_prompt_json(self, work_name: str) -> Dict:
        """
        构建用于高质量翻译的提示词 JSON
        格式与 prompt_example.json 兼容
        
        Args:
            work_name: 作品名
            
        Returns:
            提示词 JSON 字典
        """
        if work_name not in self.terms_data:
            return {}
        
        prompt_data = {
            "terminology_guide": {
                "work_name": work_name,
                "note": "Please maintain consistency with the following terminology translations throughout this work:"
            }
        }
        
        work_terms = self.terms_data[work_name]
        
        # 角色名
        if work_terms.get(self.CATEGORY_CHARACTER):
            prompt_data["terminology_guide"]["character_names"] = work_terms[self.CATEGORY_CHARACTER]
        
        # 地名
        if work_terms.get(self.CATEGORY_PLACE):
            prompt_data["terminology_guide"]["place_names"] = work_terms[self.CATEGORY_PLACE]
        
        # 特殊术语
        if work_terms.get(self.CATEGORY_TERM):
            prompt_data["terminology_guide"]["special_terms"] = work_terms[self.CATEGORY_TERM]
        
        return prompt_data
    
    def learn_from_translations(self, work_name: str, original_texts: List[str], translations: List[str], 
                                min_length: int = 2, max_length: int = 8) -> List[Tuple[str, str, str]]:
        """
        从翻译结果中自动学习专有名词
        通过对比原文和译文，提取疑似的专有名词
        
        Args:
            work_name: 作品名
            original_texts: 原文列表
            translations: 译文列表
            min_length: 最小词长
            max_length: 最大词长
            
        Returns:
            学习到的术语列表 [(原文, 译文, 建议类别), ...]
        """
        learned_terms = []
        existing_terms = self.get_terms(work_name)
        
        # 提取原文中的疑似专有名词（片假名、连续大写字母）
        for orig, trans in zip(original_texts, translations):
            # 提取片假名词（通常是外来语/人名）
            katakana_pattern = r'[\u30A0-\u30FF]{' + str(min_length) + ',' + str(max_length) + '}'
            katakana_matches = re.findall(katakana_pattern, orig)
            
            # 提取连续大写字母（英文人名/地名）
            english_pattern = r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b'
            english_matches = re.findall(english_pattern, orig)
            
            candidates = set(katakana_matches + english_matches)
            
            for candidate in candidates:
                # 跳过已存在的术语
                if candidate in existing_terms:
                    continue
                
                # 在译文中查找对应翻译（简单匹配）
                # 这里可以使用更复杂的对齐算法
                if candidate in orig and orig in trans:
                    # 尝试定位译文中的对应位置
                    # 简化版本：假设顺序一致
                    learned_terms.append((candidate, candidate, self.CATEGORY_CHARACTER))
        
        return learned_terms
    
    def batch_add_terms(self, work_name: str, terms: List[Tuple[str, str, str]]):
        """
        批量添加专有名词
        
        Args:
            work_name: 作品名
            terms: 术语列表 [(原文, 译文, 类别), ...]
        """
        for original, translation, category in terms:
            try:
                self.add_term(work_name, category, original, translation)
            except Exception as e:
                print(f"添加术语失败 {original}: {e}")
    
    def export_work_terms(self, work_name: str, export_file: str):
        """
        导出指定作品的专有名词到文件
        
        Args:
            work_name: 作品名
            export_file: 导出文件路径
        """
        if work_name not in self.terms_data:
            print(f"作品不存在: {work_name}")
            return
        
        try:
            with open(export_file, 'w', encoding='utf-8') as f:
                json.dump({work_name: self.terms_data[work_name]}, f, ensure_ascii=False, indent=2)
            print(f"已导出到: {export_file}")
        except Exception as e:
            print(f"导出失败: {e}")
    
    def import_work_terms(self, import_file: str, merge: bool = True):
        """
        从文件导入作品专有名词
        
        Args:
            import_file: 导入文件路径
            merge: 是否与现有数据合并
        """
        try:
            with open(import_file, 'r', encoding='utf-8') as f:
                imported_data = json.load(f)
            
            for work_name, work_data in imported_data.items():
                if merge and work_name in self.terms_data:
                    # 合并模式：更新已有类别
                    for category in self.ALL_CATEGORIES:
                        if category in work_data:
                            self.terms_data[work_name][category].update(work_data[category])
                else:
                    # 覆盖模式或新作品
                    self.terms_data[work_name] = work_data
            
            self._save_terms()
            print(f"已导入: {import_file}")
        except Exception as e:
            print(f"导入失败: {e}")
    
    def get_statistics(self) -> Dict[str, any]:
        """获取统计信息"""
        total_works = len(self.terms_data)
        total_terms = 0
        category_counts = {cat: 0 for cat in self.ALL_CATEGORIES}
        
        for work_data in self.terms_data.values():
            for category in self.ALL_CATEGORIES:
                count = len(work_data.get(category, {}))
                category_counts[category] += count
                total_terms += count
        
        return {
            "total_works": total_works,
            "total_terms": total_terms,
            "category_counts": category_counts
        }


# 便捷函数
_term_manager_instance: Optional[TermManager] = None

def get_term_manager() -> TermManager:
    """获取全局 TermManager 实例"""
    global _term_manager_instance
    if _term_manager_instance is None:
        _term_manager_instance = TermManager()
    return _term_manager_instance
