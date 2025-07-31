#!/usr/bin/env python3
"""
动态代理测试脚本
测试不同的API端点和认证方式
"""

import asyncio
import aiohttp
import json
import time

class DynamicProxyTester:
    def __init__(self, proxy_base_url="http://localhost:8080"):
        self.proxy_base_url = proxy_base_url
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def test_health_check(self):
        """测试健康检查端点"""
        print("\n=== 测试健康检查 ===")
        try:
            async with self.session.get(f"{self.proxy_base_url}/health") as resp:
                result = await resp.json()
                print(f"状态码: {resp.status}")
                print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
                return resp.status == 200
        except Exception as e:
            print(f"健康检查失败: {e}")
            return False
    
    async def test_openai_style_api(self, domain, path, model, api_key):
        """测试OpenAI风格的API"""
        print(f"\n=== 测试 OpenAI 风格 API: {domain}{path} ===")
        
        url = f"{self.proxy_base_url}/{domain}{path}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": "你好，请简单介绍一下你自己。"}
            ],
            "stream": False,
            "max_tokens": 100
        }
        
        try:
            print(f"请求URL: {url}")
            print(f"请求头: {headers}")
            print(f"请求体: {json.dumps(payload, indent=2, ensure_ascii=False)}")
            
            async with self.session.post(url, headers=headers, json=payload) as resp:
                print(f"状态码: {resp.status}")
                response_text = await resp.text()
                print(f"响应: {response_text[:500]}..." if len(response_text) > 500 else response_text)
                return resp.status == 200
        except Exception as e:
            print(f"请求失败: {e}")
            return False
    
    async def test_anthropic_style_api(self, domain, path, model, api_key):
        """测试Anthropic风格的API"""
        print(f"\n=== 测试 Anthropic 风格 API: {domain}{path} ===")
        
        url = f"{self.proxy_base_url}/{domain}{path}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": "你好，请简单介绍一下你自己。"}
            ],
            "max_tokens": 100
        }
        
        try:
            print(f"请求URL: {url}")
            print(f"请求头: {headers}")
            print(f"请求体: {json.dumps(payload, indent=2, ensure_ascii=False)}")
            
            async with self.session.post(url, headers=headers, json=payload) as resp:
                print(f"状态码: {resp.status}")
                response_text = await resp.text()
                print(f"响应: {response_text[:500]}..." if len(response_text) > 500 else response_text)
                return resp.status == 200
        except Exception as e:
            print(f"请求失败: {e}")
            return False
    
    async def test_stream_request(self, domain, path, model, api_key):
        """测试流式请求"""
        print(f"\n=== 测试流式请求: {domain}{path} ===")
        
        url = f"{self.proxy_base_url}/{domain}{path}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": "请用一句话介绍Python编程语言。"}
            ],
            "stream": True,
            "max_tokens": 50
        }
        
        try:
            print(f"请求URL: {url}")
            print(f"流式响应:")
            
            async with self.session.post(url, headers=headers, json=payload) as resp:
                print(f"状态码: {resp.status}")
                
                if resp.status == 200:
                    chunk_count = 0
                    async for line in resp.content:
                        line_str = line.decode('utf-8').strip()
                        if line_str:
                            print(f"Chunk {chunk_count}: {line_str}")
                            chunk_count += 1
                            if chunk_count >= 5:  # 只显示前5个chunk
                                print("... (更多chunk)")
                                break
                    return True
                else:
                    response_text = await resp.text()
                    print(f"错误响应: {response_text}")
                    return False
        except Exception as e:
            print(f"流式请求失败: {e}")
            return False
    
    async def test_forbidden_domain(self):
        """测试被禁止的域名"""
        print(f"\n=== 测试被禁止的域名 ===")
        
        url = f"{self.proxy_base_url}/malicious.example.com/v1/chat/completions"
        headers = {
            "Authorization": "Bearer fake-key",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "test-model",
            "messages": [
                {"role": "user", "content": "test"}
            ]
        }
        
        try:
            async with self.session.post(url, headers=headers, json=payload) as resp:
                print(f"状态码: {resp.status}")
                response_text = await resp.text()
                print(f"响应: {response_text}")
                return resp.status == 403  # 应该返回403禁止访问
        except Exception as e:
            print(f"请求失败: {e}")
            return False

async def main():
    """主测试函数"""
    print("🚀 开始动态代理测试")
    print("⚠️  注意：以下测试使用示例API密钥，需要替换为真实密钥才能成功调用")
    
    # 注意：这里使用的是示例API密钥，实际测试时需要替换为真实的密钥
    test_cases = [
        # OpenAI风格的API测试
        {
            "name": "DeepSeek API",
            "domain": "api.deepseek.com",
            "path": "/v1/chat/completions",
            "model": "deepseek-chat",
            "api_key": "sk-your-deepseek-api-key",
            "type": "openai"
        },
        # Anthropic风格的API测试
        {
            "name": "Moonshot Anthropic API",
            "domain": "api.moonshot.cn",
            "path": "/anthropic/v1/messages",
            "model": "claude-3-5-haiku-20241022",
            "api_key": "sk-your-moonshot-api-key",
            "type": "anthropic"
        },
        # SiliconFlow API测试
        {
            "name": "SiliconFlow API",
            "domain": "api.siliconflow.cn",
            "path": "/v1/chat/completions",
            "model": "Qwen/Qwen2.5-7B-Instruct",
            "api_key": "sk-your-siliconflow-api-key",
            "type": "openai"
        }
    ]
    
    async with DynamicProxyTester() as tester:
        # 首先测试健康检查
        health_ok = await tester.test_health_check()
        if not health_ok:
            print("❌ 健康检查失败，请确保代理服务器正在运行")
            return
        
        print("✅ 健康检查通过")
        
        # 测试被禁止的域名
        forbidden_ok = await tester.test_forbidden_domain()
        if forbidden_ok:
            print("✅ 域名白名单功能正常")
        else:
            print("❌ 域名白名单功能异常")
        
        # 测试各种API
        for test_case in test_cases:
            print(f"\n📝 测试用例: {test_case['name']}")
            print("⚠️  注意：需要替换为真实的API密钥才能成功调用")
            
            if test_case['type'] == 'openai':
                success = await tester.test_openai_style_api(
                    test_case['domain'],
                    test_case['path'],
                    test_case['model'],
                    test_case['api_key']
                )
            else:
                success = await tester.test_anthropic_style_api(
                    test_case['domain'],
                    test_case['path'],
                    test_case['model'],
                    test_case['api_key']
                )
            
            if success:
                print(f"✅ {test_case['name']} 测试通过")
                
                # 如果基础测试通过，再测试流式请求
                stream_success = await tester.test_stream_request(
                    test_case['domain'],
                    test_case['path'],
                    test_case['model'],
                    test_case['api_key']
                )
                if stream_success:
                    print(f"✅ {test_case['name']} 流式请求测试通过")
                else:
                    print(f"❌ {test_case['name']} 流式请求测试失败")
            else:
                print(f"❌ {test_case['name']} 测试失败")
    
    print("\n🎉 测试完成")
    print("\n📋 使用说明:")
    print("1. 启动动态代理服务器: python proxy_dynamic.py --port 8080")
    print("2. 使用格式: POST http://localhost:8080/{domain}/{path}")
    print("3. 示例:")
    print("   - OpenAI风格: POST /api.deepseek.com/v1/chat/completions")
    print("   - Anthropic风格: POST /api.moonshot.cn/anthropic/v1/messages")
    print("4. 认证会根据路径自动识别和转换")
    print("5. 所有对话会自动保存为ShareGPT格式")

if __name__ == "__main__":
    asyncio.run(main())