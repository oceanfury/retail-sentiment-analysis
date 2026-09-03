# 雪球 Cookie 配置说明

雪球的数据采集需要登录态（Cookie），请按以下步骤配置：

## 获取 Cookie

1. 用 Chrome/Edge 浏览器打开 https://xueqiu.com
2. 登录你的雪球账号（手机验证码登录即可）
3. 登录成功后，按 F12 打开开发者工具
4. 切换到 **Network（网络）** 标签
5. 刷新页面，找到第一个 `xueqiu.com` 的请求
6. 在请求头中找到 **Cookie** 字段，复制其中两个值：
   - `xq_a_token` 的值（一串很长的字符串）
   - `u` 的值（一串数字）

或者更简单的方法：
1. 打开开发者工具 → Application（应用）标签
2. 左侧找到 Cookies → https://xueqiu.com
3. 在列表中找到 `xq_a_token` 和 `u`，复制它们的值

## 配置方式

### 方式一：环境变量（推荐）

在 PowerShell 中设置：

```powershell
$env:XUEQIU_XQ_A_TOKEN = "你的xq_a_token值"
$env:XUEQIU_U_TOKEN = "你的u值"
```

然后运行主程序：
```powershell
python main.py
```

### 方式二：修改配置文件

编辑 `config/settings.py`，直接填入：

```python
XUEQIU_COOKIE = {
    "xq_a_token": "你的xq_a_token值",
    "u": "你的u值",
}
```

## 注意事项

- Token 有效期通常为几天到几周，失效后需要重新获取
- 如果雪球返回 401 或空数据，说明 Token 过期了，重新获取即可
- **请勿将 Cookie 提交到公开仓库**
