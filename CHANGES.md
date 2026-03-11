# 修复记录

## 修复的问题

1. ✅ **Config.setdefault() 错误** - `src/skills/runtime.py`
   - Config 类不是字典，改用 `config.get()` + `config.set()`

2. ✅ **百度云中文编码错误** - `src/baidu_crawler.py`
   - HTTP Referer header 中文导致 `UnicodeEncodeError`
   - 使用 `urllib.parse.quote()` 编码
   - 优化产品过滤逻辑，支持功能关键词搜索（如"安全组"）

3. ✅ **火山云中文编码错误** - `src/volcano_crawler.py`
   - 同百度云问题，使用 `quote()` 编码
   - 优化产品过滤逻辑

4. ✅ **阿里云子产品文档遗漏** - `src/crawler.py`
   - 新增 `fetch_product_info()` 和 `discover_sub_product_aliases()`
   - VPN 文档从 19 篇扩展到 196 篇（含 IPsec-VPN、SSL-VPN）

5. ✅ **阿里云 API 302 重定向** - 无需修复
   - requests 库默认自动处理

## 修改的文件

核心修复：
- `src/skills/runtime.py`
- `src/baidu_crawler.py`
- `src/volcano_crawler.py`
- `src/crawler.py`
- `src/skills/fetch_doc_skill.py` (max_pages: 20 → 200)
- `src/skills/check_changes_skill.py` (max_pages: 20 → 200)

文档：
- `FIX_SUMMARY.md` (详细修复说明)
- `CHANGES.md` (本文件)

## 测试结果

所有修复已验证通过：
- ✓ Config get/set 方法正常工作
- ✓ 百度云搜索"安全组"找到 10+ 篇文档（跨产品）
- ✓ 火山云搜索"安全组"找到 22+ 篇文档
- ✓ 阿里云 VPN 发现 196 篇文档（含子产品）
- ✓ 阿里云 API 正常返回 200

## 影响范围

- 支持搜索跨产品的功能关键词
- 阿里云产品文档发现更完整
- 所有中文查询场景正常工作
- 不影响原有功能
