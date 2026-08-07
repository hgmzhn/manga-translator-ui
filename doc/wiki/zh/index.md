---
title: 欢迎来到 Manga Translator Wiki
description: "本软件的使用与开发文档站：桌面端、Web、命令行、Docker 与开发者接口的安装、使用和开发指南"
pageId: index
lang: zh-CN
outline: [2, 4]
lastUpdated: true
---

<div align="center">

![Manga Translator Wiki](/home.png){width=300}

</div>

# 欢迎来到 Manga Translator Wiki

这是本软件的使用与开发文档站。内容以当前仓库源码、桌面 i18n 和服务端公开代码为依据，覆盖桌面端、Web、命令行、Docker 与开发者接口。先从运行形态进入对应的操作页，未核对的能力不会写成宣传语。

## 快速入口 {#quick-start}

项目共用 MangaTranslator 处理链，但提供不同的交互边界：

| 形态 | 适合场景 | 入口 |
| --- | --- | --- |
| Qt 桌面应用 | 本机选图、调整参数、查看进度并在可视化编辑器中修订 | [产品形态](./introduction/product-forms.md) · [首次翻译](./introduction/first-translation.md) |
| 命令行 CLI | 无桌面环境、脚本化和批量处理 | [命令结构](./cli/command-structure.md) · [本地输入与输出](./cli/local-input-output.md) |
| Web 界面 | 浏览器上传、配置任务、查看结果和历史 | [启动与访问](./web/launch-and-access.md) · [上传、配置与翻译](./web/upload-config-and-translate.md) |
| Docker | 以容器方式运行 Web 形态，并用卷保存资源和服务器数据 | [Docker](./install/docker.md) |
| 内部服务 | 已有本地集成所需的内部协议（`ws` / `shared`） | [Web、WS 与 shared 模式](./cli/web-ws-and-shared-modes.md) · [内部协议](./developer/internal-shared-and-websocket.md) |

从哪里开始：

- 第一次使用：从[首次翻译](./introduction/first-translation.md)走完一次完整的翻译流程，或用[产品形态](./introduction/product-forms.md)了解各运行方式的区别。
- 想要安装：从[Windows 便携版](./install/windows-portable.md)、[Linux/macOS 安装](./install/linux-and-macos.md)或[Docker 部署](./install/docker.md)按平台挑选入口。
- 想要开发或接入：见[参与贡献](#contributing)和[相关项目与链接](./developer/related-projects-and-links.md)，那里汇总了源码、协议和周边项目。

## 参与贡献 {#contributing}

欢迎通过 Pull Request 修正 wiki 内容——错别字、过期信息、缺失页面——也欢迎调整文档结构；有疑问或建议时，也可以直接提 Issue。贡献流程见[参与贡献指南](./community/contributing.md)，相关项目与链接见[相关项目与链接](./developer/related-projects-and-links.md)。