import argparse
import sys

def create_parser():
    """创建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        description='Manga Translator - 漫画翻译工具',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # 添加子命令
    subparsers = parser.add_subparsers(dest='mode', help='运行模式')
    
    # ===== Web 模式 =====
    web_parser = subparsers.add_parser('web', help='Web API 服务器模式')
    web_parser.add_argument('--host', default='127.0.0.1',
                           help='服务器主机（默认：127.0.0.1）')
    web_parser.add_argument('--port', default=8000, type=int,
                           help='服务器端口（默认：8000）')
    web_parser.add_argument('--use-gpu', action='store_true',
                           help='使用 GPU')
    web_parser.add_argument('--models-ttl', default=0, type=int,
                           help='上次使用后将模型保留在内存中的时间（秒）（0 表示永远）')
    web_parser.add_argument('--retry-attempts', default=None, type=int,
                           help='翻译失败时的重试次数（-1 表示无限重试，None 表示使用 API 传入的配置）')
    web_parser.add_argument('-v', '--verbose', action='store_true',
                           help='显示详细日志')
    
    # ===== Local 模式（默认） =====
    local_parser = subparsers.add_parser('local', help='命令行翻译模式')
    local_parser.add_argument('-i', '--input', required=True, nargs='+',
                             help='输入图片或文件夹路径')
    local_parser.add_argument('-o', '--output', default=None,
                             help='输出目录（默认：同目录加 -translated 后缀）')
    local_parser.add_argument('--config', default=None,
                             help='配置文件路径（默认：examples/config.json）')
    local_parser.add_argument('-v', '--verbose', action='store_true',
                             help='显示详细日志')
    local_parser.add_argument('--overwrite', action='store_true',
                             help='覆盖已存在的文件')
    
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
    
    return parser


def parse_args():
    """解析命令行参数"""
    parser = create_parser()
    args = parser.parse_args()
    
    # 如果没有指定模式，默认使用 cli 模式
    if args.mode is None:
        # 检查是否有 -i 参数（CLI 模式的必需参数）
        if '-i' in sys.argv or '--input' in sys.argv:
            # 重新解析为 cli 模式
            sys.argv.insert(1, 'cli')
            args = parser.parse_args()
        else:
            parser.print_help()
            sys.exit(1)
    
    return args
