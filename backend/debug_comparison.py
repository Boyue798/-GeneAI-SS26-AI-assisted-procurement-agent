"""验证 Comparison 本地DB过滤问题"""
import sys, os, json
sys.path.insert(0, ".")
from database import query_products_sync

quotes = query_products_sync()
print(f"数据库加载: {len(quotes)} 条报价")

# 检查 category 分布
from collections import Counter
cats = Counter(q.get("category") for q in quotes)
print(f"Category 分布 (前10): {cats.most_common(10)}")

# 检查空 category 数量
empty = sum(1 for q in quotes if not q.get("category"))
print(f"空 category: {empty}/{len(quotes)}")

# 模拟 "A4 paper" 搜索的过滤
intent_category = "paper"  # parser 会从"A4 paper"提取出"paper"
passed_category = [q for q in quotes 
    if intent_category is None or not q.get("category") or q.get("category") == intent_category]
print(f"Category 过滤后: {len(passed_category)}/{len(quotes)}")

# 显示前3个
for q in passed_category[:3]:
    print(f"  product={q.get('product','')[:60]} vendor={q.get('vendor','')} category={q.get('category')} price={q.get('unitPriceEur')}")
