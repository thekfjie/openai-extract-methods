const fs = require('fs');

// 读取 unified-pairs.json
const data = JSON.parse(fs.readFileSync('unified-pairs.json', 'utf8'));

// 提取所有代理
const proxies = data
    .map(item => item.proxy)
    .filter(proxy => proxy && proxy !== 'direct'); // 过滤掉 'direct' 和空值

// 保存到 proxies.txt
fs.writeFileSync('proxies.txt', proxies.join('\n'), 'utf8');

console.log(`✓ 已提取 ${proxies.length} 个代理到 proxies.txt`);
