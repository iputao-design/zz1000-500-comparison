# 中证1000、中证500与上证指数历史对比

这个仓库用于发布静态版指数对比页面。

- 公网页面入口：`index.html`
- 本地源页面：`中证1000_中证500_上证指数_十年滑动对比图.html`
- 数据起点：`2014-10-17`

更新流程：

```sh
python3 build_index_comparison.py
```

脚本会刷新 CSV 和 HTML。如果当前目录已经配置 GitHub remote `origin`，脚本会自动提交并推送本次更新到 `main` 分支，GitHub Pages 随后会发布最新页面。
