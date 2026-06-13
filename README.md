# Easy Share

一个适合 Ubuntu 24.04 LTS 的轻量私有文件分享服务器，提供浏览器登录、上传、下载与删除功能。

## 安全设计

- 全站密码认证，使用恒定时间比较避免侧信道泄漏。
- 所有写操作均验证 CSRF token；Cookie 默认启用 `Secure`、`HttpOnly` 和 `SameSite=Strict`。
- 上传文件使用随机内部文件名，原始名称经过净化，不会被当作磁盘路径使用。
- 文件不由 Nginx 直接公开，只能经过已认证的应用下载。
- 默认限制单文件为 1 GiB，并设置 CSP、禁止 iframe、MIME 嗅探等响应头。
- systemd 服务使用独立低权限用户，并启用文件系统和临时目录隔离。
- Nginx 负责 HTTPS；建议只开放 22、80、443 端口，并使用强随机密码。

> 这是小型私人分享服务，不包含多用户权限、病毒扫描和断点续传。不要把它当作公开网盘。若允许不可信用户上传，请额外接入 ClamAV、速率限制和独立存储配额。

## Ubuntu 24.04 安装

以下命令假设项目已放在 `/opt/easyshare`，域名为 `files.example.com`：

```bash
sudo apt update
sudo apt install -y python3-venv nginx certbot python3-certbot-nginx
sudo useradd --system --home /var/lib/easyshare --shell /usr/sbin/nologin easyshare
sudo mkdir -p /opt/easyshare /var/lib/easyshare/uploads /etc/easyshare
sudo chown -R easyshare:easyshare /var/lib/easyshare
sudo chmod 750 /var/lib/easyshare /var/lib/easyshare/uploads /etc/easyshare

cd /opt/easyshare
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

生成配置（请替换密码；密钥命令会自动生成随机值）：

```bash
sudo tee /etc/easyshare/easyshare.env >/dev/null <<EOF_ENV
EASYSHARE_SECRET_KEY=$(openssl rand -hex 32)
EASYSHARE_PASSWORD=请替换为至少20位的随机密码
EASYSHARE_DATA_DIR=/var/lib/easyshare
EASYSHARE_MAX_UPLOAD_MB=1024
EASYSHARE_COOKIE_SECURE=1
EOF_ENV
sudo chown root:easyshare /etc/easyshare/easyshare.env
sudo chmod 640 /etc/easyshare/easyshare.env
```

安装并启动服务：

```bash
sudo cp deploy/systemd/easyshare.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now easyshare
sudo systemctl status easyshare
```

先申请证书，再安装 Nginx 配置：

```bash
sudo certbot certonly --nginx -d files.example.com
sudo cp deploy/nginx/easyshare.conf /etc/nginx/sites-available/easyshare
sudo sed -i 's/files\.example\.com/你的真实域名/g' /etc/nginx/sites-available/easyshare
sudo ln -s /etc/nginx/sites-available/easyshare /etc/nginx/sites-enabled/easyshare
sudo nginx -t && sudo systemctl reload nginx
```

如果先安装配置再申请证书，Nginx 会因证书文件尚不存在而无法通过配置检查。也可先使用 Certbot 自动生成站点配置，再合并本项目的反向代理设置。

### 防火墙

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

访问 `https://你的域名` 即可使用。应用只监听 `127.0.0.1:8000`，不要将 Gunicorn 端口暴露到公网。

## 更新与备份

```bash
cd /opt/easyshare
sudo -u easyshare .venv/bin/pip install -r requirements.txt
sudo systemctl restart easyshare
sudo tar -czf easyshare-backup-$(date +%F).tar.gz /var/lib/easyshare
```

SQLite 数据库与上传文件都位于 `/var/lib/easyshare`。备份时建议短暂停止服务，以获得一致快照。

## 本地开发与测试

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export EASYSHARE_SECRET_KEY=dev-only-secret
export EASYSHARE_PASSWORD=dev-password
export EASYSHARE_DATA_DIR="$PWD/data"
export EASYSHARE_COOKIE_SECURE=0
.venv/bin/flask --app easyshare.app run
```

运行测试：

```bash
.venv/bin/pytest
```
