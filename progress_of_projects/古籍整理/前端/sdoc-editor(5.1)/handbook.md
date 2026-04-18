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
       JSON_EXTRACT(snapshot_json, '$.project') AS project_meta,
       JSON_EXTRACT(snapshot_json, '$.annotations') AS annotations,
       JSON_EXTRACT(snapshot_json, '$.pages') AS pages_field,
       JSON_EXTRACT(snapshot_json, '$.customChars') AS customchars_field
FROM project_snapshots
ORDER BY snapshot_id DESC
LIMIT 5;



四、关闭mysql，退出mysql操作命令行

1.关闭mysql：net stop MySQL80

2.退出mysql：exit

