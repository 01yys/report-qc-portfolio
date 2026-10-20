// 所有样例均为本项目合成文本，不来源于患者报告。
export const samples = [
  { id: "concept", title: "肺部结节（含左右、否定词不一致）", findings: "左肺上叶见 8 mm 结节。\n右肺下叶未见炎性改变。", impression: "右肺上叶结节，约 8 mm。\n右肺下叶炎性改变。" },
  { id: "equivalent", title: "正常报告（尺寸单位等值）", findings: "左肺上叶见 8 mm 结节。", impression: "左肺上叶见 0.8 cm 结节。" },
  { id: "size", title: "尺寸不一致（8 mm 与 18 mm）", findings: "左肺上叶见 8 mm 结节。", impression: "左肺上叶见 18 mm 结节。" },
  { id: "chinese-size", title: "中文尺寸不一致（8 毫米与 18 毫米）", findings: "左肺上叶见一枚结节，直径8毫米。", impression: "左肺上叶单发结节，直径18毫米。" },
  { id: "chinese-equivalent", title: "中文单位等值（8 毫米与 0.8 厘米）", findings: "左肺上叶见一枚结节，直径8毫米。", impression: "左肺上叶单发结节，直径0.8厘米。" },
  { id: "post-absence", title: "肯否不一致（这枚结节不存在）", findings: "左肺上叶可见一枚结节。", impression: "左肺上叶这枚结节不存在。" },
  { id: "negative", title: "正常报告（两段均明确否定）", findings: "右肺下叶未见炎性改变。", impression: "右肺下叶无炎性改变。" },
  { id: "uncertain", title: "不确定表达（不强行判断）", findings: "右肺上叶不能排除结节。", impression: "右肺上叶考虑结节。" },
  { id: "multi", title: "多病灶（无可靠配对）", findings: "左肺上叶见 8 mm 结节。左肺上叶见 10 mm 结节。", impression: "左肺上叶见 18 mm 结节。" },
  { id: "normal", title: "正常报告（两段一致）", findings: "左肺上叶见结节。右肺下叶见炎性改变。", impression: "左肺上叶结节。右肺下叶炎性改变。" },
];
export const moduleLabels = { laterality: "侧别与部位", measurement: "尺寸与单位", assertion: "肯定与否定" };
