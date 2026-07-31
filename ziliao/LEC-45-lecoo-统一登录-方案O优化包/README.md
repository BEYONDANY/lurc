# lecoo 统一登录 · 方案 O 优化包（LEC-45）

> 公司：lecoo｜使命：Build a SaaS product  
> 依据：方案 A 开工包目录结构 + [LEC-2](/LEC/issues/LEC-2) 已确认方案 O  
> 版本：**O-V1.5**（手机版 + 人脸/指纹/MFA 交互原型 · 单包去重）｜日期：2026-07-28

## 本包做什么

在**保留方案 A 的交付纪律与目录形态**的前提下，将认证内核与产品边界升级为**方案 O**，并按 Board 拒因补齐：

| 拒因 | 本版落地 |
|------|----------|
| 需要手机版 | 原型顶栏「④ 手机版」完整闭环 |
| 人脸 / 指纹 / 多因子 | 原型顶栏「⑤ 安全认证交互」+ 登录 MFA 弹窗 + 管理端策略 |
| 交互原型需要添加上 | 同一 HTML 原型内可点演示 |
| 只要一个压缩包、过滤重复 | 仅交付本 ZIP；包内去掉历史 DELIVERY-RECORD 重复文件 |

## 目录

```text
架构图/
  01-总体架构图.html
  02-部署架构图.html
  03-OIDC登录流程图.html
  04-成员邀请审批流程图.html
开发开工包/
  README.md
  01-接口契约-openapi-v1.yaml
  02-数据库设计-ddl-v1.sql
  03-M1任务卡-backlog.md
  04-测试策略与关键用例.md
V1.0/
  SSO交互原型-C端与管理端-O-V1.0.html   ← 桌面 + 手机 + 安全认证交互（唯一原型入口）
  lecoo统一登录-整体解决方案-O-V1.0.{md,html,pdf,docx}
  lecoo统一登录-架构与资源方案-O-V1.0.{md,html,pdf,docx}
  lecoo统一登录-产品需求文档PRD-O-V1.0.{md,html,pdf,docx}
  lecoo统一登录-需求规格说明书SRS-O-V1.0.{md,html,pdf,docx}
O-V1.5-DELIVERY-RECORD.md
README.md
```

## 如何预览

1. 打开 `V1.0/SSO交互原型-C端与管理端-O-V1.0.html`
2. 顶栏切换：①登录 ②C端 ③管理后端 **④手机版** **⑤安全认证交互**
3. 手机版：面容/指纹 → MFA → 工作台 → 安全设置
4. 安全认证：MFA / 人脸 / 指纹 / 注册 / Step-up / 管理策略
5. 文档：同目录 PDF / DOCX / HTML / MD

## 变更摘要（O-V1.5）

- 新增手机版交互原型（生物解锁、MFA、工作台）
- 新增人脸 / 指纹 / MFA / Step-up / 注册交互流
- PRD / SRS / 解决方案 / 架构文档同步增量并重出 PDF/DOCX
- 包内删除 O-V1.1～O-V1.4 历史交付记录重复文件；对外只保留本 ZIP
