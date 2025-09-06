"""
自定义JSON编码器
用于处理numpy数组和其他特殊数据类型的序列化
"""
import json
import numpy as np


class CustomJSONEncoder(json.JSONEncoder):
    """自定义JSON编码器，支持numpy数组等特殊数据类型"""
    
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        
        # 调用父类的默认方法
        return super().default(obj)
