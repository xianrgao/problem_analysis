---
name: rewrite
description: 根据变量池中原表和参考表的预览编写 Python，将绑定、批次或补充字段按已验证关联键写回表格，并保存同名新版本及最新预览。
metadata:
  version: '1.0.0'
  title: 数据关联与重写
---

# 数据关联与重写

输入：待重写变量引用、参考变量引用、关联规则、需要添加／更新的列。先查看两表 preview 和数据描述，读取全量数据仅发生在执行代码中。

## 工作规则

- 先确认原表粒度、稳定行 ID 和参考表的唯一键。首次绑定通常为 `(tg, component, position)`，而不是单独 `tg`。同一个 TG 的多个 NC 行必须保留。
- 以左关联为默认补充方式；不自动删行、不用内连接隐藏未匹配数据、不用 `drop_duplicates` 任意解决冲突。
- 多个零件／位置先按业务规则取首次有效绑定，检测并报告同一时间冲突，再转换为一行一 TG 的宽表或逐零件关联。
- 使用 pandas `validate="many_to_one"` 等基数约束，校验原表行数与行 ID 集合保持一致；输出未匹配率、新增列及冲突数。
- 同名写入为新版本，由宿主触发 `{name}_preview`。失败时不发布部分重写结果。

## 全量变量代码样例

```python
source_name = "实际NC变量名称"
reference_name = "实际首次绑定变量名称"
source = ctx.vars.load(source_name).copy()
reference = ctx.vars.load(reference_name).copy()

# 此例要求 reference 已经是一行一 TG，仅含所分析的一个零件和位置。
if reference["tg"].isna().any() or reference["tg"].duplicated().any():
    raise ValueError("绑定表的 TG 必须非空且唯一；先完成零件/位置筛选及冲突处理")
if any(c in source for c in ("batch", "supplier")):
    raise ValueError("目标列已存在；必须明确覆盖规则或更换列名")
output = source.merge(reference[["tg", "batch", "supplier"]], on="tg", how="left", validate="many_to_one")
assert len(output) == len(source), "关联导致原表行数变化"
ctx.vars.save(source_name, output, description="在原 NC 明细中补充指定零件首次绑定的批次及供应商；未匹配记录保留为空。")
```

具体列名必须来自当前数据，禁止照抄示例猜字段。通用确定性实现见 [scripts/safe_enrich.py](scripts/safe_enrich.py)，复杂绑定情形见 [references/first-binding.md](references/first-binding.md)。
