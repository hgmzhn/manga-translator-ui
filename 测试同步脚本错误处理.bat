@echo off
chcp 65001 >nul
echo ========================================
echo 测试私有仓库同步脚本的错误处理
echo ========================================
echo.

echo 测试1: 正常执行（模拟）
echo ----------------------------------------
echo 模拟成功场景...
timeout /t 2 >nul
echo 成功！窗口将在3秒后关闭
timeout /t 3 >nul
echo.

echo 测试2: 模拟错误
echo ----------------------------------------
echo 测试错误处理（传入无效参数）...
echo.
call "私有仓库同步.bat" invalid_branch invalid_action
echo.
echo 错误测试完成，错误码: %errorlevel%
echo.

echo ========================================
echo 所有测试完成
echo ========================================
pause
