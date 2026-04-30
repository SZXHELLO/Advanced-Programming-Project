minibot-web/
├── frontend/                          # React 前端
│   ├── public/                        # 静态资源
│   ├── src/
│   │   ├── components/               # 组件目录
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── SessionItem.tsx
│   │   │   ├── ModelConfigModal.tsx
│   │   │   └── SubAgentCard.tsx
│   │   ├── pages/                    # 页面目录
│   │   │   ├── Dashboard.tsx
│   │   │   └── AgentsBoard.tsx
│   │   ├── services/                 # API & WebSocket
│   │   │   ├── websocket.ts
│   │   │   └── api.ts
│   │   ├── types/                    # TypeScript 类型
│   │   │   └── index.ts
│   │   ├── App.tsx                   # 主应用组件
│   │   ├── main.tsx                  # 入口文件
│   │   └── index.css                 # 全局样式
│   ├── index.html                    # HTML 模板
│   ├── package.json                  # 前端依赖
│   ├── vite.config.ts               # Vite 配置
│   ├── tsconfig.json                # TypeScript 配置
│   ├── tailwind.config.js           # ⭐ TailwindCSS 配置
│   └── postcss.config.js            # ⭐ PostCSS 配置
│
├── backend/                          # Node.js BFF
│   ├── src/
│   │   └── server.ts                # BFF 服务器
│   ├── package.json                 # 后端依赖
│   └── tsconfig.json               # TypeScript 配置
│
└── README.md                        # 项目文档