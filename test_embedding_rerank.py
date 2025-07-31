#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试动态代理的embedding和rerank功能
"""

import requests
import json
import time

class EmbeddingRerankTester:
    def __init__(self, base_url="http://localhost:8080"):
        self.base_url = base_url
        self.session = requests.Session()
        
    def test_health_check(self):
        """测试健康检查"""
        print("🔍 测试健康检查...")
        try:
            response = self.session.get(f"{self.base_url}/health")
            print(f"状态码: {response.status_code}")
            print(f"响应: {response.text}")
            if response.status_code == 200:
                print("✅ 健康检查通过")
                return True
            else:
                print("❌ 健康检查失败")
                return False
        except Exception as e:
            print(f"❌ 健康检查异常: {e}")
            return False
    
    def test_embedding_api(self, domain, path, model, input_text, api_key="your-api-key"):
        """测试embedding API"""
        print(f"\n=== 测试 Embedding API: {domain}{path} ===")
        
        url = f"{self.base_url}/{domain}{path}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "input": input_text
        }
        
        print(f"请求URL: {url}")
        print(f"请求头: {headers}")
        print(f"请求体: {json.dumps(data, indent=2, ensure_ascii=False)}")
        
        try:
            response = self.session.post(url, headers=headers, json=data, timeout=30)
            print(f"状态码: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Embedding API 调用成功")
                print(f"模型: {result.get('model', 'N/A')}")
                print(f"向量维度: {len(result.get('data', [{}])[0].get('embedding', []))}")
                return True
            else:
                print(response.text)
                print(f"❌ Embedding API 调用失败")
                return False
                
        except Exception as e:
            print(f"❌ Embedding API 调用异常: {e}")
            return False
    
    def test_rerank_api(self, domain, path, model, query, documents, api_key="your-api-key"):
        """测试rerank API"""
        print(f"\n=== 测试 Rerank API: {domain}{path} ===")
        
        url = f"{self.base_url}/{domain}{path}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "query": query,
            "documents": documents,
            "top_k": len(documents)
        }
        
        print(f"请求URL: {url}")
        print(f"请求头: {headers}")
        print(f"请求体: {json.dumps(data, indent=2, ensure_ascii=False)}")
        
        try:
            response = self.session.post(url, headers=headers, json=data, timeout=30)
            print(f"状态码: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Rerank API 调用成功")
                print(f"模型: {result.get('model', 'N/A')}")
                print(f"结果数量: {len(result.get('results', []))}")
                for i, item in enumerate(result.get('results', [])[:3]):
                    print(f"  排名 {i+1}: 文档{item.get('index', 'N/A')}, 相关性: {item.get('relevance_score', 'N/A')}")
                return True
            else:
                print(response.text)
                print(f"❌ Rerank API 调用失败")
                return False
                
        except Exception as e:
            print(f"❌ Rerank API 调用异常: {e}")
            return False
    
    def test_forbidden_domain(self):
        """测试被禁止的域名"""
        print(f"\n=== 测试被禁止的域名 ===")
        
        url = f"{self.base_url}/malicious-domain.com/v1/embeddings"
        headers = {
            "Authorization": "Bearer fake-key",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "text-embedding-ada-002",
            "input": "test"
        }
        
        try:
            response = self.session.post(url, headers=headers, json=data, timeout=10)
            print(f"状态码: {response.status_code}")
            print(f"响应: {response.text}")
            
            if response.status_code == 403:
                print("✅ 正确拒绝了被禁止的域名")
                return True
            else:
                print("❌ 应该拒绝被禁止的域名")
                return False
                
        except Exception as e:
            print(f"❌ 测试异常: {e}")
            return False
    
    def run_all_tests(self):
        """运行所有测试"""
        print("🚀 开始测试动态代理的 Embedding 和 Rerank 功能")
        print("=" * 60)
        
        # 健康检查
        if not self.test_health_check():
            print("❌ 健康检查失败，停止测试")
            return
        
        # 测试被禁止的域名
        self.test_forbidden_domain()
        
        # 测试用例数据
        test_cases = [
            {
                "name": "SiliconFlow Embedding",
                "type": "embedding",
                "domain": "api.siliconflow.cn",
                "path": "/v1/embeddings",
                "model": "BAAI/bge-large-zh-v1.5",
                "input": "人工智能是计算机科学的一个分支",
                "api_key": "sk-your-siliconflow-api-key"
            },
            {
                "name": "DeepSeek Embedding", 
                "type": "embedding",
                "domain": "api.deepseek.com",
                "path": "/v1/embeddings",
                "model": "deepseek-embedding",
                "input": "深度学习是机器学习的一个子领域",
                "api_key": "sk-your-deepseek-api-key"
            },
            {
                "name": "Jina AI Rerank",
                "type": "rerank",
                "domain": "deepsearch.jina.ai",
                "path": "/v1/rerank",
                "model": "jina-reranker-v1-base-en",
                "query": "What is artificial intelligence?",
                "documents": [
                    "Artificial intelligence is a branch of computer science.",
                    "Machine learning is a subset of AI.",
                    "Deep learning uses neural networks.",
                    "Natural language processing is an AI application."
                ],
                "api_key": "jina_your-api-key"
            }
        ]
        
        print("\n📝 测试用例说明:")
        print("⚠️  注意：以下测试使用示例API密钥，需要替换为真实密钥才能成功调用")
        print("⚠️  测试主要验证代理的路由和认证转换功能")
        
        # 执行测试
        for case in test_cases:
            print(f"\n📝 测试用例: {case['name']}")
            print("⚠️  注意：需要替换为真实的API密钥才能成功调用")
            
            if case['type'] == 'embedding':
                self.test_embedding_api(
                    domain=case['domain'],
                    path=case['path'],
                    model=case['model'],
                    input_text=case['input'],
                    api_key=case['api_key']
                )
            elif case['type'] == 'rerank':
                self.test_rerank_api(
                    domain=case['domain'],
                    path=case['path'],
                    model=case['model'],
                    query=case['query'],
                    documents=case['documents'],
                    api_key=case['api_key']
                )
        
        print("\n🎉 测试完成")
        print("\n📋 使用说明:")
        print("1. 动态代理支持 embedding 和 rerank 功能")
        print("2. 使用格式: POST http://localhost:8080/{domain}/{path}")
        print("3. 示例:")
        print("   - Embedding: POST /api.siliconflow.cn/v1/embeddings")
        print("   - Rerank: POST /deepsearch.jina.ai/v1/rerank")
        print("4. 认证会根据路径自动识别为 OpenAI 格式")
        print("5. 所有请求会自动保存到数据库")

if __name__ == "__main__":
    tester = EmbeddingRerankTester()
    tester.run_all_tests()