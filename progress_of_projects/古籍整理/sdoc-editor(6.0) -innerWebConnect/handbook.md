一、启动mysql

1.win+R-->ctrl+shift+enter,输入net start MySQL80

2.管理员权限下使用命令提示行，输入mysql -u root -p

3.输入root用户的密码

二、启动JSON

在backend文件夹用命令行执行npm start

三、在命令行进行mysql查询

SELECT project_id, title, author, dynasty, book, volume, updated_at
FROM projects
ORDER BY updated_at DESC
LIMIT 5;



SELECT snapshot_id, project_id,
       JSON_EXTRACT(snapshot_json, '\$.project') AS project_meta,
       JSON_EXTRACT(snapshot_json, '\$.annotations') AS annotations,
       JSON_EXTRACT(snapshot_json, '​\$.pages') AS pages_field,
       JSON_EXTRACT(snapshot_json, '$.customChars') AS customchars_field
FROM project_snapshots
ORDER BY snapshot_id DESC
LIMIT 5;



四、关闭mysql，退出mysql操作命令行

1.关闭mysql：net stop MySQL80

2.退出mysql：exit



五、

是的，报错了。  
你截图里的核心错误是：

- `Error: listen EADDRINUSE: address already in use :::8000`

意思是：**8000 端口已经被别的进程占用了**，所以这次 `npm run dev` 启动失败。

---

解决步骤（Windows PowerShell）：

1. 查占用 8000 的进程 PID
```powershell
netstat -ano | findstr :8000
```
你会看到类似 `LISTENING    34532`，最后一列就是 PID。

2. 结束该 PID
```powershell
taskkill /PID 34532 /F
```
把 `34532` 换成你查到的 PID。

3. 再启动后端
```powershell
npm run dev
```

---

补充判断：

- 如果你本来就已经有一个后端实例在跑（之前开的终端没关），其实**不用再开第二个**，直接用已有实例就行。
- 只有看到这句才算成功启动：
  - `Node backend listening on http://localhost:8000`

如果你愿意，我可以再给你一套“避免重复启动后端”的日常操作习惯（很短，3条）。



六、前端静态启动

```
python -m http.server 8080 --bind 0.0.0.0
```

