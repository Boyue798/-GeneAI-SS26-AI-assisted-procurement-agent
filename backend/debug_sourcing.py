"""验证 Sourcing 本地DB检索问题 (ChromaDB启用)"""
import sys, os, asyncio
sys.path.insert(0, ".")
from database import query_suppliers_sync
from agent.parser import IntentParser

suppliers = query_suppliers_sync()
print(f"数据库加载: {len(suppliers)} 家供应商")

# 检查 category 分布
from collections import Counter
cats = Counter(s.get("category") for s in suppliers)
print(f"Category 分布: {cats.most_common(10)}")

# 模拟搜索流程
parser = IntentParser(None)  # 不用LLM，纯启发式
query = "glass adhesive Germany"
intent = parser.parse(query)
print(f"\n查询: {query}")
print(f"解析: category={intent.category}, country={intent.country}, keywords={intent.keywords[:6]}")

# 按 category 过滤
matched = [s for s in suppliers if not intent.category or s.get("category") == intent.category]
print(f"Category匹配: {len(matched)}/{len(suppliers)}")
for s in matched[:3]:
    print(f"  {s.get('name')} - category={s.get('category')} - country={s.get('country')}")
