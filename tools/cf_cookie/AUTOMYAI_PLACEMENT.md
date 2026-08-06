# CF Cookie tool placement

- 代码: /opt/automyai/tools/cf_cookie
- 数据: /opt/automyai/data/cf_cookie
- 代理: /opt/automyai/data/cf_cookie/proxies/proxies.txt

示例:
```bash
cd /opt/automyai/tools/cf_cookie
node cf.auto-attack.js https://target.example 120 16 1000 \
  --proxy=/opt/automyai/data/cf_cookie/proxies/proxies.txt --headless
```
