# ProcureAI 完整测试报告
## 2026-07-03 | 简伯约

---

## 一、测试概览

| 测试项 | 状态 | 详情 |
|--------|------|------|
| 后端启动 | ✅ 通过 | 需修复 sys.path 污染问题 |
| 健康检查 `/api/health` | ✅ 通过 | `{"status": "ok"}` |
| 认证 `/api/auth/login` | ⚠️ 通过(有风险) | 硬编码密码 `password123` |
| 认证 `/api/auth/me` | ✅ 通过 | Bearer token 验证正常 |
| 无权访问拦截 | ✅ 通过 | 401 + 标准错误格式 |
| Vault API | ✅ 通过 | 内存存储，重启丢失 |
| Conversations API | ✅ 通过 | 无数据库时降级到本地JSON |
| Sourcing `/api/sourcing/search` | ✅ 通过 | 返回12个网络搜索结果 |
| Comparison `/api/comparison/search` | ⚠️ 通过 | 返回0结果（因无本地数据+无有效价格） |
| 数据库连接 | ❌ 失败 | DATABASE_URL 未配置 |

---

## 二、🔴 严重问题（必须修复）

### 2.1 数据库无法调用 — 根因分析

**问题**: `.env` 文件中缺失 `DATABASE_URL`。

**证据**:
- 代码 `backend/database.py:18` 读取 `os.getenv("DATABASE_URL")`
- 但 `.env` 文件只包含 `OPENAI_API_KEY`、`LLM_MODEL` 等，没有数据库连接串
- `.env.example` 定义的是分离字段 `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`，但代码从未拼接它们
- **代码和配置不匹配！**

**后果**:
- 所有数据库查询返回空（`get_connection()` 返回 `None`）
- sourcing 模块无法加载本地供应商（7个 mock 供应商不会被加载）
- comparison 模块无法加载本地产品报价

**修复**:
```bash
# 在 backend/.env 中添加（从数据库同学获取 Supabase 连接串）:
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

或者修改 `database.py` 从分离字段拼接:
```python
database_url = os.getenv("DATABASE_URL") or (
    f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)
```

---

### 2.2 OPENAI_API_KEY 是占位符

**问题**: `.env` 文件中 `OPENAI_API_KEY=***`，只有3个星号。

**后果**:
- LLM 意图解析降级到纯启发式规则（`parser.py` 的 `_parse_heuristically`）
- LLM 排序降级到启发式打分
- LLM 翻译德语搜索词失败
- 整个 Agent 退化为关键词匹配模式

**修复**: 填入真实的 OpenAI/DeepSeek API Key。

---

### 2.3 `db_writer.py` 空指针崩溃风险

**问题**: `db_writer.py` 第31-34行：
```python
conn = get_connection()  # 可能返回 None
try:
    cur = conn.cursor()   # None.cursor() → AttributeError!
```

但 `database.py` 的 `query_suppliers()` 有同样的问题——`get_connection()` 返回 `None` 时没有提前检查。

**对比**: `conversations.py:236-237` 正确处理了:
```python
conn = _db_conn()
if conn is None:
    return _local_list(...)  # 降级到本地存储
```

**修复**: 统一所有数据库调用方，在 `get_connection()` 返回 `None` 时优雅降级。

---

### 2.4 `db_writer.py` 与 `schema.sql` 的 `sourcing_candidate` 表结构完全冲突 🔴🔴

**这是双agent交叉验证的最严重发现。**

**问题**: 两张表的设计理念完全不同：

| | `schema.sql:108-115` 定义 | `db_writer.py:77-96` 实际使用 |
|---|---|---|
| 列名 | `id, session_id, supplier_id, relevance, quality_note, is_incumbent` | `name, origin, website, country, contact_name, contact_email, contact_phone, scale, rating, attributes` |
| 模型 | 关联表（记录哪个 session 搜出了哪个 supplier） | 当 supplier 的克隆表在用 |
| 关键约束 | `session_id NOT NULL` 外键到 `sourcing_session` | **根本没有这列！** |

**崩溃位置**:
- `db_writer.py:72`: `SELECT id FROM sourcing_candidate WHERE name = %s` → **列 `name` 不存在**
- `db_writer.py:79-96`: `INSERT INTO sourcing_candidate (name, origin, ...)` → **列名全错**
- `db_writer.py:122`: `SELECT * FROM sourcing_candidate WHERE name = %s` → **同上**

**修复**: 二选一：
- **方案A**: 修改 `schema.sql`，让 `sourcing_candidate` 变成 supplier 的克隆表（加 name/origin/website 等列）
- **方案B**: 修改 `db_writer.py`，先建 `sourcing_session` 再插 `sourcing_candidate` 作为关联记录

---

## 三、🟡 中等问题

### 3.1 Hermes 环境 Python 路径污染

**问题**: `/Users/jianboyue/.hermes/hermes-agent/venv/lib/python3.11/site-packages/` 中的 `.pth` 文件会将 Hermes 的 python3.11 包注入到每个 Python 进程的 `sys.path` 最前面，而 Hermes 的 pydantic 已损坏。

**修复方案**: 为项目创建 `run.sh`:
```bash
#!/bin/bash
cd "$(dirname "$0")/backend"
PYTHONPATH="" .venv/bin/python3 -c "
import sys
sys.path = [p for p in sys.path if 'hermes-agent' not in p and '.hermes' not in p]
sys.path.insert(0, '.')
import uvicorn
uvicorn.run('main:app', host='0.0.0.0', port=8000, reload=False)
"
```

---

### 3.2 `api/auth.py` 硬编码密码

- **第33行**: 密码 `password123` 明文硬编码
- **第31-41行**: 用户数据 `MOCK_USERS` 是字典，没有真正的认证
- Token 存储在第42行 `MOCK_TOKENS` 字典，服务重启全部丢失

**建议**: 至少有环境变量配置 mock 用户的密码，或接入 Supabase Auth。

---

### 3.3 `sourcing.py` 和 `comparison.py` 大量代码重复

两个文件几乎是镜像结构:
- `_SEARCH_JOBS` ↔ `_COMPARISON_JOBS`
- `_SearchJobState` ↔ `_ComparisonJobState`
- `_append_event()` ↔ 完全相同的逻辑
- `_prune_jobs()` ↔ 完全相同的逻辑
- `_format_sse()` ↔ 完全相同的函数
- SSE streaming 逻辑完全相同

**建议**: 抽取公共基类 `BaseJobManager`。

---

### 3.4 `procurement_agent.py` 过于臃肿 (1272行)

单个文件包含:
- 供应商搜索全流程
- 产品报价搜索全流程
- LLM翻译、过滤、相关判断
- 价格提取正则
- Web搜索整合
- 结果去重合并

**建议**: 拆分为:
- `agent/supplier_pipeline.py` — 供应商搜索
- `agent/quote_pipeline.py` — 报价搜索
- `agent/web_filters.py` — LLM过滤/相关性判断
- `agent/price_extractor.py` — 价格提取

---

### 3.5 `conversations.py` 的 `_ensure_table()` 设计不合理

**问题**: 第77-131行通过 `ALTER TABLE ADD COLUMN IF NOT EXISTS` 补字段，这是数据迁移操作，不应该在每次请求时执行。

**建议**: 这些字段应在 `schema.sql` 中统一定义，或通过独立的 migration 脚本执行。

---

### 3.6 `parser.py` 硬编码所有品类关键词

**问题**: `CATEGORY_KEYWORDS` 字典(第18-97行) 硬编码了17个品类的所有中英德关键词。

**建议**: 将品类关键词移到外部配置文件（如 `categories.yaml`），方便非开发者维护。

---

## 四、🟢 轻微问题

### 4.1 前端 `api.ts` Token 泄露风险

`api.ts:79` 和 `api.ts:109`:
```typescript
Authorization: *** ${token}
```
Template literal 中有三个星号 —— 这可能是笔误或占位符。应该是:
```typescript
Authorization: `Bearer ${token}`
```

### 4.2 Vault 是纯内存存储

`vault.py:32`: `MOCK_VAULT: dict` — API Keys 存内存，重启全部丢失。

### 4.3 `agent_interface.py` 几乎未被使用

这个文件的函数和 `api/sourcing.py`、`api/comparison.py` 中的逻辑重复。

### 4.4 缺少前端 VITE_API_BASE_URL 配置

前端 `api.ts:24` 读取 `VITE_API_BASE_URL`，但项目中没有 `.env.local` 或 Vercel 环境变量设置，可能会默认走 mock 模式。

---

## 五、Python 依赖警告

```
WARNING: hermes-agent venv (python3.11) packages leaking into project venv (python3.12)
         → 所有 package 实际安装到了 Hermes 的 site-packages
         → 必须用 --target 参数重新安装
```

---

## 六、测试建议

### 优先级 P0（立即）
1. **配置 DATABASE_URL** — 数据库同学提供 Supabase 连接串
2. **配置真实的 OPENAI_API_KEY**
3. **修复 `db_writer.py` 空指针** — 在 `conn.cursor()` 前检查 `conn is not None`

### 优先级 P1（本周）
4. **修复 `sourcing_candidate` schema 不匹配** — 先建 session 再插 candidate
5. **创建 `run.sh` 启动脚本** — 绕过 Hermes 路径污染
6. **填写 `VITE_API_BASE_URL`** — 前端才能连到后端

### 优先级 P2（月底前）
7. **拆分 `procurement_agent.py`** — 1272行太长
8. **抽取 `sourcing.py` / `comparison.py` 公共代码**
9. **品类关键词外部化**

---

## 七、总结

| 维度 | 评分 | 说明 |
|------|------|------|
| 功能完整性 | 7/10 | 核心流程跑通，但数据库和LLM是假的 |
| 代码质量 | 6/10 | 架构清晰但有重复代码，缺少防御性检查 |
| 安全性 | 4/10 | 硬编码密码、API Key占位符、Bearer token明文 |
| 可维护性 | 6/10 | 拆分后可提升，parser硬编码品类是瓶颈 |
| 测试覆盖 | 5/10 | 有测试文件但覆盖不全，缺少集成测试 |

**核心结论**: 软件架构合理，核心功能可运行。**数据库"无法调用"的根因是 `DATABASE_URL` 环境变量完全缺失** — 代码读取的变量名和 `.env.example` 定义的字段名不一致。修复配置后加上真实的 API Key，系统即可正常工作。
