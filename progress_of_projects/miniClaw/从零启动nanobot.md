# 一、初始化步骤

## 0) 进入源码目录（这里是 pyproject.toml 所在位置）

cd E:\githubrepository\bot\nanobot

## 1) 确认 Python 版本（要求 >= 3.11）
python --version

## 2) 创建虚拟环境
python -m venv .venv

## 3) 激活虚拟环境（PowerShell）
## 如果提示脚本执行被禁用，先执行这一行再激活：
## Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.venv\Scripts\Activate.ps1

## 4) 安装依赖（开发依赖建议也装上，方便跑起来/测试）
python -m pip install -U pip
pip install -e .
pip install -e ".[dev]"

## 5) 初始化配置 + workspace（会生成 ~/.nanobot/config.json）
nanobot onboard
## 如需交互式向导：
## nanobot onboard --wizard

## 6) 修改配置文件（按你选择的模型/提供方填 API key）
## 配置文件路径通常是：%USERPROFILE%\.nanobot\config.json
notepad $env:USERPROFILE\.nanobot\config.json

## 7) 启动本地 CLI 聊天（最适合先跑通）
nanobot agent

## 或者一条命令直接发消息（无交互）
nanobot agent -m "Hello! 请用 nanobot 完成一次小任务。"

## 8) 如果你想启动网关（接入 Telegram/Discord/等渠道）
nanobot gateway



# 二、接入千问api方法

## 1) 在 `providers.dashscope` 填入 API Key

你当前 `C:\Users\25283\.nanobot\config.json` 中，`providers` 下有这个块：

- `providers.dashscope.apiKey` 目前是 `null`

把它改成你的百炼/通义 DashScope API Key 字符串即可（`apiBase` 一般可保持 `null`，因为 registry 里已有默认兼容地址）：

"providers": {

  "dashscope": {

​    "apiKey": "你的_百炼_DASHSCOPE_API_KEY",

​    "apiBase": null,

​    "extraHeaders": null

  }

}

## 2) 把默认模型切到 DashScope 的 Qwen

你现在的默认模型是：

- `agents.defaults.model`: `"anthropic/claude-opus-4-5"`（所以 nanobot 会选 Anthropic）

要改成 Qwen（DashScope）。建议你用显式 provider 前缀让路由最稳，比如：

- `agents.defaults.model`: `"dashscope/qwen-max"`（或你在百炼控制台实际使用的 Qwen 模型名）

示例（只展示需要改的字段）：

"agents": {

  "defaults": {

​    "model": "dashscope/qwen-max",

​    "provider": "auto"

  }

}

（`provider: "auto"` 可以保留；关键是 `model` 要能匹配到 `dashscope`。）

## 3) 启动测试

改完保存后，直接跑 CLI：

nanobot agent -m "你好，用阿里百炼回答一个问题"

如果还在走 Anthropic，大概率是你 `agents.defaults.model` 没改到 Qwen/dashscope 前缀；把 `model` 换成 `dashscope/xxxx` 再试一次。

------

如果你把你准备用的百炼模型名（例如控制台里显示的那个，比如 `qwen-max` / `qwen-plus` / `qwen-turbo` 等）发我，我可以帮你把 `agents.defaults.model` 那一行给到最准确的写法。

# 三、与微信通讯

按 README 里 WeChat（微信 / Weixin） 这一段来接就行（用的是 ilinkai 的“个人微信”接口，走 HTTP long-poll + 扫码登录，不需要你本地装/登录微信桌面客户端）。

## 1) 安装微信支持依赖

在你的环境里执行：

pip install "nanobot-ai[weixin]"

## 2) 在 `C:\Users\25283\.nanobot\config.json` 开启 weixin 频道

把 `channels.weixin` 设为启用，并至少配置 `allowFrom`：

最小示例（你可以直接对照你现有配置改）：
```json
{
  "channels": {
  "weixin": {
  "enabled": true,
  "allowFrom": ["YOUR_WECHAT_USER_ID"]
    }
  }
}
```
关键点：

- `allowFrom` 不是微信昵称，而是你的微信账号的 sender id（一般在 nanobot 日志里能看到）。
- 想先不做限制就用：`"allowFrom": ["*"]`。

`token`、`routeTag`、`stateDir`、`pollTimeout` 都是可选项：

- `token` 可不填：不填的话你后面登录会走交互式扫码，并把 token 保存起来。
- 如果你有上游路由需求才需要 `routeTag`。

## 3) 扫码登录（生成/更新 token）

执行：

nanobot channels login weixin

如果 token 已过期/你要重新登录：

nanobot channels login weixin --force

## 4) 启动网关（开始收发消息）

nanobot gateway

此时你的 agent/模型能力仍由你 `providers` 和 `agents.defaults.model` 决定；微信只是“消息通道”。

## 5) 关闭网关（停止收发消息）
在终端按 CTRL+C
