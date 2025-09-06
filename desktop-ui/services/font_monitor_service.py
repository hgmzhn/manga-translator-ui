#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
字体目录监控服务
监控字体目录变化并通知UI更新
"""

import os
import time
import threading
from typing import Callable, List, Set
from pathlib import Path
import logging

class FontMonitorService:
    """字体目录监控服务"""
    
    def __init__(self, fonts_directory: str):
        self.fonts_directory = fonts_directory
        self.callbacks: List[Callable] = []
        self.monitoring = False
        self.monitor_thread = None
        self.last_fonts: Set[str] = set()
        self.logger = logging.getLogger(__name__)
        
        # 支持的字体文件扩展名
        self.font_extensions = {'.ttf', '.otf', '.ttc', '.woff', '.woff2'}
        
        # 初始化字体列表
        self._update_font_list()
    
    def register_callback(self, callback: Callable):
        """注册字体变化回调函数"""
        if callback not in self.callbacks:
            self.callbacks.append(callback)
            self.logger.info(f"注册字体监控回调: {callback.__name__}")
    
    def unregister_callback(self, callback: Callable):
        """取消注册字体变化回调函数"""
        if callback in self.callbacks:
            self.callbacks.remove(callback)
            self.logger.info(f"取消注册字体监控回调: {callback.__name__}")
    
    def _get_font_files(self) -> Set[str]:
        """获取字体目录中的所有字体文件"""
        font_files = set()
        
        if not os.path.exists(self.fonts_directory):
            return font_files
        
        try:
            for filename in os.listdir(self.fonts_directory):
                file_path = os.path.join(self.fonts_directory, filename)
                if (os.path.isfile(file_path) and 
                    Path(filename).suffix.lower() in self.font_extensions):
                    font_files.add(filename)
        except Exception as e:
            self.logger.error(f"扫描字体目录失败: {e}")
        
        return font_files
    
    def _update_font_list(self):
        """更新字体列表"""
        self.last_fonts = self._get_font_files()
    
    def _notify_callbacks(self, font_files: Set[str]):
        """通知所有注册的回调函数"""
        for callback in self.callbacks:
            try:
                callback(sorted(font_files))
            except Exception as e:
                self.logger.error(f"字体监控回调执行失败 {callback.__name__}: {e}")
    
    def _monitor_loop(self):
        """监控循环"""
        self.logger.info("开始监控字体目录变化")
        
        while self.monitoring:
            try:
                current_fonts = self._get_font_files()
                
                if current_fonts != self.last_fonts:
                    self.logger.info(f"检测到字体目录变化: {current_fonts - self.last_fonts} 新增, {self.last_fonts - current_fonts} 删除")
                    self.last_fonts = current_fonts
                    self._notify_callbacks(current_fonts)
                
                time.sleep(2.0)  # 每2秒检查一次
                
            except Exception as e:
                self.logger.error(f"字体监控循环错误: {e}")
                time.sleep(5.0)  # 出错时等待更长时间
    
    def start_monitoring(self):
        """开始监控字体目录"""
        if self.monitoring:
            self.logger.warning("字体监控已经在运行")
            return
        
        if not os.path.exists(self.fonts_directory):
            self.logger.warning(f"字体目录不存在: {self.fonts_directory}")
            return
        
        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="FontMonitor"
        )
        self.monitor_thread.start()
        self.logger.info("字体目录监控已启动")
    
    def stop_monitoring(self):
        """停止监控字体目录"""
        if not self.monitoring:
            return
        
        self.monitoring = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=3.0)
        
        self.logger.info("字体目录监控已停止")
    
    def get_current_fonts(self) -> List[str]:
        """获取当前字体列表"""
        return sorted(self._get_font_files())
    
    def refresh_fonts(self):
        """手动刷新字体列表"""
        current_fonts = self._get_font_files()
        if current_fonts != self.last_fonts:
            self.last_fonts = current_fonts
            self._notify_callbacks(current_fonts)
            self.logger.info("手动刷新字体列表完成")
        else:
            self.logger.info("字体列表无变化")
