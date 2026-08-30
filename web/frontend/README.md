# Coding Agent Workspace 前端（Vite + Vue 3）

## 构建

```bash
cd web/frontend
npm install
npm run build
```

构建产物输出到 `web/frontend/dist/`，后端（`web/server.py`）会自动托管它：
`GET /` 返回 `dist/index.html`，`/assets/*` 返回 `dist/assets/*`。

开发模式（热更新，`/api` 自动代理到 `http://127.0.0.1:8001`）：

```bash
npm run dev
```

## 启动后端

```bash
python -m uvicorn web.server:app --port 8001
```

访问 `http://127.0.0.1:8001`。若 `dist/` 未构建，会显示构建提示页。

## 说明

- 左侧栏：选择工作目录 + 该目录下的会话列表（每个目录可多个会话，可新建/删除）。
- 「添加工作区」打开一个**服务端目录浏览器**（`GET /api/fs/list`），可逐级浏览服务器文件系统并选择文件夹作为工作区。
  - 为什么不用浏览器原生的文件夹选择弹窗：浏览器的 File System Access API 只能拿到一个不透明的 `handle`，**无法把绝对路径交给后端**。而 agent 需要在服务器侧读写真实文件，所以正确做法是让用户在**服务器侧目录浏览器**里选一个真实可用的目录。
- 右侧「对话 / 轨迹」双视图；对话里每条消息中间步骤用可折叠「处理过程」展示，轨迹里是分段 timeline + 可折叠步骤卡片。
- 会话持久化到 `.coding-agent/web-sessions/`，刷新/重启不丢历史。
