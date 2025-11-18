"""
OpenAI API连接测试脚本
用于诊断"所有API密钥均请求失败"问题
"""

import os
import sys
from dotenv import load_dotenv

# 加载.env文件
print("=" * 60)
print("OpenAI API连接测试")
print("=" * 60)

load_dotenv()

# 读取配置
api_key = os.getenv('OPENAI_API_KEY', '')
base_url = os.getenv('OPENAI_API_BASE', 'https://api.openai.com/v1')
model = os.getenv('OPENAI_MODEL', 'gpt-4o')

# 显示配置信息
print("\n📋 当前配置：")
print("-" * 60)
if api_key:
    masked_key = api_key[:20] + "..." + api_key[-4:] if len(api_key) > 24 else "***"
    print(f"✓ API密钥: {masked_key}")
else:
    print("✗ API密钥: 未设置")
    print("\n❌ 错误: OPENAI_API_KEY 未在.env文件中配置")
    print("\n请在 .env 文件中添加：")
    print("OPENAI_API_KEY=sk-your-api-key-here")
    sys.exit(1)

print(f"✓ API地址: {base_url}")
print(f"✓ 模型名称: {model}")

# 测试连接
print("\n🔍 测试API连接...")
print("-" * 60)

try:
    from openai import OpenAI
    
    # 创建客户端
    client = OpenAI(
        api_key=api_key,
        base_url=base_url
    )
    
    # 发送测试请求
    print("发送测试请求...")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "你好，请回复'测试成功'"}
        ],
        max_tokens=50,
        temperature=0.5
    )
    
    # 获取响应
    result = response.choices[0].message.content
    
    print("\n✅ API连接成功！")
    print("-" * 60)
    print(f"响应内容: {result}")
    print(f"使用的模型: {response.model}")
    print(f"消耗Token: {response.usage.total_tokens if response.usage else 'N/A'}")
    
    print("\n🎉 你的API配置正常，可以正常使用翻译功能！")
    
except ImportError:
    print("\n❌ 错误: 未安装openai库")
    print("\n请运行以下命令安装：")
    print("pip install openai")
    sys.exit(1)

except Exception as e:
    error_msg = str(e)
    print(f"\n❌ API连接失败")
    print("-" * 60)
    print(f"错误类型: {type(e).__name__}")
    print(f"错误信息: {error_msg}")
    
    # 提供具体的解决建议
    print("\n💡 可能的原因和解决方案：")
    print("-" * 60)
    
    if "401" in error_msg or "Unauthorized" in error_msg:
        print("🔑 API密钥无效")
        print("   - 检查API密钥是否正确")
        print("   - 确认密钥未过期或被撤销")
        print("   - 访问 https://platform.openai.com/api-keys 查看密钥状态")
    
    elif "429" in error_msg or "Rate limit" in error_msg:
        print("⏱️ 请求频率超限")
        print("   - 等待几分钟后重试")
        print("   - 在.env中添加: OPENAI_MAX_REQUESTS_PER_MINUTE=3")
    
    elif "insufficient_quota" in error_msg or "quota" in error_msg.lower():
        print("💰 配额不足")
        print("   - 检查账户余额: https://platform.openai.com/usage")
        print("   - 充值或等待配额重置")
        print("   - 检查付款方式是否有效")
    
    elif "Connection" in error_msg or "timeout" in error_msg.lower():
        print("🌐 网络连接问题")
        print("   - 检查网络连接")
        print("   - 如果使用代理，确认代理设置正确")
        print("   - 尝试使用其他网络")
    
    elif "model" in error_msg.lower():
        print("🤖 模型不可用")
        print(f"   - 当前模型: {model}")
        print("   - 尝试改用其他模型（如 gpt-4o-mini）")
        print("   - 在.env中修改: OPENAI_MODEL=gpt-4o-mini")
    
    else:
        print("❓ 其他错误")
        print("   - 查看上方的错误信息")
        print("   - 检查API服务状态: https://status.openai.com/")
        print("   - 如果使用中转API，联系服务商")
    
    print("\n📚 详细解决方案请查看: API密钥失败问题解决方案.md")
    sys.exit(1)

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
