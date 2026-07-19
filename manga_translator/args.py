import argparse
import os
import sys


def _env_true(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in ('true', '1', 'yes', 'on')


def create_parser():
    """创建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        description='Manga Translator - 漫画翻译工具',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # 添加子命令
    subparsers = parser.add_subparsers(dest='mode', help='运行模式')
    
    # ===== Web 模式（Web服务器：API + Web界面）=====
    web_parser = subparsers.add_parser('web', help='Web服务器模式（API + Web界面）')
    web_parser.add_argument('--host', 
                           default=os.getenv('MT_WEB_HOST', '0.0.0.0'),
                           help='服务器主机（默认：0.0.0.0，环境变量：MT_WEB_HOST）')
    web_parser.add_argument('--port', 
                           default=int(os.getenv('MT_WEB_PORT', '8000')), 
                           type=int,
                           help='服务器端口（默认：8000，环境变量：MT_WEB_PORT）')
    web_parser.add_argument('--use-gpu', 
                           action='store_true',
                           default=_env_true('MT_USE_GPU'),
                           help='使用 GPU（环境变量：MT_USE_GPU=true）')
    web_parser.add_argument('--disable-onnx-gpu',
                           action='store_true',
                           default=_env_true('MT_DISABLE_ONNX_GPU'),
                           help='禁用 ONNX Runtime GPU 加速（环境变量：MT_DISABLE_ONNX_GPU=true）')
    web_parser.add_argument('--models-ttl', 
                           default=int(os.getenv('MT_MODELS_TTL', '0')), 
                           type=int,
                           help='上次使用后将模型保留在内存中的时间（秒）（0 表示永远，环境变量：MT_MODELS_TTL）')
    web_parser.add_argument('--retry-attempts', 
                           default=int(os.getenv('MT_RETRY_ATTEMPTS', '-1')) if os.getenv('MT_RETRY_ATTEMPTS') else None, 
                           type=int,
                           help='翻译失败时的重试次数（-1 表示无限重试，None 表示使用 API 传入的配置，环境变量：MT_RETRY_ATTEMPTS）')
    web_parser.add_argument('-v', '--verbose', 
                           action='store_true',
                           default=os.getenv('MT_VERBOSE', '').lower() in ('true', '1', 'yes'),
                           help='显示详细日志（环境变量：MT_VERBOSE=true）')
    
    # ===== Local 模式（默认） =====
    local_parser = subparsers.add_parser('local', help='命令行翻译模式')
    local_parser.add_argument('-i', '--input', required=True, nargs='+',
                             help='输入图片或文件夹路径')
    local_parser.add_argument('-o', '--output', default=None,
                             help='输出目录（默认：同目录加 -translated 后缀）')
    local_parser.add_argument('--config', default=None,
                             help='配置文件路径（默认：config/config.json）')
    local_parser.add_argument('-v', '--verbose', action='store_true',
                             help='显示详细日志')
    local_parser.add_argument('--overwrite', action='store_true',
                             help='覆盖已存在的文件')
    local_parser.add_argument('--use-gpu', action='store_true', default=None,
                             help='使用 GPU 加速（覆盖配置文件）')
    local_parser.add_argument('--disable-onnx-gpu', action='store_true', default=None,
                             help='禁用 ONNX Runtime GPU 加速（覆盖配置文件）')
    local_parser.add_argument('--format', default=None,
                             help='输出格式：png/jpg/webp/avif（覆盖配置文件）')
    local_parser.add_argument('--batch-size', type=int, default=None,
                             help='批量处理大小（覆盖配置文件）')
    local_parser.add_argument('--attempts', type=int, default=None,
                             help='翻译失败重试次数，-1表示无限重试（覆盖配置文件）')
    # 内存管理参数（子进程模式）
    local_parser.add_argument('--subprocess', action='store_true',
                             help='启用子进程模式（支持内存管理和断点续传）')
    local_parser.add_argument('--memory-limit', type=int, default=0,
                             help='绝对内存限制（MB），超过后自动重启子进程，0表示不限制（默认：0）')
    local_parser.add_argument('--memory-percent', type=int, default=0,
                             help='内存百分比限制，超过系统总内存的这个百分比时重启，0表示不限制（默认：0）')
    local_parser.add_argument('--batch-per-restart', type=int, default=0,
                             help='每处理N张图片后重启子进程释放内存，0表示不限制（默认：0）')
    
    # ===== WebSocket 模式 =====
    ws_parser = subparsers.add_parser('ws', help='WebSocket 模式')
    ws_parser.add_argument('--host', default='127.0.0.1',
                          help='WebSocket 服务的主机（默认：127.0.0.1）')
    ws_parser.add_argument('--port', default=5003, type=int,
                          help='WebSocket 服务的端口（默认：5003）')
    ws_parser.add_argument('--nonce', default=None,
                          help='用于保护内部 WebSocket 通信的 Nonce')
    ws_parser.add_argument('--ws-url', default='ws://localhost:5000',
                          help='WebSocket 模式的服务器 URL（默认：ws://localhost:5000）')
    ws_parser.add_argument('--models-ttl', default=0, type=int,
                          help='上次使用后将模型保留在内存中的时间（秒）（0 表示永远）')
    ws_parser.add_argument('--retry-attempts', default=None, type=int,
                          help='翻译失败时的重试次数（-1 表示无限重试，None 表示使用 API 传入的配置）')
    ws_parser.add_argument('-v', '--verbose', action='store_true',
                          help='显示详细日志')
    ws_parser.add_argument('--use-gpu', action='store_true',
                          help='使用 GPU')
    ws_parser.add_argument('--disable-onnx-gpu', action='store_true',
                          default=_env_true('MT_DISABLE_ONNX_GPU'),
                          help='禁用 ONNX Runtime GPU 加速')
    
    # ===== Shared 模式（API 实例） =====
    shared_parser = subparsers.add_parser('shared', help='API 模式')
    shared_parser.add_argument('--host', default='127.0.0.1',
                              help='API 服务的主机（默认：127.0.0.1）')
    shared_parser.add_argument('--port', default=5003, type=int,
                              help='API 服务的端口（默认：5003）')
    shared_parser.add_argument('--nonce', default=None,
                              help='用于保护内部 API 服务器通信的 Nonce')
    shared_parser.add_argument('--models-ttl', default=0, type=int,
                              help='模型在内存中的 TTL（秒）（0 表示永远）')
    shared_parser.add_argument('--retry-attempts', default=None, type=int,
                              help='翻译失败时的重试次数（-1 表示无限重试，None 表示使用 API 传入的配置）')
    shared_parser.add_argument('-v', '--verbose', action='store_true',
                              help='显示详细日志')
    shared_parser.add_argument('--use-gpu', action='store_true',
                              help='使用 GPU')
    shared_parser.add_argument('--disable-onnx-gpu', action='store_true',
                              default=_env_true('MT_DISABLE_ONNX_GPU'),
                              help='禁用 ONNX Runtime GPU 加速')
    
    return parser


def parse_args():
    """解析命令行参数"""
    parser = create_parser()
    
    # 如果第一个参数不是模式，默认使用 local 模式
    if len(sys.argv) > 1 and sys.argv[1] not in ['web', 'local', 'ws', 'shared']:
        # 检查是否有 -i 参数（local 模式的必需参数）
        if '-i' in sys.argv or '--input' in sys.argv:
            # 在第一个参数前插入 'local'
            sys.argv.insert(1, 'local')
    
    args = parser.parse_args()
    
    # 如果还是没有模式，显示帮助
    if args.mode is None:
        parser.print_help()
        sys.exit(1)
    
    return args
