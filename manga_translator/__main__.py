#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Manga Translator - 命令行入口
支持多种运行模式：cli, local, ws, shared
"""
import sys
import asyncio
import logging

def main():
    """主函数"""
    from manga_translator.args import parse_args
    from manga_translator.utils import init_logging, set_log_level, get_logger
    
    # 解析参数
    args = parse_args()
    
    # 初始化日志
    init_logging()
    set_log_level(level=logging.DEBUG if args.verbose else logging.INFO)
    logger = get_logger(args.mode)
    
    # 根据模式分发
    if args.mode == 'web':
        # Web API 服务器模式
        logger.info('Starting Web API server')
        from manga_translator.server.main import main as web_main
        
        # 调用 web_main 来正确设置所有配置
        web_main(args)
    
    elif args.mode == 'local':
        # Local 模式（命令行翻译）
        logger.info('Running in local mode')
        from manga_translator.mode.local import run_local_mode
        asyncio.run(run_local_mode(args))
    
    elif args.mode == 'ws':
        # WebSocket 模式
        logger.info('Running in WebSocket mode')
        from manga_translator.mode.ws import MangaTranslatorWS
        translator = MangaTranslatorWS(vars(args))
        asyncio.run(translator.listen(vars(args)))
    
    elif args.mode == 'shared':
        # Shared/API 模式
        logger.info('Running in shared/API mode')
        from manga_translator.mode.share import MangaShare
        translator = MangaShare(vars(args))
        asyncio.run(translator.listen(vars(args)))
    
    else:
        logger.error(f'Unknown mode: {args.mode}')
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nTranslation cancelled by user.')
        sys.exit(0)
    except asyncio.CancelledError:
        print('\nTranslation cancelled by user.')
        sys.exit(0)
    except Exception as e:
        import traceback
        print(f'\n{e.__class__.__name__}: {e}')
        traceback.print_exc()
        sys.exit(1)
