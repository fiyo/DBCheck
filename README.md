# 数据库巡检工具 - DBCheck（MySQL、PostgreSQL）

> 本项目是在 [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git)  基础上增加了对 PostgreSQL 的支持。
> 主要功能为 MySQL 和 PostgreSQL 数据库巡检并自动生成 Word 格式巡检报告。
> 详细项目介绍及使用方法见公众号 [小周的数据库进阶之路](https://mp.weixin.qq.com/s/rogoiuYtpU07O55_VqBK8w?scene=1&click_id=8)

## 安装依赖
```bash
# 安装Python3pip
yum install -y python3-pip

# 安装Python依赖库
pip3 install pyinstaller
pip3 install pymysql paramiko openpyxl docxtpl python-docx pandas psutil==5.9.0 psycopg2-binary
```

## 执行脚本
```bash
python3 main.py
```


## 打包命令
```bash
pyinstaller --onefile --name mysql_inspector \
    --hidden-import pymysql \
    --hidden-import docx \
    --hidden-import docxtpl \
    --hidden-import paramiko \
    --hidden-import psutil \
    --hidden-import openpyxl \
    main.py
```
## 打包后执行
在dist目录下
```bash
[root@localhost dist]# ./mysql_inspector
```
<img width="572" height="359" alt="image" src="https://github.com/fiyo/DBCheck/blob/main/snapshot/file1.png" />

## 鸣谢
本项目是在 [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git) 基础上改进而来，在原 MySQL 数据库巡检功能的基础上增加了 **PostgreSQL** 数据库支持。

感谢 [Zhh9126/MySQLDBCHECK](https://github.com/Zhh9126/MySQLDBCHECK.git) 作者的付出！

原项目作者公众号文章：[小周的数据库进阶之路](https://mp.weixin.qq.com/s/rogoiuYtpU07O55_VqBK8w?scene=1&click_id=8)

目前部分功能正在持续完善中，欢迎参与共同完善反馈问题或提出建议！