USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

SCREEN = {
    "colorDepth": 24,
    "pixelDepth": 24,
    "height": 1080,
    "width": 1920,
    "availHeight": 1040,
    "availWidth": 1920,
}

VIEWPORT = {"width": 1324, "height": 842}

TEALEAF_APP_KEY = "76938917d7504ff7a962174c021690bd"
HCAPTCHA_SITEKEY = "884d15d9-b649-4bbb-8d1c-2d6f0eed75eb"

# Proxy pool format: host:port:username:password
# Prefer env injection:
#   PAYPAL_PROXY_ENABLED=1
#   PAYPAL_PROXY_URL=http://user:pass@host:port
#   PAYPAL_PROXY_POOL=host:port:user:pass,host2:port:user:pass
PROXY_ENABLED = False
PROXY_POOL: list[str] = []
