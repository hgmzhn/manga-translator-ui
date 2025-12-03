# -*- coding: utf-8 -*-
"""
系统资源监控服务
监控 CPU、GPU、内存使用率
"""

import psutil
import subprocess
import threading
from typing import Dict, Optional
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
import logging


class SystemMonitor(QObject):
    """系统资源监控器"""
    
    # 信号：资源使用率更新 (cpu%, gpu_3d%, gpu_compute%, ram_used_gb, ram_total_gb, vram_used_gb, vram_total_gb, hdd%)
    stats_updated = pyqtSignal(float, float, float, float, float, float, float, float)
    
    def __init__(self, interval_ms: int = 1000, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger("services.system_monitor")
        self.interval_ms = interval_ms
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_stats)
        
        # 监控配置（控制是否监控各项）
        self._monitor_cpu = True
        self._monitor_gpu = True
        self._monitor_ram = True
        self._monitor_vram = True
        self._monitor_hdd = True
        
        # 缓存的 GPU 使用率（后台线程更新）
        self._gpu_3d = 0.0
        self._gpu_compute = 0.0
        self._vram_used = 0.0
        self._vram_total = 0.0
        self._gpu_lock = threading.Lock()
        self._gpu_thread = None
        self._gpu_update_interval = 2  # GPU 更新间隔（秒）
        self._gpu_update_counter = 0
        
    def start(self):
        """开始监控"""
        self._timer.start(self.interval_ms)
        self.logger.info(f"系统监控已启动，间隔 {self.interval_ms}ms")
        
    def stop(self):
        """停止监控"""
        self._timer.stop()
        self.logger.info("系统监控已停止")
    
    def set_interval(self, interval_ms: int):
        """设置监控间隔"""
        self.interval_ms = interval_ms
        if self._timer.isActive():
            self._timer.stop()
            self._timer.start(interval_ms)
        self.logger.info(f"监控间隔已更新为 {interval_ms}ms")
    
    def set_config(self, config: dict):
        """设置监控配置"""
        self._monitor_cpu = config.get('show_cpu', True)
        self._monitor_gpu = config.get('show_gpu', True)
        self._monitor_ram = config.get('show_ram', True)
        self._monitor_vram = config.get('show_vram', True)
        self._monitor_hdd = config.get('show_hdd', True)
        self.logger.debug(f"监控配置已更新: CPU={self._monitor_cpu}, GPU={self._monitor_gpu}, RAM={self._monitor_ram}, VRAM={self._monitor_vram}, HDD={self._monitor_hdd}")
    
    def _update_stats(self):
        """更新统计数据（主线程，不阻塞）"""
        try:
            # 根据配置决定是否监控各项
            cpu_percent = psutil.cpu_percent(interval=None) if self._monitor_cpu else 0.0
            
            if self._monitor_ram:
                mem_info = psutil.virtual_memory()
                mem_used_gb = mem_info.used / (1024 ** 3)
                mem_total_gb = mem_info.total / (1024 ** 3)
            else:
                mem_used_gb = 0.0
                mem_total_gb = 0.0
            
            # HDD 使用率（当前工作目录所在磁盘）
            if self._monitor_hdd:
                try:
                    import os
                    # Windows 使用当前驱动器，Linux/Mac 使用根目录
                    drive = os.path.splitdrive(os.getcwd())[0] or '/'
                    if drive and not drive.endswith(os.sep):
                        drive += os.sep
                    disk_usage = psutil.disk_usage(drive)
                    hdd_percent = disk_usage.percent
                except:
                    hdd_percent = 0.0
            else:
                hdd_percent = 0.0
            
            # 使用缓存的 GPU 数据（不阻塞）
            with self._gpu_lock:
                gpu_3d = self._gpu_3d if self._monitor_gpu else 0.0
                gpu_compute = self._gpu_compute if self._monitor_gpu else 0.0
                vram_used = self._vram_used if self._monitor_vram else 0.0
                vram_total = self._vram_total if self._monitor_vram else 0.0
            
            # 只有需要 GPU 或 VRAM 时才启动后台线程
            if self._monitor_gpu or self._monitor_vram:
                self._gpu_update_counter += 1
                if self._gpu_update_counter >= self._gpu_update_interval:
                    self._gpu_update_counter = 0
                    self._start_gpu_update_thread()
            
            self.stats_updated.emit(cpu_percent, gpu_3d, gpu_compute, mem_used_gb, mem_total_gb, vram_used, vram_total, hdd_percent)
            
        except Exception as e:
            self.logger.warning(f"获取系统状态失败: {e}")
            self.stats_updated.emit(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    
    def _start_gpu_update_thread(self):
        """启动后台线程更新 GPU 数据"""
        # 如果上一个线程还在运行，跳过
        if self._gpu_thread and self._gpu_thread.is_alive():
            return
        
        self._gpu_thread = threading.Thread(target=self._update_gpu_in_background, daemon=True)
        self._gpu_thread.start()
    
    def _update_gpu_in_background(self):
        """后台线程更新 GPU 数据"""
        try:
            gpu_stats = self._get_gpu_usage()
            vram_stats = self._get_vram_usage()
            with self._gpu_lock:
                self._gpu_3d = gpu_stats.get('3d', 0.0)
                self._gpu_compute = gpu_stats.get('compute', 0.0)
                self._vram_used = vram_stats.get('used', 0.0)
                self._vram_total = vram_stats.get('total', 0.0)
        except Exception as e:
            self.logger.debug(f"后台 GPU 更新失败: {e}")
    
    def _get_gpu_usage(self) -> Dict[str, float]:
        """获取 GPU 使用率 (3D 和 Compute)"""
        result = {'3d': 0.0, 'compute': 0.0}
        
        try:
            # 尝试使用 nvidia-smi 获取 NVIDIA GPU 使用率
            nvidia_result = self._get_nvidia_gpu_usage()
            if nvidia_result:
                return nvidia_result
            
            # 尝试使用 Windows Performance Counter 获取 GPU 使用率
            windows_result = self._get_windows_gpu_usage()
            if windows_result:
                return windows_result
                
        except Exception as e:
            self.logger.debug(f"获取 GPU 使用率失败: {e}")
        
        return result
    
    def _get_nvidia_gpu_usage(self) -> Optional[Dict[str, float]]:
        """使用 nvidia-smi 获取 NVIDIA GPU 使用率"""
        try:
            # 查询 GPU 利用率
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            
            if result.returncode == 0:
                gpu_util = float(result.stdout.strip().split('\n')[0])
                # NVIDIA 只返回总体利用率，我们假设主要是 3D/Compute 混合使用
                return {'3d': gpu_util, 'compute': gpu_util}
                
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass
        except Exception as e:
            self.logger.debug(f"nvidia-smi 查询失败: {e}")
        
        return None
    
    def _get_windows_gpu_usage(self) -> Optional[Dict[str, float]]:
        """使用 Windows PowerShell 获取 GPU 使用率"""
        try:
            # 使用 PowerShell 获取 GPU 引擎使用率
            ps_script = '''
            $counters = Get-Counter -Counter "\\GPU Engine(*engtype_3D)\\Utilization Percentage","\\GPU Engine(*engtype_Compute*)\\Utilization Percentage" -ErrorAction SilentlyContinue
            if ($counters) {
                $samples = $counters.CounterSamples
                $gpu3d = ($samples | Where-Object { $_.Path -like "*3D*" } | Measure-Object -Property CookedValue -Sum).Sum
                $gpuCompute = ($samples | Where-Object { $_.Path -like "*Compute*" } | Measure-Object -Property CookedValue -Sum).Sum
                Write-Output "$gpu3d|$gpuCompute"
            } else {
                Write-Output "0|0"
            }
            '''
            
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_script],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            
            if result.returncode == 0:
                output = result.stdout.strip()
                if '|' in output:
                    parts = output.split('|')
                    gpu_3d = float(parts[0]) if parts[0] else 0.0
                    gpu_compute = float(parts[1]) if parts[1] else 0.0
                    return {'3d': min(gpu_3d, 100.0), 'compute': min(gpu_compute, 100.0)}
                    
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass
        except Exception as e:
            self.logger.debug(f"PowerShell GPU 查询失败: {e}")
        
        return None
    
    def _get_vram_usage(self) -> Dict[str, float]:
        """获取 VRAM (显存) 使用情况，支持 NVIDIA 和 AMD"""
        result = {'used': 0.0, 'total': 0.0}
        
        # 尝试 NVIDIA
        nvidia_result = self._get_nvidia_vram()
        if nvidia_result:
            return nvidia_result
        
        # 尝试 AMD (通过 Windows WMI)
        amd_result = self._get_amd_vram()
        if amd_result:
            return amd_result
        
        return result
    
    def _get_nvidia_vram(self) -> Optional[Dict[str, float]]:
        """获取 NVIDIA 显卡 VRAM"""
        try:
            cmd_result = subprocess.run(
                ['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits'],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            
            if cmd_result.returncode == 0:
                output = cmd_result.stdout.strip().split('\n')[0]
                parts = output.split(',')
                if len(parts) >= 2:
                    vram_used_mib = float(parts[0].strip())
                    vram_total_mib = float(parts[1].strip())
                    return {
                        'used': vram_used_mib / 1024,  # MiB -> GB
                        'total': vram_total_mib / 1024
                    }
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass
        except Exception as e:
            self.logger.debug(f"NVIDIA VRAM 查询失败: {e}")
        return None
    
    def _get_amd_vram(self) -> Optional[Dict[str, float]]:
        """获取 AMD/Intel 显卡 VRAM (通过 Windows 性能计数器)"""
        try:
            # 使用 PowerShell 获取 GPU 专用内存使用和总量
            ps_script = '''
            try {
                $counters = Get-Counter -Counter "\\GPU Adapter Memory(*)\\Dedicated Usage" -ErrorAction SilentlyContinue
                $used = 0
                if ($counters) {
                    $used = ($counters.CounterSamples | Measure-Object -Property CookedValue -Sum).Sum
                }
                
                # 优先从注册表获取真实显存大小（解决 4GB 溢出问题）
                $total = 0
                $regPaths = @(
                    "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0000",
                    "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0001"
                )
                foreach ($regPath in $regPaths) {
                    if (Test-Path $regPath) {
                        $qw = (Get-ItemProperty -Path $regPath -Name "HardwareInformation.qwMemorySize" -ErrorAction SilentlyContinue)."HardwareInformation.qwMemorySize"
                        if ($qw -and $qw -gt $total) { $total = $qw }
                    }
                }
                
                # 如果注册表没有，尝试 WMI
                if ($total -eq 0) {
                    $adapter = Get-CimInstance -ClassName Win32_VideoController | Where-Object { $_.AdapterRAM -gt 0 } | Select-Object -First 1
                    if ($adapter) { $total = [uint64]$adapter.AdapterRAM }
                }
                
                Write-Output "$used|$total"
            } catch {
                Write-Output "0|0"
            }
            '''
            
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps_script],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            
            if result.returncode == 0:
                output = result.stdout.strip()
                if '|' in output:
                    parts = output.split('|')
                    used_bytes = float(parts[0]) if parts[0] else 0.0
                    total_bytes = float(parts[1]) if parts[1] else 0.0
                    if total_bytes > 0:
                        return {
                            'used': used_bytes / (1024 ** 3),  # bytes -> GB
                            'total': total_bytes / (1024 ** 3)
                        }
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass
        except Exception as e:
            self.logger.debug(f"AMD/通用 VRAM 查询失败: {e}")
        return None
    
    def get_current_stats(self) -> Dict[str, float]:
        """获取当前系统状态（同步方法）"""
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            mem_info = psutil.virtual_memory()
            mem_used_gb = mem_info.used / (1024 ** 3)
            mem_total_gb = mem_info.total / (1024 ** 3)
            gpu_stats = self._get_gpu_usage()
            
            return {
                'cpu': cpu_percent,
                'gpu_3d': gpu_stats.get('3d', 0.0),
                'gpu_compute': gpu_stats.get('compute', 0.0),
                'mem_used': mem_used_gb,
                'mem_total': mem_total_gb
            }
        except Exception as e:
            self.logger.warning(f"获取系统状态失败: {e}")
            return {'cpu': 0.0, 'gpu_3d': 0.0, 'gpu_compute': 0.0, 'mem_used': 0.0, 'mem_total': 0.0}
