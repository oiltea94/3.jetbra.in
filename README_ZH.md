[English](README.md) | 简体中文

# 3.jetbra.in

[3.jetbra.in](https://3.jetbra.in/) 的静态镜像，包含页面资源和 ZIP 下载包，通过 GitHub Actions 自动同步并部署到 GitHub Pages。

## 本地使用

需要 Python 3.10+。

```sh
python -m pip install -r requirements-dev.txt
python scripts/mirror.py
python scripts/mirror.py --verify-only
```

打开 `public/index.html` 即可预览。运行测试：`python -m pytest -q`。

## 部署

1. 将项目推送到仓库的 `main` 分支。
2. 在 **Settings > Pages > Source** 中选择 **GitHub Actions**。
3. 运行 **Actions > Sync and deploy mirror > Run workflow**。

无需额外配置 Secrets。仓库策略须允许 Actions 提交到 `main` 并部署到 Pages。

每天 **UTC 00:00 / 北京时间 08:00** 自动同步，也支持代码推送到 `main` 或手动触发。下载失败时保留旧快照。
