# R4-A.1 制度核对与旧资产修订演练（1.3.0）

沿用 R3 演练的隔离 Compose 环境与建设、审核、分析三个身份。启动入口为 `scripts/start_r2_demo.py`。本轮使用规划方提供的三份原文。测试回执注明实际渠道和模型。 [环境与账号](R3-DEMO.md) · [验收材料](r4a1-materials/README.md)

## 上传到报告

1. 建设人员在工作台拖入 `regulation-plain.txt`，点“开始整理”。核对页顶部应显示 **2026-01-01**，依据是第十二条“本办法自2026年1月1日起施行”。查看整理结果中的自动重试和弃用数。若整篇回退，页面显示“本次未经 AI 整理”，本次真实模型演练计失败。
2. 核对第四条的原文、适用范围与条件。在顶部的批量确认列表可逐项展开依据原句，并取消自己尚未核实的条目。条件和例外分别选择。第五条的补正要求来自该条原文，即使手工清空该值，仍须逐条核对。适用主体在“改一改”中选择，以采纳时确认的对象为准。
3. 点“全部采纳高把握”。查看“还需补充”中的逐项原因。核实数据项的单位和记录粒度后填写；错误归入技能的制度条款可改为规则，确属技能的去技能工作台完善。已采纳的草稿也可点“不要”。确认提交框中的可送审数与留存数，再提交。
4. 独立审核人员在审核中心打开该规则，核对范围、条件、例外、生效日期、适用主体及各项来源。发布确认框应再次显示生效日期。回到已发布要素，点“就此提问”，询问本条规定的办理时限和申请材料。
5. 生成报告，点击出处上标核对引文和高亮。下载 Word 和离线网页，确认第四条的“10个工作日”及单位均保留。

分别上传 `regulation-repeal.txt` 和 `regulation-history.txt`。两份的日期初始均应为空。前者的 2019-03-01 属于废止的旧办法，后者的 2018-05-01 属于预算管理历史。浏览器演练将 **2026-11-18** 作为另行给定的模拟发布或印发日，在顶部填写一次。核对日期来源为“核对人填写”。差旅报销第三条的时限应为 **15个工作日**。 [原文与预期](r4a1-materials/README.md)

## 从旧页修订

用原资产页修订 0.7.0 数据项或知识、从原审批规则页修订授权规则。受拦时点“去补齐”，定位五项检查中缺失的字段。手工内容缺可定位原件时，建设人员可为该项写明原因后送审。审核人员必须逐项填写接受理由或退回理由。本轮全部编辑人和提交人均由其他审核人员办理。发布后核对原编号、版本、日期以及动态中的申请与办理记录。

## 自动复现

在有建库权限的一次性 MySQL 8.4、Node 22 和 Chromium 环境运行。每次使用新的输出目录，真实模型密钥由运行环境提供给 `DEEPSEEK_API_KEY`。

```bash
# 合成模型。包含原 R3、R4 动作、普通制度、旧页修订与导出
.venv/bin/python e2e/run_r4.py --output work/r4a1-demo/actions

# 真实模型。首次贯通一次、三份原文分开记录
.venv/bin/python e2e/run_r4.py --live --repetitions 1 --material regulation-plain --output work/r4a1-demo/early
.venv/bin/python e2e/run_r4.py --live --repetitions 1 --material regulation-repeal --output work/r4a1-demo/repeal
.venv/bin/python e2e/run_r4.py --live --repetitions 1 --material regulation-history --output work/r4a1-demo/history
.venv/bin/python e2e/run_r4.py --live --repetitions 3 --material regulation-plain --output work/r4a1-demo/final
```

`runner.json` 记录代码提交、模型与接口地址、实际提炼结果、人工操作、送审数、逐项发布检查、报告校验及资源清理。连续三次使用三个全新数据库，第一遍失败即停止。AA2 的 200 条为 24 条原件与 176 条合成副本，AB16 另查原文片段在页面中的起止位置。 [交付报告](R4-REPORT.md)
